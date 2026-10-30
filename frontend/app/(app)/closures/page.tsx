"use client";

/**
 * Closures — back-half of the funnel.
 *
 * Two tabs in one page so the admin gets a single landing:
 *   • Closing : Applied / Docs received / Enrolled (3 stages stacked)
 *   • DNC     : every lead with dnd=true, with last disposition shown
 *
 * Polls /api/closures every 15s for fresh totals. Each row links to the
 * full lead detail (timeline + notes).
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  Trophy,
  ShieldOff,
  FileCheck,
  FileText,
  GraduationCap,
  Loader2,
  RefreshCw,
  Phone,
  Search,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { maskPhone } from "@/lib/phone";

interface ClosureLead {
  id: number;
  name: string | null;
  phone: string;
  email: string | null;
  program_interest: string | null;
  funnel_stage: string;
  funnel_stage_set_at: string | null;
  status: string;
  last_disposition: string | null;
  last_disposition_at: string | null;
  dnd: boolean;
  notes: string | null;
}

interface StageBucket {
  stage: string;
  count: number;
  leads: ClosureLead[];
}

interface ClosuresResponse {
  closing: StageBucket[];
  dnc: ClosureLead[];
  totals: Record<string, number>;
  server_time: string;
}

const STAGE_META: Record<string, { label: string; icon: React.ComponentType<{ className?: string }>; tint: string }> = {
  applied:       { label: "Applied",         icon: FileText,       tint: "text-sky-400" },
  docs_received: { label: "Documents received", icon: FileCheck,    tint: "text-emerald-400" },
  enrolled:      { label: "Enrolled",        icon: GraduationCap,  tint: "text-amber-400" },
};

function fmtRel(iso: string | null, nowMs: number): string {
  if (!iso) return "—";
  const sec = Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 1000));
  if (sec < 60) return "just now";
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export default function ClosuresPage() {
  const { isAdmin } = useAuth();
  const [data, setData] = useState<ClosuresResponse | null>(null);
  const [tab, setTab] = useState<"closing" | "dnc">("closing");
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get<ClosuresResponse>("/api/closures");
      setData(r);
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to load closures");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 15_000);
    return () => clearInterval(id);
  }, [load]);

  const now = Date.now();
  const totals = data?.totals ?? {};
  const enrolledPct = totals.enrolled_pct ?? 0;

  const filterLeads = (leads: ClosureLead[]): ClosureLead[] => {
    if (!q.trim()) return leads;
    const term = q.toLowerCase();
    return leads.filter(
      (l) =>
        (l.name || "").toLowerCase().includes(term) ||
        l.phone.toLowerCase().includes(term) ||
        (l.email || "").toLowerCase().includes(term) ||
        (l.program_interest || "").toLowerCase().includes(term)
    );
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink flex items-center gap-2.5">
            <Trophy className="h-6 w-6 text-brand" />
            Closures
          </h1>
          <p className="text-sm text-ink-faint mt-1">
            Late-funnel leads ready to enroll and the DNC list of removed contacts.
          </p>
        </div>
        <button onClick={load} disabled={busy} className="btn-ghost text-sm">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </button>
      </div>

      {/* Headline tiles */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Tile label="Applied" value={String(totals.applied ?? 0)} />
        <Tile label="Docs received" value={String(totals.docs_received ?? 0)} />
        <Tile label="Enrolled" value={String(totals.enrolled ?? 0)} accent />
        <Tile label="Conversion" value={`${enrolledPct}%`} />
        <Tile label="On DNC" value={String(totals.dnc ?? 0)} muted />
      </div>

      {err && (
        <div className="card p-4 text-sm text-danger bg-danger/5 border-danger/30">{err}</div>
      )}

      {/* Tabs */}
      <div className="card overflow-hidden">
        <div className="flex items-center border-b border-border">
          <Tab active={tab === "closing"} onClick={() => setTab("closing")}>
            <Trophy className="h-4 w-4" />
            Closing pipeline
          </Tab>
          <Tab active={tab === "dnc"} onClick={() => setTab("dnc")}>
            <ShieldOff className="h-4 w-4" />
            DNC list
            {totals.dnc ? (
              <span className="ml-1.5 text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-danger/15 text-danger">
                {totals.dnc}
              </span>
            ) : null}
          </Tab>
          <div className="flex-1 px-3">
            <div className="relative max-w-xs ml-auto">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-ink-faint" />
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search name, phone, program…"
                className="input pl-8 py-1.5 text-xs"
              />
            </div>
          </div>
        </div>

        {busy && !data ? (
          <div className="p-10 flex justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-brand" />
          </div>
        ) : data ? (
          tab === "closing" ? (
            <div className="divide-y divide-border">
              {data.closing.map((bucket) => {
                const meta = STAGE_META[bucket.stage] ?? {
                  label: bucket.stage,
                  icon: FileText,
                  tint: "text-ink-dim",
                };
                const Icon = meta.icon;
                const filtered = filterLeads(bucket.leads);
                return (
                  <div key={bucket.stage}>
                    <div className="px-4 py-2.5 bg-surface flex items-center gap-2 text-sm font-semibold">
                      <Icon className={clsx("h-4 w-4", meta.tint)} />
                      {meta.label}
                      <span className="text-xs text-ink-faint font-normal ml-1">· {bucket.count}</span>
                    </div>
                    {filtered.length === 0 ? (
                      <div className="px-4 py-6 text-xs text-ink-faint text-center">
                        {bucket.leads.length === 0
                          ? `No leads in ${meta.label.toLowerCase()} yet.`
                          : "No matches for current search."}
                      </div>
                    ) : (
                      filtered.map((l) => (
                        <LeadRow key={l.id} lead={l} isAdmin={isAdmin} nowMs={now} />
                      ))
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="divide-y divide-border">
              {(() => {
                const filtered = filterLeads(data.dnc);
                if (filtered.length === 0) {
                  return (
                    <div className="px-4 py-12 text-sm text-ink-faint text-center">
                      {data.dnc.length === 0
                        ? "DNC list is empty — no leads removed from dialing yet."
                        : "No matches for current search."}
                    </div>
                  );
                }
                return filtered.map((l) => (
                  <LeadRow key={l.id} lead={l} isAdmin={isAdmin} nowMs={now} dncMode />
                ));
              })()}
            </div>
          )
        ) : null}
      </div>
    </div>
  );
}

function Tile({
  label,
  value,
  accent,
  muted,
}: {
  label: string;
  value: string;
  accent?: boolean;
  muted?: boolean;
}) {
  return (
    <div
      className={clsx(
        "card p-4",
        accent && "border-amber-500/40 bg-amber-500/5"
      )}
    >
      <p className="text-[10px] uppercase tracking-wider text-ink-faint">{label}</p>
      <p
        className={clsx(
          "text-2xl font-semibold mt-1 tabular-nums",
          accent ? "text-amber-400" : muted ? "text-ink-dim" : "text-ink"
        )}
      >
        {value}
      </p>
    </div>
  );
}

function Tab({
  children,
  active,
  onClick,
}: {
  children: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        "px-4 py-3 text-sm flex items-center gap-2 border-b-2 -mb-px transition-colors",
        active
          ? "border-brand text-brand font-medium"
          : "border-transparent text-ink-dim hover:text-ink"
      )}
    >
      {children}
    </button>
  );
}

function LeadRow({
  lead,
  isAdmin,
  nowMs,
  dncMode = false,
}: {
  lead: ClosureLead;
  isAdmin: boolean;
  nowMs: number;
  dncMode?: boolean;
}) {
  return (
    <Link
      href={`/leads/${lead.id}`}
      className="block px-4 py-3 hover:bg-hover transition-colors"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-medium text-ink truncate">
              {lead.name || "Unnamed lead"}
            </p>
            {lead.last_disposition && (
              <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-bg border border-border text-ink-dim">
                {lead.last_disposition.replace(/_/g, " ")}
              </span>
            )}
            {dncMode && (
              <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-danger/15 text-danger flex items-center gap-1">
                <ShieldOff className="h-2.5 w-2.5" />
                DNC
              </span>
            )}
          </div>
          <p className="text-xs text-ink-faint mt-0.5 truncate flex items-center gap-2">
            <Phone className="h-3 w-3" />
            {maskPhone(lead.phone, isAdmin)}
            {lead.program_interest && <span>· {lead.program_interest}</span>}
          </p>
        </div>
        <div className="text-right shrink-0">
          <p className="text-[11px] text-ink-dim tabular-nums">
            {fmtRel(lead.last_disposition_at || lead.funnel_stage_set_at, nowMs)}
          </p>
          <p className="text-[10px] text-ink-faint uppercase tracking-wide">
            {lead.status}
          </p>
        </div>
      </div>
    </Link>
  );
}
