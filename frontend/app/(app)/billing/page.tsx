"use client";

/**
 * Usage — per-call billable minutes, post-deployment-epoch only.
 *
 * Currency display removed 2026-06-03 per client request. The backend
 * still computes amount_inr (the field is unused in the UI now but kept
 * so the endpoint contract doesn't break for any other consumer or for
 * a future re-enable).
 *
 * Billable-time rules (unchanged):
 *   1-30s  → flat 30s
 *   >30s   → round UP to next full minute
 */
import { useCallback, useEffect, useState } from "react";
import { Receipt, PhoneCall, Calendar, Clock, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { fmtIST } from "@/lib/datetime";
import { RequireAdmin } from "@/components/require-admin";

interface BillingSummary {
  rate_inr_per_minute: number;
  epoch_iso: string;
  total_calls: number;
  total_calls_excluded: number;
  total_billable_seconds: number;
  total_billable_minutes: number;
  total_amount_inr: number;          // unused in UI (currency removed)
  calls_today: number;
  amount_today_inr: number;          // unused in UI (currency removed)
}

interface BillingCallRow {
  call_id: number;
  lead_id: number | null;
  phone: string | null;
  caller_name: string | null;
  started_at: string;
  duration_s: number;
  billable_seconds: number;
  amount_inr: number;                // unused in UI (currency removed)
}

interface BillingCallList {
  total: number;
  items: BillingCallRow[];
}

const PAGE_SIZE = 50;

export default function BillingPage() {
  const [summary, setSummary] = useState<BillingSummary | null>(null);
  const [list, setList] = useState<BillingCallList | null>(null);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [s, l] = await Promise.all([
        api.get<BillingSummary>("/api/billing/summary"),
        api.get<BillingCallList>(
          `/api/billing/calls?limit=${PAGE_SIZE}&offset=${page * PAGE_SIZE}`,
        ),
      ]);
      setSummary(s);
      setList(l);
    } finally {
      setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  const mmss = (s: number) =>
    `${Math.floor(s / 60)}:${(Math.floor(s) % 60).toString().padStart(2, "0")}`;

  const lastPage = list ? Math.max(0, Math.ceil(list.total / PAGE_SIZE) - 1) : 0;

  return (
    <RequireAdmin>
    <div className="space-y-5">
      {/* Page header */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Receipt className="h-6 w-6 text-brand" /> Usage
          </h1>
          <p className="text-sm text-ink-dim mt-1">
            Billable call time. Calls 1–30s round up to 30s; longer calls round up to the next full minute.
          </p>
        </div>
        {loading && <Loader2 className="h-4 w-4 animate-spin text-ink-faint" />}
      </div>

      {/* Summary cards — minutes + counts only, no currency */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <SummaryCard
          label="Total billable minutes"
          value={summary ? summary.total_billable_minutes.toFixed(2) : "—"}
          icon={<Clock className="h-4 w-4" />}
          accent="text-amber-400"
          big
        />
        <SummaryCard
          label="Billable calls"
          value={summary ? summary.total_calls.toLocaleString("en-IN") : "—"}
          icon={<PhoneCall className="h-4 w-4" />}
          accent="text-sky-400"
        />
        <SummaryCard
          label="Today"
          value={
            summary
              ? `${summary.calls_today} call${summary.calls_today === 1 ? "" : "s"}`
              : "—"
          }
          icon={<Calendar className="h-4 w-4" />}
          accent="text-rose-400"
        />
      </div>

      {/* Epoch / pre-deploy exclusion note */}
      {summary && summary.total_calls_excluded > 0 && (
        <div className="card p-3 border-amber-500/30 bg-amber-500/[0.04] text-sm">
          <span className="text-amber-300 font-medium">Excluded:</span>{" "}
          <span className="text-ink-dim">
            {summary.total_calls_excluded} pre-deployment call
            {summary.total_calls_excluded === 1 ? "" : "s"} (before{" "}
            <span className="font-mono">{fmtIST(summary.epoch_iso)}</span>) are not counted.
          </span>
        </div>
      )}

      {/* Per-call table — actual vs billable time only */}
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-border flex items-center justify-between">
          <h3 className="font-semibold text-sm">Per-call detail</h3>
          <span className="text-xs text-ink-faint font-mono">
            {list ? `${list.total} total` : "…"}
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-hover/40 text-ink-dim text-xs uppercase tracking-wider">
              <tr>
                <th className="text-left px-4 py-2.5 font-medium">Call</th>
                <th className="text-left px-4 py-2.5 font-medium">When</th>
                <th className="text-left px-4 py-2.5 font-medium">Caller</th>
                <th className="text-right px-4 py-2.5 font-medium">Actual</th>
                <th className="text-right px-4 py-2.5 font-medium">Billable</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {list?.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-ink-dim">
                    No billable calls yet. New calls from{" "}
                    <span className="font-mono">{summary ? fmtIST(summary.epoch_iso) : "epoch"}</span>{" "}
                    will appear here.
                  </td>
                </tr>
              )}
              {list?.items.map((r) => (
                <tr key={r.call_id} className="hover:bg-hover/30 transition-colors">
                  <td className="px-4 py-2.5 font-mono text-ink-faint">#{r.call_id}</td>
                  <td className="px-4 py-2.5 text-ink-dim whitespace-nowrap">
                    {fmtIST(r.started_at)}
                  </td>
                  <td className="px-4 py-2.5">
                    <div className="text-ink">{r.caller_name || "—"}</div>
                    <div className="text-xs font-mono text-ink-faint">{r.phone || "—"}</div>
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono text-ink-dim">
                    {mmss(r.duration_s)}
                  </td>
                  <td className="px-4 py-2.5 text-right font-mono">
                    {r.billable_seconds === 0 ? (
                      <span className="text-ink-faint">free</span>
                    ) : (
                      <span className="text-ink font-semibold">{mmss(r.billable_seconds)}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {list && list.total > PAGE_SIZE && (
          <div className="px-4 py-2.5 border-t border-border flex items-center justify-between text-xs">
            <span className="text-ink-faint font-mono">
              page {page + 1} / {lastPage + 1}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="btn-ghost px-3 py-1 disabled:opacity-40"
              >
                Prev
              </button>
              <button
                onClick={() => setPage((p) => Math.min(lastPage, p + 1))}
                disabled={page >= lastPage}
                className="btn-ghost px-3 py-1 disabled:opacity-40"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
    </RequireAdmin>
  );
}

function SummaryCard({
  label,
  value,
  icon,
  accent,
  big,
}: {
  label: string;
  value: string;
  icon: React.ReactNode;
  accent: string;
  big?: boolean;
}) {
  return (
    <div className="card p-4">
      <div className={`flex items-center gap-1.5 text-[11px] uppercase tracking-wider ${accent}`}>
        {icon} {label}
      </div>
      <div
        className={`mt-2 font-mono font-bold ${big ? "text-3xl" : "text-2xl"} text-ink`}
      >
        {value}
      </div>
    </div>
  );
}
