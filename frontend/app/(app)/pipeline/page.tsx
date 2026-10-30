"use client";

/**
 * Lead Pipeline — strategic kanban.
 *
 * Reads from /api/pipeline which is the SAME source of truth as the
 * Inbox kanban and the Closures page. Bucket counts here match exactly
 * with the Inbox bucket sizes (which cap card lists at 100 per bucket
 * for snappy interaction, but show the true total in the column header).
 *
 * DNC is NOT a lane here — those leads live on /closures under the DNC
 * tab. Putting a lead on DNC happens via the Inbox disposition dropdown
 * (`do_not_call` or `wrong_number`), not by drag-and-drop. That keeps
 * every funnel_stage / status / dnd transition flowing through the
 * single DISPOSITION_MAPPING source of truth on the backend.
 *
 * Lane → real meaning:
 *   Cold     : Lead.lead_temperature = 'cold'
 *   Warm     : Lead.lead_temperature = 'warm'
 *   Hot      : Lead.lead_temperature = 'hot'
 *   Closing  : Lead.funnel_stage ∈ {applied, docs_received, enrolled}
 *
 * Excluded from this view:
 *   - Unclassified leads (lead_temperature IS NULL) — no AI contact yet
 *   - DND leads (live on Closures)
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Snowflake,
  Flame,
  Trophy,
  ThermometerSun,
  RefreshCw,
  ChevronRight,
  Loader2,
  Calendar,
  X,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useLiveEvents, type LiveEvent } from "@/lib/live";
import { maskPhone } from "@/lib/phone";
import { useAuth } from "@/lib/auth-context";

interface PipelineLead {
  id: number;
  name: string | null;
  phone: string;
  program_interest: string | null;
  lead_temperature: "hot" | "warm" | "cold" | null;
  funnel_stage: string;
  last_disposition: string | null;
  last_attempted_at: string | null;
  funnel_stage_set_at: string | null;
  attempt_count: number;
}

interface PipelineBucket {
  key: "hot" | "warm" | "cold" | "closing";
  count: number;
  leads: PipelineLead[];
}

interface PipelineResponse {
  buckets: PipelineBucket[];
  total_in_pipeline: number;
  server_time: string;
  filter_date: string | null;
  filter_window_start_utc: string | null;
  filter_window_end_utc: string | null;
}

// IST = UTC + 5:30. Compute today / yesterday / N-days-ago in IST without
// pulling a date library — the user's clock is irrelevant; the BACKEND
// rolls the day at midnight IST.
function istDateOffset(offsetDays: number): string {
  const nowMs = Date.now() + 5.5 * 60 * 60 * 1000;
  const istMs = nowMs - offsetDays * 86_400_000;
  const d = new Date(istMs);
  // Use UTC getters because we already shifted into IST.
  const y = d.getUTCFullYear();
  const m = (d.getUTCMonth() + 1).toString().padStart(2, "0");
  const day = d.getUTCDate().toString().padStart(2, "0");
  return `${y}-${m}-${day}`;
}

type LaneKey = "cold" | "warm" | "hot" | "closing";

const LANES: {
  key: LaneKey;
  label: string;
  subtitle: string;
  icon: typeof Snowflake;
  accent: string;
  barColor: string;
}[] = [
  { key: "cold",    label: "Cold",    subtitle: "Low engagement",  icon: Snowflake,      accent: "from-sky-500/10 to-sky-500/0 border-sky-500/20",       barColor: "bg-sky-500" },
  { key: "warm",    label: "Warm",    subtitle: "Browsing",        icon: ThermometerSun, accent: "from-amber-500/10 to-amber-500/0 border-amber-500/20", barColor: "bg-amber-500" },
  { key: "hot",     label: "Hot",     subtitle: "Strong intent",   icon: Flame,          accent: "from-rose-500/10 to-rose-500/0 border-rose-500/20",     barColor: "bg-rose-500" },
  { key: "closing", label: "Closing", subtitle: "Applied → enrolled", icon: Trophy,       accent: "from-emerald-500/10 to-emerald-500/0 border-emerald-500/20", barColor: "bg-emerald-500" },
];

function timeAgo(iso?: string | null): string {
  if (!iso) return "—";
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 60) return `${Math.floor(sec)}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}

export default function PipelinePage() {
  const { isAdmin } = useAuth();
  const [data, setData] = useState<PipelineResponse | null>(null);
  const [busy, setBusy] = useState(true);
  // Empty string = "All time" (no filter). Otherwise YYYY-MM-DD IST.
  const [selectedDate, setSelectedDate] = useState<string>("");

  const todayIst = useMemo(() => istDateOffset(0), []);
  const yesterdayIst = useMemo(() => istDateOffset(1), []);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const qs = new URLSearchParams({ limit_per_bucket: "200" });
      if (selectedDate) qs.set("date", selectedDate);
      const r = await api.get<PipelineResponse>(`/api/pipeline?${qs.toString()}`);
      setData(r);
    } finally {
      setBusy(false);
    }
  }, [selectedDate]);

  useEffect(() => {
    load();
    // Only auto-refresh when looking at the live (no-date) view. When
    // viewing a historical day, the data doesn't change — don't waste
    // requests.
    if (selectedDate) return;
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, [load, selectedDate]);

  useLiveEvents((e: LiveEvent) => {
    if (!selectedDate && (e.type === "call.ended" || e.type === "lead.created")) {
      load();
    }
  });

  const byKey: Record<LaneKey, PipelineBucket> = {
    cold: { key: "cold", count: 0, leads: [] },
    warm: { key: "warm", count: 0, leads: [] },
    hot: { key: "hot", count: 0, leads: [] },
    closing: { key: "closing", count: 0, leads: [] },
  };
  if (data) {
    for (const b of data.buckets) byKey[b.key as LaneKey] = b;
  }
  const total = data?.total_in_pipeline ?? 0;

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink tracking-tight">Lead Pipeline</h1>
          <p className="text-sm text-ink-dim mt-1">
            {selectedDate ? (
              <>
                Classifications recorded on{" "}
                <span className="text-brand font-medium">{selectedDate}</span>{" "}
                (IST). Each lead shown was classified that day.
              </>
            ) : (
              <>
                Every classified lead bucketed by AI temperature and funnel stage —
                same source as <span className="text-brand">Inbox</span> and{" "}
                <span className="text-brand">Closures</span>.
              </>
            )}
          </p>
        </div>
        <button onClick={load} disabled={busy} className="btn-ghost text-xs">
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          {busy ? "Refreshing" : "Refresh"}
        </button>
      </div>

      {/* Date filter — IST calendar day from when the system started */}
      <div className="card p-3 flex items-center gap-2 flex-wrap">
        <Calendar className="h-4 w-4 text-ink-faint shrink-0" />
        <span className="text-xs uppercase tracking-widest text-ink-faint mr-1">
          Filter
        </span>
        <DatePreset
          label="All time"
          active={!selectedDate}
          onClick={() => setSelectedDate("")}
        />
        <DatePreset
          label="Today"
          active={selectedDate === todayIst}
          onClick={() => setSelectedDate(todayIst)}
        />
        <DatePreset
          label="Yesterday"
          active={selectedDate === yesterdayIst}
          onClick={() => setSelectedDate(yesterdayIst)}
        />
        <input
          type="date"
          value={selectedDate}
          onChange={(e) => setSelectedDate(e.target.value)}
          max={todayIst}
          className="input py-1 text-xs max-w-[180px]"
        />
        {selectedDate && (
          <button
            type="button"
            onClick={() => setSelectedDate("")}
            className="btn-ghost text-xs"
            title="Clear date filter"
          >
            <X className="h-3 w-3" />
            Clear
          </button>
        )}
      </div>

      {/* Funnel summary bar */}
      <div className="card p-4">
        <div className="flex items-center justify-between gap-2 text-[11px] uppercase tracking-widest text-ink-dim font-mono">
          <span>
            {total}{" "}
            {selectedDate
              ? `lead${total === 1 ? "" : "s"} classified on ${selectedDate}`
              : "classified leads in pipeline"}
          </span>
          <span>
            HOT {byKey.hot.count} · WARM {byKey.warm.count} · COLD {byKey.cold.count} ·
            CLOSING {byKey.closing.count}
          </span>
        </div>
        <div className="mt-3 flex h-2 rounded-full overflow-hidden bg-hover">
          {(["cold", "warm", "hot", "closing"] as LaneKey[]).map((k) => {
            const pct = total ? (byKey[k].count / total) * 100 : 0;
            return (
              <div
                key={k}
                className={clsx(LANES.find((l) => l.key === k)?.barColor, "transition-all duration-500")}
                style={{ width: `${pct}%` }}
                title={`${k}: ${byKey[k].count} (${pct.toFixed(0)}%)`}
              />
            );
          })}
        </div>
      </div>

      {/* Kanban columns */}
      {busy && !data ? (
        <div className="card p-10 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-brand" />
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          {LANES.map((lane) => {
            const bucket = byKey[lane.key];
            const Icon = lane.icon;
            return (
              <div
                key={lane.key}
                className={clsx(
                  "card flex flex-col h-[calc(100vh-280px)] min-h-[420px] bg-gradient-to-b",
                  lane.accent
                )}
              >
                {/* Column header */}
                <div className="px-4 py-3 border-b border-border flex items-center justify-between sticky top-0 bg-surface/80 backdrop-blur-sm">
                  <div className="flex items-center gap-2">
                    <Icon className="h-4 w-4" />
                    <div>
                      <h3 className="text-sm font-semibold text-ink">{lane.label}</h3>
                      <p className="text-[10px] uppercase tracking-widest text-ink-faint">
                        {lane.subtitle}
                      </p>
                    </div>
                  </div>
                  <span className="font-mono text-lg font-semibold text-ink">
                    {bucket.count}
                  </span>
                </div>

                {/* Column body */}
                <div className="flex-1 overflow-y-auto p-2 space-y-2">
                  {bucket.leads.length === 0 ? (
                    <div className="text-[11px] text-ink-faint text-center py-6 italic">
                      No leads in this stage
                    </div>
                  ) : (
                    <>
                      {bucket.leads.map((lead) => (
                        <LeadKanbanCard key={lead.id} lead={lead} isAdmin={isAdmin} />
                      ))}
                      {bucket.count > bucket.leads.length && (
                        <p className="text-[10px] text-ink-faint text-center py-2">
                          + {bucket.count - bucket.leads.length} more in this stage
                        </p>
                      )}
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function DatePreset({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "text-xs px-2.5 py-1 rounded transition-colors",
        active
          ? "bg-brand text-white font-medium"
          : "bg-bg border border-border text-ink-dim hover:text-ink hover:border-brand"
      )}
    >
      {label}
    </button>
  );
}

function LeadKanbanCard({ lead, isAdmin }: { lead: PipelineLead; isAdmin: boolean }) {
  const router = useRouter();
  return (
    <button
      type="button"
      onClick={() => router.push(`/leads/${lead.id}`)}
      className="w-full text-left card p-3 hover:border-brand transition-all group cursor-pointer select-none"
    >
      <div className="flex items-start justify-between gap-2 mb-1">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink truncate">
            {lead.name || `Lead #${lead.id}`}
          </p>
          <p className="text-[11px] text-ink-faint font-mono truncate">
            {maskPhone(lead.phone, isAdmin)}
          </p>
        </div>
      </div>
      {lead.program_interest && (
        <p className="text-[11px] text-ink-dim truncate mb-1">{lead.program_interest}</p>
      )}
      {lead.last_disposition && (
        <p className="text-[10px] text-ink-faint italic line-clamp-2 leading-snug">
          last: {lead.last_disposition.replace(/_/g, " ")}
        </p>
      )}
      <div className="flex items-center justify-between gap-2 mt-2 pt-2 border-t border-border">
        <span className="text-[10px] text-ink-faint font-mono">
          {lead.attempt_count > 0
            ? `${lead.attempt_count} attempt${lead.attempt_count > 1 ? "s" : ""}`
            : "new"}
        </span>
        <span className="text-[10px] text-ink-faint font-mono">
          {timeAgo(lead.funnel_stage_set_at ?? lead.last_attempted_at)} ago
        </span>
        <ChevronRight className="h-3 w-3 text-ink-faint group-hover:text-brand transition-colors" />
      </div>
    </button>
  );
}
