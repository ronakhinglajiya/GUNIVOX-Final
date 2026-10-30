"use client";

/**
 * Calls list — enhanced 2026-05-19 with:
 *   - lead-temperature pill column (uses backend's classifier output)
 *   - caller name + program columns (so the row tells a story)
 *   - filter chips for HOT / WARM / COLD
 *   - search by lead / caller name / program
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { fmtIST } from "@/lib/datetime";
import { useLiveEvents, type LiveEvent } from "@/lib/live";
import type { Call, CallsList } from "@/lib/types";
import { useAuth } from "@/lib/auth-context";
import { ChevronRight, RefreshCw, Radio, Search, Flame, ThermometerSun, Snowflake } from "lucide-react";

function fmtDuration(s?: number | null): string {
  if (!s) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

/** Billable seconds per the tariff:
 *  duration < 1s   → 0  (never connected)
 *  1 ≤ d ≤ 30s    → 30 (min slab)
 *  d > 30s         → ceil(d / 60) * 60
 *  Mirrors backend `app/billing.py` so the operator sees the same number
 *  client-side without an extra round-trip per row.
 */
function billableSeconds(s?: number | null): number {
  if (!s || s < 1) return 0;
  if (s <= 30) return 30;
  return Math.ceil(s / 60) * 60;
}
function fmtBillable(s?: number | null): string {
  const bs = billableSeconds(s);
  if (bs === 0) return "—";
  const m = Math.floor(bs / 60);
  const sec = bs % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function TempPill({ t }: { t?: string | null }) {
  if (!t) return <span className="badge-muted">—</span>;
  const cls = t === "hot" ? "pill-hot" : t === "warm" ? "pill-warm" : t === "cold" ? "pill-cold" : "badge-muted";
  return <span className={cls}>{t}</span>;
}

type TempFilter = "all" | "hot" | "warm" | "cold";

export default function CallsPage() {
  const { isAdmin } = useAuth();
  const [items, setItems] = useState<Call[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [liveBlip, setLiveBlip] = useState(false);
  const [query, setQuery] = useState("");
  const [temp, setTemp] = useState<TempFilter>("all");

  const fetchList = useCallback(async () => {
    setLoading(true);
    try {
      // limit raised 100 → 500 (2026-06-03): the previous 100-row cap
      // caused the temperature chip counts to under-report — e.g. the DB
      // had 6 cold calls but only 4 fell in the first 100 returned, so
      // the "Cold" chip showed 4 while the Dashboard (which queries the
      // full DB) showed 6. With 500 we load everything for the demo
      // dataset (<500 calls) so the chip == ground truth.
      const r = await api.get<CallsList>("/api/calls?limit=500&offset=0");
      setItems(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  const { status: liveStatus } = useLiveEvents((e: LiveEvent) => {
    if (e.type === "ping" || e.type === "hello") return;
    setLiveBlip(true);
    setTimeout(() => setLiveBlip(false), 600);
    if (e.type === "call.queued" || e.type === "call.ended" || e.type === "call.turn") {
      fetchList();
    }
  });

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((c) => {
      if (temp !== "all" && c.lead_temperature !== temp) return false;
      if (!q) return true;
      return (
        String(c.id).includes(q) ||
        String(c.lead_id).includes(q) ||
        (c.caller_name || "").toLowerCase().includes(q) ||
        (c.lead_program || "").toLowerCase().includes(q) ||
        (c.lead_reasons || "").toLowerCase().includes(q)
      );
    });
  }, [items, query, temp]);

  const counts = useMemo(() => {
    const out = { hot: 0, warm: 0, cold: 0 };
    for (const c of items) {
      if (c.lead_temperature === "hot") out.hot++;
      else if (c.lead_temperature === "warm") out.warm++;
      else if (c.lead_temperature === "cold") out.cold++;
    }
    return out;
  }, [items]);

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink tracking-tight">Calls</h1>
          <p className="text-sm text-ink-dim mt-1 flex items-center gap-2 font-mono">
            <span>
              {filtered.length} shown · {total} total
            </span>
            <span
              className={
                liveStatus === "open"
                  ? "inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-success"
                  : "inline-flex items-center gap-1 text-[10px] uppercase tracking-widest text-ink-faint"
              }
            >
              <Radio className={`h-3 w-3 ${liveBlip ? "text-brand animate-pulse" : ""}`} />
              {liveStatus === "open" ? "live" : liveStatus}
            </span>
          </p>
        </div>
        <button onClick={fetchList} className="btn-ghost text-sm">
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </div>

      {/* Filter bar */}
      <div className="card p-3 flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-ink-faint" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by name, program, reasons, ID…"
            className="input pl-9"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <FilterChip active={temp === "all"}  onClick={() => setTemp("all")}  label="All"  count={total} />
          <FilterChip active={temp === "hot"}  onClick={() => setTemp("hot")}  label="Hot"  count={counts.hot}  icon={<Flame className="h-3 w-3" />} kind="hot" />
          <FilterChip active={temp === "warm"} onClick={() => setTemp("warm")} label="Warm" count={counts.warm} icon={<ThermometerSun className="h-3 w-3" />} kind="warm" />
          <FilterChip active={temp === "cold"} onClick={() => setTemp("cold")} label="Cold" count={counts.cold} icon={<Snowflake className="h-3 w-3" />} kind="cold" />
        </div>
      </div>

      {/* List */}
      <div className="card overflow-hidden">
        {loading ? (
          <div className="p-4 space-y-2">
            {[1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="skeleton h-10 w-full" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="p-10 text-center text-ink-dim">
            <p className="text-sm">
              {items.length === 0
                ? "No calls yet — use the dialer on the right to start one."
                : "No calls match this filter."}
            </p>
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Caller</th>
                <th>Program</th>
                <th>Temperature</th>
                <th>Started</th>
                <th>Duration</th>
                {isAdmin && (
                  <th title="Billable time per tariff: 1-30s → 30s, >30s → next minute">Billable</th>
                )}
                <th>TTFA</th>
                <th>Outcome</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((c) => (
                <tr key={c.id}>
                  <td className="font-mono text-xs text-ink-faint">#{c.id}</td>
                  <td>
                    <p className="text-ink truncate max-w-[140px]" title={c.caller_name || ""}>
                      {c.caller_name || <span className="text-ink-faint">Lead #{c.lead_id}</span>}
                    </p>
                  </td>
                  <td>
                    <p className="text-ink-dim text-xs truncate max-w-[160px]" title={c.lead_program || ""}>
                      {c.lead_program || "—"}
                    </p>
                  </td>
                  <td>
                    {isAdmin ? <TempPill t={c.lead_temperature} /> : <span className="text-ink-faint">—</span>}
                  </td>
                  <td className="text-ink-dim text-xs">
                    {fmtIST(c.started_at)}
                  </td>
                  <td className="font-mono text-sm">{fmtDuration(c.duration_s)}</td>
                  {isAdmin && (
                    <td className="font-mono text-sm font-semibold text-amber-400">
                      {fmtBillable(c.duration_s)}
                    </td>
                  )}
                  <td className="font-mono text-sm text-ink-dim">
                    {c.ttfa_p50_ms ? `${c.ttfa_p50_ms}ms` : "—"}
                  </td>
                  <td>
                    <span
                      className={
                        c.outcome === "completed"
                          ? "badge-success"
                          : c.outcome?.startsWith("transfer")
                          ? "badge-info"
                          : c.outcome === "no_speech"
                          ? "badge-warning"
                          : "badge-muted"
                      }
                    >
                      {c.outcome ?? (c.ended_at ? "ended" : "in-progress")}
                    </span>
                  </td>
                  <td className="text-right">
                    <Link href={`/calls/${c.id}`} className="btn-ghost text-xs">
                      Open <ChevronRight className="h-3 w-3" />
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  label,
  count,
  icon,
  kind,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
  count: number;
  icon?: React.ReactNode;
  kind?: "hot" | "warm" | "cold";
}) {
  const activeAccent =
    kind === "hot"
      ? "border-rose-500/40 bg-rose-500/10 text-rose-400"
      : kind === "warm"
      ? "border-amber-500/40 bg-amber-500/10 text-amber-400"
      : kind === "cold"
      ? "border-sky-500/40 bg-sky-500/10 text-sky-400"
      : "border-brand bg-brand-subtle text-brand";
  return (
    <button
      onClick={onClick}
      className={
        active
          ? `inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border text-xs font-medium ${activeAccent}`
          : "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-border bg-surface text-ink-dim hover:bg-hover hover:text-ink transition-colors text-xs font-medium"
      }
    >
      {icon}
      {label}
      <span className="font-mono text-[10px] opacity-70">{count}</span>
    </button>
  );
}
