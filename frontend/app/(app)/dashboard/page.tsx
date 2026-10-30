"use client";

/**
 * Dashboard — redesigned 2026-05-19 to actually surface the work the
 * backend now does (lead-temperature classifier output, programs
 * discussed, caller names).
 *
 * Layout, top to bottom:
 *   1. Title row with live-event indicator
 *   2. Four hero stat tiles (leads, calls, callbacks, DND)
 *   3. Three BIG temperature tiles (HOT / WARM / COLD) with mini bars
 *   4. Recent calls (enriched with caller name + program + temperature pill)
 *
 * No backend changes — all data from existing endpoints.
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { parseServerDate, fmtISTDate } from "@/lib/datetime";
import { useLiveEvents, type LiveEvent } from "@/lib/live";
import type { Call, DashboardStats, CallsList } from "@/lib/types";
import {
  Users,
  PhoneCall,
  CalendarClock,
  ShieldOff,
  ChevronRight,
  Radio,
  Flame,
  ThermometerSun,
  Snowflake,
  Activity,
} from "lucide-react";
import Link from "next/link";

function fmtDuration(s?: number | null): string {
  if (!s) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

function timeAgo(iso?: string | null): string {
  const d = parseServerDate(iso);
  if (!d) return "—";
  const sec = (Date.now() - d.getTime()) / 1000;
  if (sec < 60) return `${Math.floor(sec)}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return fmtISTDate(iso);
}

function HeroStat({
  label,
  value,
  icon: Icon,
  hint,
  loading,
}: {
  label: string;
  value: string | number;
  icon: React.ComponentType<{ className?: string }>;
  hint?: string;
  loading?: boolean;
}) {
  return (
    <div className="tile">
      <div className="flex items-center justify-between mb-2">
        <p className="label">{label}</p>
        <Icon className="h-4 w-4 text-ink-faint" />
      </div>
      {loading ? (
        <div className="skeleton h-9 w-20" />
      ) : (
        <p className="text-3xl font-semibold text-ink font-mono tabular-nums">{value}</p>
      )}
      {hint && <p className="text-xs text-ink-faint mt-1">{hint}</p>}
    </div>
  );
}

function TempTile({
  kind,
  label,
  count,
  total,
  icon: Icon,
}: {
  kind: "hot" | "warm" | "cold";
  label: string;
  count: number;
  total: number;
  icon: React.ComponentType<{ className?: string }>;
}) {
  const pct = total > 0 ? (count / total) * 100 : 0;
  const cls = `tile tile-${kind}`;
  const barCls =
    kind === "hot"
      ? "bg-gradient-to-r from-rose-600 to-rose-400"
      : kind === "warm"
      ? "bg-gradient-to-r from-amber-500 to-amber-300"
      : "bg-gradient-to-r from-sky-500 to-sky-300";
  return (
    <Link href={`/pipeline`} className={cls}>
      <div className="tile-accent" />
      <div className="flex items-start justify-between mb-3 pl-2">
        <div>
          <p className="label">{label}</p>
          <p className="text-[10px] text-ink-faint mt-0.5">classified by AI</p>
        </div>
        <Icon
          className={
            kind === "hot"
              ? "h-5 w-5 text-rose-500"
              : kind === "warm"
              ? "h-5 w-5 text-amber-500"
              : "h-5 w-5 text-sky-500"
          }
        />
      </div>
      <div className="flex items-baseline justify-between pl-2 mb-3">
        <span className="text-4xl font-semibold font-mono text-ink tabular-nums">{count}</span>
        <span className="text-xs text-ink-faint font-mono">{pct.toFixed(0)}% of total</span>
      </div>
      <div className="h-1.5 bg-hover rounded-full overflow-hidden ml-2">
        <div
          className={`h-full ${barCls} transition-all duration-500`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </Link>
  );
}

function TempPill({ t }: { t?: string | null }) {
  if (!t) return <span className="badge-muted">—</span>;
  const cls = t === "hot" ? "pill-hot" : t === "warm" ? "pill-warm" : t === "cold" ? "pill-cold" : "badge-muted";
  return <span className={cls}>{t}</span>;
}

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [recent, setRecent] = useState<Call[]>([]);
  const [liveBlip, setLiveBlip] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([
        api.get<DashboardStats>("/api/dashboard/stats"),
        api.get<CallsList>("/api/calls?limit=8&offset=0"),
      ]);
      setStats(s);
      setRecent(c.items);
    } catch {
      /* topbar shows status */
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const { status: liveStatus } = useLiveEvents((e: LiveEvent) => {
    if (e.type === "ping" || e.type === "hello") return;
    setLiveBlip(true);
    setTimeout(() => setLiveBlip(false), 600);
    if (
      e.type === "call.queued" ||
      e.type === "call.ended" ||
      e.type === "call.turn" ||
      e.type === "lead.created"
    ) {
      refresh();
    }
  });

  const temps = stats?.lead_temperature ?? { hot: 0, warm: 0, cold: 0 };
  const classifiedTotal = temps.hot + temps.warm + temps.cold;

  return (
    <div className="space-y-6">
      {/* Title + live indicator */}
      <div className="flex items-end justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-ink tracking-tight">Dashboard</h1>
          <p className="text-sm text-ink-dim mt-1 flex items-center gap-2">
            <span>Live admissions outreach overview</span>
            <span
              className={
                liveStatus === "open"
                  ? "inline-flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-success font-mono"
                  : "inline-flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-ink-faint font-mono"
              }
            >
              <Radio className={`h-3 w-3 ${liveBlip ? "text-brand animate-pulse" : ""}`} />
              {liveStatus === "open" ? "live" : liveStatus}
            </span>
          </p>
        </div>
        <Link href="/pipeline" className="btn-outline text-sm">
          <Activity className="h-4 w-4" /> View pipeline
        </Link>
      </div>

      {/* Hero stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <HeroStat label="Total leads" value={stats?.total_leads ?? "—"} icon={Users} hint="in pipeline" loading={!stats} />
        <HeroStat label="Total calls" value={stats?.total_calls ?? "—"} icon={PhoneCall} hint="all time" loading={!stats} />
        <HeroStat label="Pending callbacks" value={stats?.pending_callbacks ?? "—"} icon={CalendarClock} loading={!stats} />
        <HeroStat label="On DND" value={stats?.dnd_count ?? "—"} icon={ShieldOff} loading={!stats} />
      </div>

      {/* Lead temperature tiles — the headline metric */}
      <div>
        <div className="flex items-end justify-between mb-3">
          <h2 className="text-sm font-semibold text-ink uppercase tracking-widest">
            Lead temperature
          </h2>
          <span className="text-[11px] text-ink-faint font-mono">
            {classifiedTotal} classified · tap any tile for pipeline view
          </span>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <TempTile kind="hot"  label="Hot leads"  count={temps.hot}  total={classifiedTotal} icon={Flame} />
          <TempTile kind="warm" label="Warm leads" count={temps.warm} total={classifiedTotal} icon={ThermometerSun} />
          <TempTile kind="cold" label="Cold leads" count={temps.cold} total={classifiedTotal} icon={Snowflake} />
        </div>
      </div>

      {/* Recent calls */}
      <div className="card">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h2 className="font-semibold">Recent calls</h2>
          <Link
            href="/calls"
            className="text-xs text-brand hover:underline inline-flex items-center gap-1 font-medium"
          >
            View all <ChevronRight className="h-3 w-3" />
          </Link>
        </div>
        {!stats ? (
          <div className="p-4 space-y-2">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton h-10 w-full" />
            ))}
          </div>
        ) : recent.length === 0 ? (
          <div className="p-10 text-center text-ink-dim text-sm">
            <PhoneCall className="h-8 w-8 mx-auto mb-2 text-ink-faint" />
            <p>No calls yet.</p>
            <p className="text-xs text-ink-faint mt-1">
              Use the dialer on the right to start a call.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-border">
            {recent.map((c) => (
              <Link
                key={c.id}
                href={`/calls/${c.id}`}
                className="grid grid-cols-12 items-center gap-3 px-4 py-3 hover:bg-hover transition-colors text-sm"
              >
                <div className="col-span-1 font-mono text-xs text-ink-faint">#{c.id}</div>
                <div className="col-span-3 truncate">
                  <p className="font-medium text-ink truncate">
                    {c.caller_name || `Lead #${c.lead_id}`}
                  </p>
                  {c.lead_program && (
                    <p className="text-[11px] text-ink-faint truncate">{c.lead_program}</p>
                  )}
                </div>
                <div className="col-span-3 text-[11px] text-ink-dim truncate">
                  {c.lead_reasons || "—"}
                </div>
                <div className="col-span-2 flex items-center gap-2">
                  <TempPill t={c.lead_temperature} />
                </div>
                <div className="col-span-1 font-mono text-xs text-ink-dim text-right">
                  {fmtDuration(c.duration_s)}
                </div>
                <div className="col-span-1 text-[11px] text-ink-faint text-right font-mono">
                  {timeAgo(c.started_at)}
                </div>
                <div className="col-span-1 text-right">
                  <ChevronRight className="h-4 w-4 text-ink-faint inline" />
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
