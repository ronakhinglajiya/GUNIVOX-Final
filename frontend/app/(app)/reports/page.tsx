"use client";

/**
 * Reports — one-click downloads with date + temperature filters:
 *   • Calls CSV (rows: phone, billable mins, outcome, full transcript)
 *   • Leads CSV (rows: profile + total billable mins per lead)
 *   • Summary PDF (header, stats tiles, outcome breakdown, call table)
 *
 * Filters:
 *   - Date: pick a civil day in IST. Default = today. "All time" → no filter.
 *   - Temperature: all / hot / warm / cold. Filters on Call.lead_temperature.
 *
 * Both filters are sent as query params; backend handles the IST→UTC window.
 * Filenames carry the active filters (e.g. gunivox-calls-2026-06-04-cold.csv).
 */
import { useMemo, useRef, useState } from "react";
import {
  FileSpreadsheet, FileText, Download, Loader2, Calendar,
  Flame, ThermometerSun, Snowflake, Sparkles, Upload, UserCheck,
  PhoneCall, PhoneOff, PhoneMissed, MicOff,
} from "lucide-react";
import { useToast } from "@/lib/toast-context";
import { getToken } from "@/lib/api";
import { RequireAdmin } from "@/components/require-admin";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Kind = "calls.csv" | "leads.csv" | "summary.pdf" | "leads_transcripts.pdf";
type Temp = "all" | "hot" | "warm" | "cold";
// Outcome maps DIRECTLY to a single Call.outcome value from the database
// — no synthesized buckets. "completed" is the only outcome that has a
// transcript; no_answer / busy / no_speech mean the call never had a
// transcript-producing exchange.
type Outcome = "all" | "completed" | "no_answer" | "busy" | "no_speech";

const REPORTS: Array<{
  kind: Kind;
  title: string;
  desc: string;
  icon: React.ReactNode;
  accent: string;
}> = [
  {
    kind: "calls.csv",
    title: "Calls report (CSV)",
    desc: "Every call in the selected range: phone, duration, billable minutes, outcome, transcript.",
    icon: <FileSpreadsheet className="h-5 w-5" />,
    accent: "text-emerald-400",
  },
  {
    kind: "leads.csv",
    title: "Leads report (CSV)",
    desc: "Profile + activity. Filters apply to the lead's calls — included if any call matches.",
    icon: <FileSpreadsheet className="h-5 w-5" />,
    accent: "text-sky-400",
  },
  {
    kind: "summary.pdf",
    title: "Summary report (PDF)",
    desc: "Visual PDF — header notes the active filters, stats tiles, outcome breakdown, per-call list.",
    icon: <FileText className="h-5 w-5" />,
    accent: "text-rose-400",
  },
  {
    kind: "leads_transcripts.pdf",
    title: "Leads with transcripts (PDF)",
    desc: "Readable PDF — one section per LEAD that was picked up. Profile + every call's transcript with Caller / Agent on separate lines.",
    icon: <FileText className="h-5 w-5" />,
    accent: "text-violet-400",
  },
];

const TEMP_BUTTONS: Array<{ key: Temp; label: string; icon: React.ReactNode; accent: string }> = [
  { key: "all",  label: "All",  icon: <Sparkles className="h-3.5 w-3.5" />,        accent: "" },
  { key: "hot",  label: "Hot",  icon: <Flame className="h-3.5 w-3.5" />,           accent: "rose" },
  { key: "warm", label: "Warm", icon: <ThermometerSun className="h-3.5 w-3.5" />,  accent: "amber" },
  { key: "cold", label: "Cold", icon: <Snowflake className="h-3.5 w-3.5" />,       accent: "sky" },
];

// One button per actual Call.outcome value in the database. Only
// "completed" has transcripts; the rest will surface as "No transcript —
// caller did not respond" in the Transcripts PDF.
const OUTCOME_BUTTONS: Array<{ key: Outcome; label: string; icon: React.ReactNode; accent: string; hasTranscript: boolean }> = [
  { key: "all",       label: "All",       icon: <Sparkles className="h-3.5 w-3.5" />,     accent: "",        hasTranscript: true  },
  { key: "completed", label: "Completed", icon: <PhoneCall className="h-3.5 w-3.5" />,    accent: "emerald", hasTranscript: true  },
  { key: "no_answer", label: "No answer", icon: <PhoneMissed className="h-3.5 w-3.5" />,  accent: "zinc",    hasTranscript: false },
  { key: "busy",      label: "Busy",      icon: <PhoneOff className="h-3.5 w-3.5" />,     accent: "amber",   hasTranscript: false },
  { key: "no_speech", label: "No speech", icon: <MicOff className="h-3.5 w-3.5" />,       accent: "violet",  hasTranscript: false },
];

function todayISTISO(): string {
  // YYYY-MM-DD in IST — used as the date input default.
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  });
  return fmt.format(new Date()); // "YYYY-MM-DD"
}

interface BackfillResult {
  leads_updated: number;
  calls_updated: number;
  total_skipped: number;
  skipped_sample: string[];
}

export default function ReportsPage() {
  const [busy, setBusy] = useState<Kind | null>(null);
  // Date filter modes: a single IST day, a [from..to] range, or all-time.
  const [dateMode, setDateMode] = useState<"single" | "range" | "all">("single");
  const [date, setDate] = useState<string>(todayISTISO());       // single-day
  const [dateFrom, setDateFrom] = useState<string>(todayISTISO()); // range start
  const [dateTo, setDateTo] = useState<string>(todayISTISO());     // range end
  const [temp, setTemp] = useState<Temp>("all");
  const [outcome, setOutcome] = useState<Outcome>("all");
  // Backfill state — uploading the yesterday-CSV to retro-fix lead names.
  const [backfilling, setBackfilling] = useState(false);
  const [backfillResult, setBackfillResult] = useState<BackfillResult | null>(null);
  const backfillInputRef = useRef<HTMLInputElement | null>(null);
  const toast = useToast();

  const todayPretty = useMemo(
    () =>
      new Intl.DateTimeFormat("en-IN", {
        weekday: "long", year: "numeric", month: "long", day: "numeric",
        timeZone: "Asia/Kolkata",
      }).format(new Date()),
    [],
  );

  // range validity — from must be on/before to
  const rangeInvalid =
    dateMode === "range" && !!dateFrom && !!dateTo && dateFrom > dateTo;

  const handleBackfill = async (file: File) => {
    setBackfilling(true);
    setBackfillResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const token = getToken();
      const res = await fetch(`${API_BASE}/api/leads/backfill_csv_names`, {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        body: fd,
      });
      if (!res.ok) {
        throw new Error(`Server returned ${res.status} ${res.statusText}`);
      }
      const data = (await res.json()) as BackfillResult;
      setBackfillResult(data);
      toast.success(
        "Names backfilled",
        `${data.leads_updated} lead${data.leads_updated === 1 ? "" : "s"} and ` +
          `${data.calls_updated} call${data.calls_updated === 1 ? "" : "s"} updated.`,
      );
    } catch (e: unknown) {
      toast.error("Backfill failed", e instanceof Error ? e.message : String(e));
    } finally {
      setBackfilling(false);
      if (backfillInputRef.current) backfillInputRef.current.value = "";
    }
  };

  const download = async (kind: Kind) => {
    setBusy(kind);
    try {
      const params = new URLSearchParams();
      if (dateMode === "single" && date) {
        params.set("date", date);
      } else if (dateMode === "range") {
        if (dateFrom) params.set("date_from", dateFrom);
        if (dateTo) params.set("date_to", dateTo);
      } // "all" → send no date param
      if (temp !== "all") params.set("temperature", temp);
      if (outcome !== "all") params.set("outcome", outcome);
      const qs = params.toString() ? `?${params.toString()}` : "";

      const token = getToken();
      const res = await fetch(`${API_BASE}/api/reports/${kind}${qs}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!res.ok) {
        throw new Error(`Server returned ${res.status} ${res.statusText}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const cd = res.headers.get("Content-Disposition") || "";
      const fn = /filename="([^"]+)"/.exec(cd)?.[1] || `gunivox-${kind}`;
      a.download = fn;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success("Report downloaded", fn);
    } catch (e: unknown) {
      toast.error(
        "Download failed",
        e instanceof Error ? e.message : "Unknown error",
      );
    } finally {
      setBusy(null);
    }
  };

  const outcomePretty =
    outcome === "all"
      ? "All outcomes"
      : (OUTCOME_BUTTONS.find((b) => b.key === outcome)?.label ?? outcome);
  const dateSummary =
    dateMode === "all"
      ? "All-time"
      : dateMode === "range"
        ? `${dateFrom || "…"} → ${dateTo || "…"}`
        : date || "All-time";
  const filterSummary =
    `${dateSummary} · ${temp[0].toUpperCase() + temp.slice(1)}` +
    ` · ${outcomePretty}`;

  return (
    <RequireAdmin>
    <div className="space-y-5 max-w-4xl">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <Download className="h-6 w-6 text-brand" /> Reports
        </h1>
        <p className="text-sm text-ink-dim mt-1 flex items-center gap-1.5">
          <Calendar className="h-4 w-4 text-ink-faint" /> {todayPretty}
        </p>
        <p className="text-xs text-ink-faint mt-1">
          Pick a date and temperature, then click <strong>Generate</strong>. Files include the
          active filters in their names so daily reports stay sorted.
        </p>
      </div>

      {/* Filter bar */}
      <div className="card p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3">
          <label className="label flex items-center gap-1.5 shrink-0">
            <Calendar className="h-3.5 w-3.5" /> Date
          </label>
          {/* Mode toggle: single day / range / all-time */}
          {([
            { key: "single", label: "Single day" },
            { key: "range",  label: "Range" },
            { key: "all",    label: "All-time" },
          ] as const).map((m) => {
            const active = dateMode === m.key;
            return (
              <button
                key={m.key}
                type="button"
                onClick={() => setDateMode(m.key)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs transition-colors
                  ${active
                    ? "border-brand bg-brand/10 text-brand"
                    : "border-border text-ink-dim hover:text-ink hover:border-ink-faint/40"
                  }`}
              >
                {m.label}
              </button>
            );
          })}

          {dateMode === "single" && (
            <>
              <input
                type="date"
                value={date}
                onChange={(e) => setDate(e.target.value)}
                max={todayISTISO()}
                className="input max-w-[170px]"
              />
              <button
                type="button"
                onClick={() => setDate(todayISTISO())}
                className="btn-ghost text-xs"
              >
                Today
              </button>
            </>
          )}

          {dateMode === "range" && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-ink-faint">From</span>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                max={todayISTISO()}
                className="input max-w-[160px]"
              />
              <span className="text-xs text-ink-faint">To</span>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                max={todayISTISO()}
                className={`input max-w-[160px] ${rangeInvalid ? "border-rose-500/60" : ""}`}
              />
              <button
                type="button"
                onClick={() => {
                  const t = todayISTISO();
                  setDateFrom(t);
                  setDateTo(t);
                }}
                className="btn-ghost text-xs"
              >
                Today
              </button>
            </div>
          )}

          {dateMode === "all" && (
            <span className="text-xs text-ink-faint">No date filter — every call, all time.</span>
          )}
        </div>
        {rangeInvalid && (
          <p className="text-xs text-rose-400">
            “From” date must be on or before “To” date.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <label className="label shrink-0">Temperature</label>
          {TEMP_BUTTONS.map((b) => {
            const active = temp === b.key;
            const ringByAccent: Record<string, string> = {
              "":      active ? "border-brand bg-brand/10 text-brand" : "",
              rose:    active ? "border-rose-500/50 bg-rose-500/10 text-rose-300" : "",
              amber:   active ? "border-amber-500/50 bg-amber-500/10 text-amber-300" : "",
              sky:     active ? "border-sky-500/50 bg-sky-500/10 text-sky-300" : "",
            };
            return (
              <button
                key={b.key}
                type="button"
                onClick={() => setTemp(b.key)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs transition-colors
                  ${active
                    ? ringByAccent[b.accent]
                    : "border-border text-ink-dim hover:text-ink hover:border-ink-faint/40"
                  }`}
              >
                {b.icon} {b.label}
              </button>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="label shrink-0">Outcome</label>
          {OUTCOME_BUTTONS.map((b) => {
            const active = outcome === b.key;
            const ringByAccent: Record<string, string> = {
              "":         active ? "border-brand bg-brand/10 text-brand" : "",
              emerald:    active ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-300" : "",
              zinc:       active ? "border-zinc-400/50 bg-zinc-500/10 text-zinc-300" : "",
              amber:      active ? "border-amber-500/50 bg-amber-500/10 text-amber-300" : "",
              violet:     active ? "border-violet-500/50 bg-violet-500/10 text-violet-300" : "",
            };
            return (
              <button
                key={b.key}
                type="button"
                onClick={() => setOutcome(b.key)}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs transition-colors
                  ${active
                    ? ringByAccent[b.accent]
                    : "border-border text-ink-dim hover:text-ink hover:border-ink-faint/40"
                  }`}
                title={b.hasTranscript ? "Transcript available" : "No transcript — caller did not respond"}
              >
                {b.icon} {b.label}
              </button>
            );
          })}
        </div>

        <p className="text-xs text-ink-faint">
          Active filter: <span className="font-mono text-ink-dim">{filterSummary}</span>
          {(() => {
            const meta = OUTCOME_BUTTONS.find((b) => b.key === outcome);
            if (!meta || meta.hasTranscript) return null;
            return (
              <span className="text-amber-400/80 ml-2">
                · No transcript exists for <strong>{meta.label.toLowerCase()}</strong> calls
                — the Transcripts PDF will show duration + “No transcript — caller did not respond.”
              </span>
            );
          })()}
        </p>
      </div>

      {/* Backfill names from CSV — retro-fix existing leads/calls without re-dialing */}
      <div className="card p-4 border-violet-500/30 bg-violet-500/[0.04]">
        <div className="flex items-start gap-4">
          <div className="shrink-0 h-10 w-10 rounded-xl bg-violet-500/15 flex items-center justify-center text-violet-300">
            <UserCheck className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="text-sm font-semibold text-ink">Backfill names from CSV</h3>
            <p className="text-xs text-ink-dim mt-0.5">
              Upload the same CSV you uploaded yesterday (phone in <strong>col L</strong>,
              first name in <strong>col E</strong>, last name in <strong>col G</strong>).
              Every <em>existing</em> lead and every call already made will get the real
              caller name — no re-dialing, no duplicates. Reports run after this will show
              the correct names.
            </p>
            {backfillResult && (
              <div className="mt-3 text-xs text-ink-dim">
                <span className="text-emerald-400 font-mono">
                  ✓ {backfillResult.leads_updated} leads
                </span>{" "}
                +{" "}
                <span className="text-sky-400 font-mono">
                  {backfillResult.calls_updated} calls
                </span>{" "}
                updated.
                {backfillResult.total_skipped > 0 && (
                  <>
                    {" "}<span className="text-amber-400">
                      ({backfillResult.total_skipped} rows skipped)
                    </span>
                    {backfillResult.skipped_sample.length > 0 && (
                      <details className="mt-1 text-[11px] text-ink-faint">
                        <summary className="cursor-pointer">Show details</summary>
                        <ul className="mt-1 space-y-0.5 font-mono">
                          {backfillResult.skipped_sample.map((s, i) => (
                            <li key={i}>· {s}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
          <div className="shrink-0">
            <input
              ref={backfillInputRef}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) handleBackfill(f);
              }}
            />
            <button
              type="button"
              onClick={() => backfillInputRef.current?.click()}
              disabled={backfilling}
              className="btn-outline border-violet-500/40 text-violet-200 hover:bg-violet-500/10"
            >
              {backfilling ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
              {backfilling ? "Backfilling…" : "Upload CSV"}
            </button>
          </div>
        </div>
      </div>

      {/* Big tiles, one per report */}
      <div className="grid gap-3">
        {REPORTS.map((r) => (
          <div
            key={r.kind}
            className="card p-5 flex items-center gap-4 hover:border-ink-faint/30 transition-colors"
          >
            <div
              className={`shrink-0 h-12 w-12 rounded-xl bg-hover/60 flex items-center justify-center ${r.accent}`}
            >
              {r.icon}
            </div>
            <div className="min-w-0 flex-1">
              <h3 className="text-base font-semibold text-ink">{r.title}</h3>
              <p className="text-sm text-ink-dim mt-0.5">{r.desc}</p>
            </div>
            <button
              onClick={() => download(r.kind)}
              disabled={busy === r.kind || rangeInvalid}
              className="btn-primary shrink-0 disabled:opacity-40"
            >
              {busy === r.kind ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              {busy === r.kind ? "Generating…" : "Generate"}
            </button>
          </div>
        ))}
      </div>

      <div className="card p-3 border-amber-500/30 bg-amber-500/[0.04] text-xs text-ink-dim space-y-1">
        <div>
          <strong className="text-amber-300">Billable minutes</strong> in every report follow the
          same tariff: <strong>1–30 sec → 30 sec</strong>; <strong>&gt;30 sec → round up to the next full minute</strong>.
        </div>
        <div>
          <strong className="text-amber-300">Date</strong> is interpreted in IST (Asia/Kolkata) —
          a day runs midnight-to-midnight local. <strong className="text-amber-300">Temperature</strong>{" "}
          filters on the lead-temperature classifier output stored on each call.
        </div>
      </div>
    </div>
    </RequireAdmin>
  );
}
