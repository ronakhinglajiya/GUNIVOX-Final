"use client";

/**
 * Team — admin-only presence + today's stats for every user.
 *
 * Polls /api/admin/team every 10s for live presence. Rendering:
 *   • Status dot — green (online, <2 min) / amber (away, 2-15 min) /
 *     gray (offline) / hollow (never signed in).
 *   • Last seen — relative ("2m ago", "1h ago", "just now").
 *   • Today's call count + total talk time (IST calendar day).
 *   • "On a call" pill if any of their calls has ended_at IS NULL.
 *
 * Admins themselves are listed too — so the admin can see "yes, I'm
 * the green dot" — but counsellor rows are visually emphasised since
 * those are the ones the admin is monitoring.
 */
import { useCallback, useEffect, useState } from "react";
import {
  UsersRound,
  Loader2,
  RefreshCw,
  Circle,
  PhoneCall,
  Clock,
  Activity,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { RequireAdmin } from "@/components/require-admin";

interface TeamMember {
  id: number;
  name: string;
  email: string;
  role: string;
  is_active: boolean;
  last_seen_at: string | null;
  presence: "online" | "away" | "offline" | "never";
  calls_today: number;
  duration_today_sec: number;
  on_call_now: boolean;
}

interface TeamResponse {
  members: TeamMember[];
  server_time: string;
}

const PRESENCE_STYLES: Record<
  TeamMember["presence"],
  { dot: string; label: string; chipText: string }
> = {
  online: { dot: "bg-emerald-500", label: "Online", chipText: "text-emerald-400" },
  away: { dot: "bg-amber-500", label: "Away", chipText: "text-amber-400" },
  offline: { dot: "bg-zinc-500", label: "Offline", chipText: "text-zinc-400" },
  never: { dot: "bg-transparent border border-zinc-600", label: "Never signed in", chipText: "text-ink-faint" },
};

function relativeTime(iso: string | null, serverNow: string): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  const now = new Date(serverNow).getTime();
  const sec = Math.max(0, Math.floor((now - t) / 1000));
  if (sec < 30) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const days = Math.floor(hr / 24);
  return `${days}d ago`;
}

function fmtDuration(sec: number): string {
  if (!sec) return "0m";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${sec}s`;
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

function TeamPageInner() {
  const [data, setData] = useState<TeamResponse | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.get<TeamResponse>("/api/admin/team");
      setData(r);
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to load team");
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 10_000);
    return () => clearInterval(id);
  }, [load]);

  const members = data?.members ?? [];
  const serverNow = data?.server_time ?? new Date().toISOString();
  const counsellors = members.filter((m) => m.role === "counsellor");
  const admins = members.filter((m) => m.role === "admin");

  const onlineCount = members.filter((m) => m.presence === "online").length;
  const onCallCount = members.filter((m) => m.on_call_now).length;
  const totalCallsToday = members.reduce((s, m) => s + m.calls_today, 0);
  const totalDurToday = members.reduce((s, m) => s + m.duration_today_sec, 0);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink flex items-center gap-2.5">
            <UsersRound className="h-6 w-6 text-brand" />
            Team
          </h1>
          <p className="text-sm text-ink-faint mt-1">
            Live presence + today&apos;s activity across all signed-up users.
          </p>
        </div>
        <button
          onClick={load}
          disabled={busy}
          className="btn-ghost text-sm"
          title="Refresh now"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <RefreshCw className="h-4 w-4" />
          )}
          Refresh
        </button>
      </div>

      {/* Summary tiles */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <SummaryTile
          icon={<Circle className="h-4 w-4 fill-emerald-500 text-emerald-500" />}
          label="Online now"
          value={String(onlineCount)}
        />
        <SummaryTile
          icon={<Activity className="h-4 w-4 text-brand" />}
          label="On a call"
          value={String(onCallCount)}
        />
        <SummaryTile
          icon={<PhoneCall className="h-4 w-4 text-brand" />}
          label="Calls today"
          value={String(totalCallsToday)}
        />
        <SummaryTile
          icon={<Clock className="h-4 w-4 text-brand" />}
          label="Talk time today"
          value={fmtDuration(totalDurToday)}
        />
      </div>

      {err && (
        <div className="card p-4 text-sm text-danger bg-danger/5 border-danger/30">
          {err}
        </div>
      )}

      {busy && !data ? (
        <div className="card p-8 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-brand" />
        </div>
      ) : (
        <>
          <Section title="Counsellors" subtitle={`${counsellors.length} signed up`}>
            {counsellors.length === 0 ? (
              <EmptyRow text="No counsellor accounts yet — invite them to /signup." />
            ) : (
              counsellors.map((m) => (
                <MemberRow key={m.id} member={m} serverNow={serverNow} />
              ))
            )}
          </Section>

          <Section title="Admins" subtitle={`${admins.length} account${admins.length === 1 ? "" : "s"}`}>
            {admins.length === 0 ? (
              <EmptyRow text="No admin accounts." />
            ) : (
              admins.map((m) => (
                <MemberRow key={m.id} member={m} serverNow={serverNow} />
              ))
            )}
          </Section>
        </>
      )}
    </div>
  );
}

function SummaryTile({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center gap-2 text-xs text-ink-faint uppercase tracking-wide">
        {icon}
        {label}
      </div>
      <p className="text-2xl font-semibold text-ink mt-2 tabular-nums">{value}</p>
    </div>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-3 border-b border-border flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {subtitle && <span className="text-xs text-ink-faint">{subtitle}</span>}
      </div>
      <div className="divide-y divide-border">{children}</div>
    </div>
  );
}

function EmptyRow({ text }: { text: string }) {
  return <div className="px-4 py-6 text-sm text-ink-faint text-center">{text}</div>;
}

function MemberRow({ member, serverNow }: { member: TeamMember; serverNow: string }) {
  const p = PRESENCE_STYLES[member.presence];
  return (
    <div className="px-4 py-3 flex items-center gap-4">
      {/* Avatar */}
      <div className="relative shrink-0">
        <div className="h-10 w-10 rounded-full bg-brand-subtle text-brand flex items-center justify-center font-semibold text-sm">
          {initials(member.name)}
        </div>
        <span
          className={clsx(
            "absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full ring-2 ring-surface",
            p.dot
          )}
          title={p.label}
        />
      </div>

      {/* Name + email */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium text-ink truncate">{member.name}</p>
          {member.on_call_now && (
            <span className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded bg-brand-subtle text-brand">
              On a call
            </span>
          )}
          {!member.is_active && (
            <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-zinc-700/40 text-zinc-400">
              Disabled
            </span>
          )}
        </div>
        <p className="text-xs text-ink-faint truncate">{member.email}</p>
      </div>

      {/* Status */}
      <div className="hidden sm:block text-right">
        <p className={clsx("text-xs font-medium", p.chipText)}>{p.label}</p>
        <p className="text-[11px] text-ink-faint tabular-nums">
          {relativeTime(member.last_seen_at, serverNow)}
        </p>
      </div>

      {/* Calls today */}
      <div className="text-right w-20">
        <p className="text-sm font-semibold text-ink tabular-nums">
          {member.calls_today}
        </p>
        <p className="text-[10px] text-ink-faint uppercase tracking-wide">calls</p>
      </div>

      {/* Talk time today */}
      <div className="text-right w-20">
        <p className="text-sm font-semibold text-ink tabular-nums">
          {fmtDuration(member.duration_today_sec)}
        </p>
        <p className="text-[10px] text-ink-faint uppercase tracking-wide">talk</p>
      </div>
    </div>
  );
}

export default function TeamPage() {
  return (
    <RequireAdmin>
      <TeamPageInner />
    </RequireAdmin>
  );
}
