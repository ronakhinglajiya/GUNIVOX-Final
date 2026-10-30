"use client";

/**
 * Analytics Hub — admin-only dashboard.
 *
 * Pulls every widget from GET /api/admin/analytics?window=... in one shot.
 * Tabs: Overview / Funnel / Counsellors / AI · Programs / Heatmap.
 * Window: Today · 7d · 30d · All.
 *
 * Charting via Recharts (declarative React components, light footprint).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BarChart3,
  TrendingDown,
  Users,
  Sparkles,
  ShieldAlert,
  Loader2,
  RefreshCw,
  Flame,
  Activity,
  Clock,
  CalendarDays,
  PhoneCall,
} from "lucide-react";
import clsx from "clsx";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as RTooltip,
  Cell,
  PieChart,
  Pie,
  Legend,
  LineChart,
  Line,
  CartesianGrid,
} from "recharts";
import { api } from "@/lib/api";
import { RequireAdmin } from "@/components/require-admin";

// ─────────────── types ───────────────
type Window = "today" | "7d" | "30d" | "all";

interface FunnelStep { key: string; label: string; count: number; }
interface Funnel { steps: FunnelStep[]; total_leads: number; }

interface AITile {
  total_dials: number;
  pickup_rate_pct: number;
  avg_duration_s: number;
  median_ttfa_ms: number | null;
  pct_long_conversations: number;
  sparkline_calls_per_day: { date: string; calls: number }[];
}

interface LeaderboardRow {
  user_id: number;
  name: string;
  calls: number;
  talk_time_sec: number;
  hot_handled: number;
  conversion_pct: number;
  hours_signed_in: number;
}

interface SLAAlerts {
  hot_unclaimed_over_30min: number;
  callbacks_overdue: number;
  locks_orphaned: number;
}

interface OutcomeSlice { outcome: string; count: number; }
interface HeatmapCell { weekday: number; hour: number; dials: number; connects: number; hots: number; }
interface ProgramRow { program: string; hot: number; warm: number; cold: number; total: number; }

interface AnalyticsResponse {
  window: Window;
  window_start_utc: string | null;
  window_end_utc: string;
  filter_from: string | null;
  filter_to: string | null;
  funnel: Funnel;
  ai: AITile;
  leaderboard: LeaderboardRow[];
  sla: SLAAlerts;
  outcomes: OutcomeSlice[];
  heatmap: HeatmapCell[];
  program_demand: ProgramRow[];
  server_time: string;
}

function istDateOffset(offsetDays: number): string {
  const nowMs = Date.now() + 5.5 * 60 * 60 * 1000;
  const istMs = nowMs - offsetDays * 86_400_000;
  const d = new Date(istMs);
  const y = d.getUTCFullYear();
  const m = (d.getUTCMonth() + 1).toString().padStart(2, "0");
  const day = d.getUTCDate().toString().padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const WINDOW_OPTIONS: { value: Window; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d",    label: "Last 7 days" },
  { value: "30d",   label: "Last 30 days" },
  { value: "all",   label: "All time" },
];

const TABS = [
  { id: "overview", label: "Overview", icon: BarChart3 },
  { id: "funnel",   label: "Funnel",   icon: TrendingDown },
  { id: "counsellors", label: "Counsellors", icon: Users },
  { id: "ai_programs", label: "AI · Programs", icon: Sparkles },
  { id: "heatmap",  label: "Heatmap",  icon: CalendarDays },
] as const;

type TabId = (typeof TABS)[number]["id"];

// Theme tints
const TINT = {
  brand:   "var(--color-brand, #ef4444)",
  cold:    "#0ea5e9",
  warm:    "#f59e0b",
  hot:     "#ef4444",
  success: "#10b981",
  muted:   "#71717a",
};

const OUTCOME_COLORS: Record<string, string> = {
  completed: TINT.success,
  no_answer: TINT.muted,
  busy: TINT.warm,
  failed: TINT.hot,
  transferred: TINT.brand,
  no_speech: "#a855f7",
};

function fmtDuration(sec: number): string {
  if (!sec) return "0s";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.round(sec % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

// ─────────────── page ───────────────
function AnalyticsHubInner() {
  const [window, setWindow] = useState<Window>("7d");
  // Custom date range. When both set, overrides `window` on the backend.
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [tab, setTab] = useState<TabId>("overview");
  const [data, setData] = useState<AnalyticsResponse | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [rangeError, setRangeError] = useState<string | null>(null);

  const todayIst = useMemo(() => istDateOffset(0), []);
  const customRangeActive = !!(dateFrom && dateTo);

  const load = useCallback(async () => {
    if (rangeError) return;
    setBusy(true);
    try {
      const qs = new URLSearchParams({ window });
      if (customRangeActive) {
        qs.set("from", dateFrom);
        qs.set("to", dateTo);
      }
      const r = await api.get<AnalyticsResponse>(`/api/admin/analytics?${qs.toString()}`);
      setData(r);
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to load analytics");
    } finally {
      setBusy(false);
    }
  }, [window, dateFrom, dateTo, customRangeActive, rangeError]);

  // Validate range whenever the inputs change. Block load on invalid.
  useEffect(() => {
    if (!dateFrom && !dateTo) {
      setRangeError(null);
      return;
    }
    if ((dateFrom && !dateTo) || (dateTo && !dateFrom)) {
      setRangeError("Pick both From and To dates.");
      return;
    }
    if (dateFrom && dateTo && dateTo < dateFrom) {
      setRangeError("To must be on or after From.");
      return;
    }
    setRangeError(null);
  }, [dateFrom, dateTo]);

  useEffect(() => {
    load();
    // Don't auto-refresh historical date ranges — they don't change.
    if (customRangeActive) return;
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load, customRangeActive]);

  const clearRange = () => {
    setDateFrom("");
    setDateTo("");
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink tracking-tight flex items-center gap-2">
            <BarChart3 className="h-6 w-6 text-brand" />
            Analytics Hub
          </h1>
          <p className="text-sm text-ink-faint mt-1">
            {customRangeActive ? (
              <>
                Custom date range:{" "}
                <span className="text-brand font-medium">{dateFrom}</span> →{" "}
                <span className="text-brand font-medium">{dateTo}</span>{" "}
                <span className="text-ink-faint">(IST, inclusive)</span>
              </>
            ) : (
              <>
                Funnel, AI performance, counsellor productivity, and program demand
                — all from a single windowed snapshot.
              </>
            )}
          </p>
        </div>
        <button onClick={load} disabled={busy || !!rangeError} className="btn-ghost text-xs">
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
          Refresh
        </button>
      </div>

      {/* Filter bar — presets + custom date range */}
      <div className="card p-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-3.5 w-3.5 text-ink-faint" />
          <span className="text-[10px] uppercase tracking-widest text-ink-faint">
            Window
          </span>
          <WindowPicker
            value={window}
            onChange={(w) => {
              setWindow(w);
              // Switching to a preset clears the custom range.
              if (customRangeActive) clearRange();
            }}
            disabled={customRangeActive}
          />
        </div>

        <div className="h-6 w-px bg-border mx-1" aria-hidden="true" />

        <div className="flex items-center gap-2 flex-wrap">
          <CalendarDays className="h-3.5 w-3.5 text-ink-faint" />
          <span className="text-[10px] uppercase tracking-widest text-ink-faint">
            Or pick a range
          </span>
          <input
            type="date"
            value={dateFrom}
            onChange={(e) => setDateFrom(e.target.value)}
            max={todayIst}
            className={clsx(
              "input py-1 text-xs max-w-[150px]",
              rangeError && "border-danger"
            )}
            aria-label="From date"
          />
          <span className="text-xs text-ink-faint">→</span>
          <input
            type="date"
            value={dateTo}
            onChange={(e) => setDateTo(e.target.value)}
            max={todayIst}
            min={dateFrom || undefined}
            className={clsx(
              "input py-1 text-xs max-w-[150px]",
              rangeError && "border-danger"
            )}
            aria-label="To date"
          />
          {customRangeActive && (
            <button onClick={clearRange} className="btn-ghost text-xs" title="Back to preset">
              Clear
            </button>
          )}
        </div>

        {rangeError && (
          <span className="text-xs text-danger ml-2">{rangeError}</span>
        )}
      </div>

      {/* SLA alerts banner */}
      {data && <SLABanner sla={data.sla} />}

      {/* Tabs */}
      <div className="card overflow-hidden">
        <div className="flex border-b border-border overflow-x-auto">
          {TABS.map((t) => {
            const Icon = t.icon;
            const active = tab === t.id;
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={clsx(
                  "px-4 py-3 text-sm flex items-center gap-2 border-b-2 -mb-px transition-colors whitespace-nowrap",
                  active
                    ? "border-brand text-brand font-medium"
                    : "border-transparent text-ink-dim hover:text-ink"
                )}
              >
                <Icon className="h-4 w-4" />
                {t.label}
              </button>
            );
          })}
        </div>

        {err && (
          <div className="p-4 text-sm text-danger bg-danger/5">{err}</div>
        )}
        {busy && !data ? (
          <div className="p-10 flex justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-brand" />
          </div>
        ) : data ? (
          <div className="p-5">
            {tab === "overview" && <OverviewTab data={data} />}
            {tab === "funnel" && <FunnelTab funnel={data.funnel} />}
            {tab === "counsellors" && <CounsellorsTab rows={data.leaderboard} />}
            {tab === "ai_programs" && <AIProgramsTab ai={data.ai} programs={data.program_demand} outcomes={data.outcomes} />}
            {tab === "heatmap" && <HeatmapTab cells={data.heatmap} />}
          </div>
        ) : null}
      </div>
    </div>
  );
}

// ─────────────── widgets ───────────────
function WindowPicker({
  value,
  onChange,
  disabled = false,
}: {
  value: Window;
  onChange: (w: Window) => void;
  disabled?: boolean;
}) {
  return (
    <div
      className={clsx(
        "flex bg-bg border border-border rounded-md p-0.5",
        disabled && "opacity-50"
      )}
      title={disabled ? "Custom range active — clear to use presets" : undefined}
    >
      {WINDOW_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          onClick={() => onChange(opt.value)}
          disabled={disabled}
          className={clsx(
            "px-3 py-1 text-xs rounded transition-colors",
            !disabled && value === opt.value
              ? "bg-brand text-white font-medium"
              : "text-ink-dim hover:text-ink",
            disabled && "cursor-not-allowed"
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function SLABanner({ sla }: { sla: SLAAlerts }) {
  const total = sla.hot_unclaimed_over_30min + sla.callbacks_overdue + sla.locks_orphaned;
  if (total === 0) {
    return (
      <div className="card p-3 flex items-center gap-2 text-sm bg-emerald-500/5 border-emerald-500/20">
        <ShieldAlert className="h-4 w-4 text-emerald-500" />
        <span className="text-ink">All SLAs green — no breaches.</span>
      </div>
    );
  }
  return (
    <div className="card p-3 flex items-center gap-4 text-sm bg-danger/5 border-danger/30 flex-wrap">
      <ShieldAlert className="h-4 w-4 text-danger" />
      <span className="font-semibold text-danger">SLA breaches:</span>
      {sla.hot_unclaimed_over_30min > 0 && (
        <span className="text-ink">
          <span className="text-danger font-bold">{sla.hot_unclaimed_over_30min}</span>{" "}
          hot lead{sla.hot_unclaimed_over_30min === 1 ? "" : "s"} unclaimed &gt;30 min
        </span>
      )}
      {sla.callbacks_overdue > 0 && (
        <span className="text-ink">
          <span className="text-danger font-bold">{sla.callbacks_overdue}</span>{" "}
          callback{sla.callbacks_overdue === 1 ? "" : "s"} overdue
        </span>
      )}
      {sla.locks_orphaned > 0 && (
        <span className="text-ink">
          <span className="text-danger font-bold">{sla.locks_orphaned}</span>{" "}
          orphan lock{sla.locks_orphaned === 1 ? "" : "s"}
        </span>
      )}
    </div>
  );
}

function OverviewTab({ data }: { data: AnalyticsResponse }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
      <Card title="Funnel" subtitle="Total → Connected → Engaged → Applied → Enrolled">
        <FunnelChart steps={data.funnel.steps} />
      </Card>
      <Card title="AI performance" subtitle="Last 7 days · daily call volume sparkline">
        <AITileMini ai={data.ai} />
      </Card>
      <Card title="Outcome breakdown" subtitle="Call results in the selected window">
        <OutcomeDonut outcomes={data.outcomes} />
      </Card>
      <Card title="Top 3 counsellors" subtitle="By calls in selected window">
        <LeaderboardTable rows={data.leaderboard.slice(0, 3)} compact />
      </Card>
    </div>
  );
}

function FunnelTab({ funnel }: { funnel: Funnel }) {
  // Compute drop-off % between adjacent steps.
  const enriched = funnel.steps.map((s, i, arr) => {
    const prev = i > 0 ? arr[i - 1].count : null;
    const drop = prev && prev > 0 ? Math.round(100 * (1 - s.count / prev)) : null;
    const passPct = funnel.total_leads ? Math.round((s.count / funnel.total_leads) * 100) : 0;
    return { ...s, drop, passPct };
  });
  return (
    <div className="space-y-4">
      <p className="text-xs text-ink-faint">
        Each row shows total leads at that stage. Drop-off % is the leakage from
        the previous stage.
      </p>
      <div className="space-y-2.5">
        {enriched.map((s) => (
          <div key={s.key} className="flex items-center gap-3">
            <div className="w-44 text-sm text-ink">{s.label}</div>
            <div className="flex-1 relative h-9 bg-bg rounded-md overflow-hidden border border-border">
              <div
                className="absolute inset-y-0 left-0 bg-brand/30"
                style={{ width: `${s.passPct}%` }}
              />
              <div className="absolute inset-0 flex items-center justify-between px-3 text-sm">
                <span className="font-semibold text-ink tabular-nums">{s.count.toLocaleString()}</span>
                <span className="text-xs text-ink-faint">{s.passPct}% of total</span>
              </div>
            </div>
            <div className="w-20 text-right text-xs tabular-nums">
              {s.drop !== null ? (
                <span className={clsx(s.drop > 50 ? "text-danger" : "text-ink-faint")}>
                  -{s.drop}%
                </span>
              ) : (
                <span className="text-ink-faint">—</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function CounsellorsTab({ rows }: { rows: LeaderboardRow[] }) {
  return (
    <div>
      <LeaderboardTable rows={rows} />
    </div>
  );
}

function LeaderboardTable({ rows, compact = false }: { rows: LeaderboardRow[]; compact?: boolean }) {
  if (rows.length === 0) {
    return <div className="text-sm text-ink-faint text-center py-6">No activity in this window.</div>;
  }
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-[10px] uppercase tracking-wider text-ink-faint border-b border-border">
          <th className="text-left py-2 px-2">Counsellor</th>
          <th className="text-right py-2 px-2">Calls</th>
          <th className="text-right py-2 px-2">Talk time</th>
          <th className="text-right py-2 px-2">Hot</th>
          <th className="text-right py-2 px-2">Conv %</th>
          {!compact && <th className="text-right py-2 px-2">Hours</th>}
        </tr>
      </thead>
      <tbody className="divide-y divide-border">
        {rows.map((r) => (
          <tr key={r.user_id}>
            <td className="py-2 px-2 text-ink">{r.name}</td>
            <td className="py-2 px-2 text-right tabular-nums">{r.calls}</td>
            <td className="py-2 px-2 text-right tabular-nums">{fmtDuration(r.talk_time_sec)}</td>
            <td className="py-2 px-2 text-right tabular-nums">
              {r.hot_handled > 0 ? (
                <span className="text-amber-400">{r.hot_handled}</span>
              ) : (
                <span className="text-ink-faint">0</span>
              )}
            </td>
            <td className="py-2 px-2 text-right tabular-nums">
              <span
                className={clsx(
                  r.conversion_pct >= 20
                    ? "text-emerald-400"
                    : r.conversion_pct >= 10
                    ? "text-amber-400"
                    : "text-ink-faint"
                )}
              >
                {r.conversion_pct}%
              </span>
            </td>
            {!compact && (
              <td className="py-2 px-2 text-right tabular-nums text-ink-dim">
                {r.hours_signed_in}h
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function AIProgramsTab({ ai, programs, outcomes }: { ai: AITile; programs: ProgramRow[]; outcomes: OutcomeSlice[] }) {
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card title="AI performance" subtitle="Pickup, duration, TTFA, long-conversation %">
          <AITileFull ai={ai} />
        </Card>
        <Card title="Outcome breakdown" subtitle="Call results in selected window">
          <OutcomeDonut outcomes={outcomes} />
        </Card>
      </div>
      <Card title="Program demand" subtitle="Top programs discussed during calls, split by classification">
        <ProgramDemandChart programs={programs} />
      </Card>
    </div>
  );
}

function HeatmapTab({ cells }: { cells: HeatmapCell[] }) {
  const [metric, setMetric] = useState<"pickup" | "hot">("pickup");
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-xs text-ink-faint">
          When the AI calls in IST. Darker = stronger. Use this to schedule campaigns
          when leads are receptive.
        </p>
        <div className="flex bg-bg border border-border rounded-md p-0.5">
          <button
            onClick={() => setMetric("pickup")}
            className={clsx(
              "px-3 py-1 text-xs rounded",
              metric === "pickup" ? "bg-brand text-white" : "text-ink-dim"
            )}
          >
            Pickup rate
          </button>
          <button
            onClick={() => setMetric("hot")}
            className={clsx(
              "px-3 py-1 text-xs rounded",
              metric === "hot" ? "bg-brand text-white" : "text-ink-dim"
            )}
          >
            Hot rate
          </button>
        </div>
      </div>
      <Heatmap cells={cells} metric={metric} />
    </div>
  );
}

// ─────────────── small reusable bits ───────────────
function Card({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border bg-bg p-4">
      <h3 className="text-sm font-semibold text-ink">{title}</h3>
      {subtitle && <p className="text-[11px] text-ink-faint mt-0.5">{subtitle}</p>}
      <div className="mt-4">{children}</div>
    </div>
  );
}

function FunnelChart({ steps }: { steps: FunnelStep[] }) {
  const data = steps.map((s) => ({ name: s.label, count: s.count }));
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 0, right: 20 }}>
          <CartesianGrid strokeDasharray="2 2" stroke="rgba(120,120,140,0.15)" />
          <XAxis type="number" stroke="#71717a" fontSize={11} />
          <YAxis dataKey="name" type="category" stroke="#71717a" fontSize={11} width={120} />
          <RTooltip
            cursor={{ fill: "rgba(120,120,140,0.08)" }}
            contentStyle={{ background: "#18181b", border: "1px solid #27272a", fontSize: 12 }}
          />
          <Bar dataKey="count" fill={TINT.brand} radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function AITileMini({ ai }: { ai: AITile }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Stat label="Pickup rate" value={`${ai.pickup_rate_pct}%`} hint={`${ai.total_dials} dials`} />
        <Stat label="Avg duration" value={fmtDuration(ai.avg_duration_s)} />
      </div>
      <div className="h-16">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={ai.sparkline_calls_per_day}>
            <Line
              type="monotone"
              dataKey="calls"
              stroke={TINT.brand}
              strokeWidth={2}
              dot={false}
            />
            <RTooltip
              contentStyle={{ background: "#18181b", border: "1px solid #27272a", fontSize: 11 }}
              labelStyle={{ color: "#a1a1aa" }}
            />
            <XAxis dataKey="date" hide />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <p className="text-[10px] text-ink-faint text-center">Calls / day · last 7 days</p>
    </div>
  );
}

function AITileFull({ ai }: { ai: AITile }) {
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Pickup rate" value={`${ai.pickup_rate_pct}%`} hint={`${ai.total_dials} dials`} />
        <Stat label="Avg duration" value={fmtDuration(ai.avg_duration_s)} />
        <Stat
          label="Median TTFA"
          value={ai.median_ttfa_ms ? `${(ai.median_ttfa_ms / 1000).toFixed(1)}s` : "—"}
          hint="time to first audio"
        />
        <Stat
          label=">2 min talks"
          value={`${ai.pct_long_conversations}%`}
          hint="of connected"
        />
      </div>
      <div className="h-32">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={ai.sparkline_calls_per_day} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="2 2" stroke="rgba(120,120,140,0.15)" />
            <XAxis dataKey="date" stroke="#71717a" fontSize={10} tickFormatter={(d) => d.slice(5)} />
            <YAxis stroke="#71717a" fontSize={10} />
            <RTooltip contentStyle={{ background: "#18181b", border: "1px solid #27272a", fontSize: 11 }} />
            <Line type="monotone" dataKey="calls" stroke={TINT.brand} strokeWidth={2} dot={{ r: 3 }} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded border border-border bg-surface p-3">
      <p className="text-[10px] uppercase tracking-wider text-ink-faint">{label}</p>
      <p className="text-xl font-semibold text-ink mt-1 tabular-nums">{value}</p>
      {hint && <p className="text-[10px] text-ink-faint mt-0.5">{hint}</p>}
    </div>
  );
}

function OutcomeDonut({ outcomes }: { outcomes: OutcomeSlice[] }) {
  if (outcomes.length === 0) {
    return <div className="text-sm text-ink-faint text-center py-10">No outcome data.</div>;
  }
  const data = outcomes.map((o) => ({
    name: o.outcome.replace(/_/g, " "),
    value: o.count,
    fill: OUTCOME_COLORS[o.outcome] || "#a1a1aa",
  }));
  return (
    <div className="h-64">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={50}
            outerRadius={80}
            paddingAngle={2}
          >
            {data.map((entry, i) => (
              <Cell key={i} fill={entry.fill} stroke="transparent" />
            ))}
          </Pie>
          <RTooltip contentStyle={{ background: "#18181b", border: "1px solid #27272a", fontSize: 12 }} />
          <Legend wrapperStyle={{ fontSize: 11 }} />
        </PieChart>
      </ResponsiveContainer>
    </div>
  );
}

function ProgramDemandChart({ programs }: { programs: ProgramRow[] }) {
  if (programs.length === 0) {
    return <div className="text-sm text-ink-faint text-center py-10">No programs mentioned yet.</div>;
  }
  // Stacked horizontal bars: hot + warm + cold.
  const data = programs.map((p) => ({
    name: p.program.length > 38 ? p.program.slice(0, 38) + "…" : p.program,
    Hot: p.hot,
    Warm: p.warm,
    Cold: p.cold,
  }));
  return (
    <div style={{ height: Math.max(200, programs.length * 30) }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 0, right: 20 }}>
          <CartesianGrid strokeDasharray="2 2" stroke="rgba(120,120,140,0.15)" />
          <XAxis type="number" stroke="#71717a" fontSize={11} />
          <YAxis dataKey="name" type="category" stroke="#71717a" fontSize={10} width={240} />
          <RTooltip
            cursor={{ fill: "rgba(120,120,140,0.08)" }}
            contentStyle={{ background: "#18181b", border: "1px solid #27272a", fontSize: 12 }}
          />
          <Legend wrapperStyle={{ fontSize: 11 }} />
          <Bar dataKey="Hot"  stackId="t" fill={TINT.hot} />
          <Bar dataKey="Warm" stackId="t" fill={TINT.warm} />
          <Bar dataKey="Cold" stackId="t" fill={TINT.cold} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function Heatmap({ cells, metric }: { cells: HeatmapCell[]; metric: "pickup" | "hot" }) {
  // Build 7×24 matrix and compute the value to render per cell.
  const grid = useMemo(() => {
    const out: { v: number; raw: HeatmapCell | null }[][] = Array.from({ length: 7 }, () =>
      Array.from({ length: 24 }, () => ({ v: 0, raw: null }))
    );
    let maxV = 0;
    for (const c of cells) {
      if (c.weekday < 0 || c.weekday > 6 || c.hour < 0 || c.hour > 23) continue;
      const v =
        metric === "pickup"
          ? c.dials > 0
            ? (c.connects / c.dials) * 100
            : 0
          : c.connects > 0
            ? (c.hots / c.connects) * 100
            : 0;
      out[c.weekday][c.hour] = { v, raw: c };
      if (v > maxV) maxV = v;
    }
    return { out, maxV };
  }, [cells, metric]);

  if (cells.length === 0) {
    return (
      <div className="text-sm text-ink-faint text-center py-10">
        No calls in this window — no heatmap to draw.
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="text-[10px] border-separate border-spacing-0.5">
        <thead>
          <tr>
            <th />
            {Array.from({ length: 24 }, (_, h) => (
              <th key={h} className="text-ink-faint font-normal w-7 text-center">
                {h.toString().padStart(2, "0")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {WEEKDAYS.map((wd, wdIdx) => (
            <tr key={wd}>
              <td className="text-ink-faint pr-2 font-medium">{wd}</td>
              {Array.from({ length: 24 }, (_, h) => {
                const cell = grid.out[wdIdx][h];
                const intensity = grid.maxV > 0 ? cell.v / grid.maxV : 0;
                const bgColor =
                  metric === "pickup"
                    ? `rgba(16, 185, 129, ${0.05 + intensity * 0.85})`
                    : `rgba(239, 68, 68, ${0.05 + intensity * 0.85})`;
                const label = cell.raw
                  ? `${WEEKDAYS[wdIdx]} ${h}:00 — ${cell.raw.dials} dials, ${cell.raw.connects} connects, ${cell.raw.hots} hot (${cell.v.toFixed(0)}%)`
                  : `${WEEKDAYS[wdIdx]} ${h}:00 — no activity`;
                return (
                  <td
                    key={h}
                    title={label}
                    style={{ backgroundColor: bgColor }}
                    className="w-7 h-7 rounded text-center text-[9px] text-ink-dim"
                  >
                    {cell.v > 0 ? cell.v.toFixed(0) : ""}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AnalyticsPage() {
  return (
    <RequireAdmin>
      <AnalyticsHubInner />
    </RequireAdmin>
  );
}
