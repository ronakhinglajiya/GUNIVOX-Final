"use client";

/**
 * Lead detail page — sticky lead header on top + a CRM/Excel-style
 * collapsible timeline beneath it.
 *
 * Timeline design (2026-06-11 redesign):
 *  • Rows are SINGLE-LINE — chevron · icon · title · time · actor pill.
 *  • Rows GROUP by IST date — each date group has its own header you can
 *    collapse to hide a whole day. Today + Yesterday + then ISO dates.
 *  • Click a row → it expands INLINE with a "box on the front" detail
 *    panel. AI call rows lazy-fetch the transcript on first expand.
 *    Counsellor notes (parsed out of `lead.notes`) expand to full text
 *    with timestamp + author shown clearly.
 *
 * Data sources unchanged: GET /api/leads/{id} + /api/leads/{id}/timeline
 * (+ GET /api/calls/{id} on demand for transcripts).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Activity,
  ArrowLeft,
  Bot,
  CalendarClock,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Clock,
  ExternalLink,
  Flame,
  Loader2,
  Lock,
  LockOpen,
  PenLine,
  Phone,
  RefreshCw,
  Save,
  Shield,
  Snowflake,
  Thermometer,
  User as UserIcon,
} from "lucide-react";
import clsx from "clsx";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/lib/toast-context";
import { maskPhone } from "@/lib/phone";

// ─────────────── types ───────────────
interface Lead {
  id: number;
  name: string | null;
  phone: string;
  email: string | null;
  program_interest: string | null;
  status: string;
  dnd: boolean;
  attempt_count: number;
  notes: string | null;
  last_attempted_at: string | null;
  lead_temperature?: "hot" | "warm" | "cold" | null;
  last_disposition?: string | null;
  last_disposition_at?: string | null;
}

interface TimelineEvent {
  id: number;
  event_type: string;
  actor_kind: "system" | "ai" | "counsellor" | "admin";
  actor_user_id: number | null;
  actor_name: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
}

interface TimelineResponse {
  lead_id: number;
  events: TimelineEvent[];
}

interface TranscriptTurn {
  id: number;
  turn_index: number;
  role: string;
  text: string | null;
}

interface CallDetail {
  id: number;
  duration_s: number | null;
  outcome: string | null;
  lead_temperature: string | null;
  lead_program: string | null;
  lead_reasons: string | null;
  turns: TranscriptTurn[];
}

interface ParsedNote {
  timestamp: Date;
  author: string;
  text: string;
}

// Parsed counsellor notes are merged with events into one stream.
type Row =
  | { kind: "event"; key: string; ts: string; ev: TimelineEvent }
  | { kind: "note"; key: string; ts: string; note: ParsedNote };

// ─────────────── helpers ───────────────
function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function fmtAbs(iso: string): string {
  return new Date(iso).toLocaleString();
}
function fmtRel(iso: string, nowMs: number): string {
  const sec = Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 1000));
  if (sec < 30) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// Group key + display label keyed off the IST calendar day the event landed on.
// We compute everything in IST by shifting +05:30 before taking date components.
function istDayKey(iso: string): { key: string; label: string } {
  const tsMs = new Date(iso).getTime() + 5.5 * 60 * 60 * 1000;
  const d = new Date(tsMs);
  const y = d.getUTCFullYear();
  const m = (d.getUTCMonth() + 1).toString().padStart(2, "0");
  const day = d.getUTCDate().toString().padStart(2, "0");
  const key = `${y}-${m}-${day}`;

  const nowIstMs = Date.now() + 5.5 * 60 * 60 * 1000;
  const nd = new Date(nowIstMs);
  const todayKey = `${nd.getUTCFullYear()}-${(nd.getUTCMonth() + 1)
    .toString()
    .padStart(2, "0")}-${nd.getUTCDate().toString().padStart(2, "0")}`;
  const yesterdayMs = nowIstMs - 86_400_000;
  const yd = new Date(yesterdayMs);
  const yesterdayKey = `${yd.getUTCFullYear()}-${(yd.getUTCMonth() + 1)
    .toString()
    .padStart(2, "0")}-${yd.getUTCDate().toString().padStart(2, "0")}`;

  let label = key;
  if (key === todayKey) label = "Today";
  else if (key === yesterdayKey) label = "Yesterday";
  else {
    // Pretty label: "Wed 11 Jun"
    label = new Date(tsMs).toLocaleDateString("en-IN", {
      weekday: "short", day: "2-digit", month: "short",
    });
  }
  return { key, label };
}

const EVENT_META: Record<
  string,
  { icon: React.ComponentType<{ className?: string }>; label: string; tint: string }
> = {
  ai_call_started: { icon: Bot, label: "AI call started", tint: "text-sky-400" },
  ai_call_ended: { icon: Bot, label: "AI call ended", tint: "text-sky-400" },
  temperature_set: { icon: Flame, label: "Classified", tint: "text-amber-400" },
  lock_acquired: { icon: Lock, label: "Lead claimed", tint: "text-brand" },
  lock_extended: { icon: Clock, label: "Lock extended", tint: "text-brand" },
  lock_expired: { icon: LockOpen, label: "Lock expired", tint: "text-zinc-400" },
  lock_released: { icon: CheckCheck, label: "Closed out", tint: "text-emerald-400" },
  manual_call_placed: { icon: Phone, label: "Manual call placed", tint: "text-emerald-400" },
  disposition_set: { icon: CircleDot, label: "Disposition set", tint: "text-brand" },
  notes_updated: { icon: PenLine, label: "Note added", tint: "text-amber-400" },
  callback_scheduled: { icon: CalendarClock, label: "Callback scheduled", tint: "text-emerald-400" },
  funnel_stage_changed: { icon: Activity, label: "Stage advanced", tint: "text-emerald-400" },
  reassigned: { icon: Shield, label: "Reassigned", tint: "text-amber-400" },
  temperature_overridden: { icon: Thermometer, label: "Temperature overridden", tint: "text-amber-400" },
  dnd_set: { icon: Shield, label: "Added to DNC", tint: "text-danger" },
};

function eventDescription(ev: TimelineEvent): string {
  const p = ev.payload || {};
  switch (ev.event_type) {
    case "temperature_set":
      return `${p.from ?? "—"} → ${p.to}${p.program ? ` · ${p.program}` : ""}`;
    case "lock_acquired":
      return `Held for ${p.duration_minutes ?? "—"} min`;
    case "lock_extended":
      return `Extension #${p.extension_n}`;
    case "lock_released":
      return `${((p.disposition as string) || "—").replace(/_/g, " ")}`;
    case "disposition_set":
      return ((p.disposition as string) || "").replace(/_/g, " ");
    case "callback_scheduled":
      return p.scheduled_at ? new Date(p.scheduled_at as string).toLocaleString() : "—";
    case "ai_call_ended":
      return [
        p.outcome ? `${p.outcome}` : null,
        p.duration_s ? `${Math.round(p.duration_s as number)}s` : null,
        p.temperature ? `${p.temperature}` : null,
      ].filter(Boolean).join(" · ");
    case "funnel_stage_changed":
      return `${p.from} → ${p.to}`;
    default:
      return "";
  }
}

const TEMP_TINT: Record<string, string> = {
  hot: "text-amber-400",
  warm: "text-emerald-400",
  cold: "text-sky-400",
};
const TEMP_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  hot: Flame, warm: Thermometer, cold: Snowflake,
};

// Parse Lead.notes (one big string built by inbox.unlock with
// `[YYYY-MM-DD HH:MM UTC] {user}:\n{text}` markers per stanza) into
// individual entries so they can be shown as their own rows.
function parseNotesBlob(raw: string | null): ParsedNote[] {
  if (!raw || !raw.trim()) return [];
  const header = /\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\]\s*([^\n:]+):/g;
  const hits: Array<{ index: number; ts: Date; author: string; bodyStart: number }> = [];
  let m: RegExpExecArray | null;
  while ((m = header.exec(raw)) !== null) {
    hits.push({
      index: m.index,
      ts: new Date(m[1].replace(" ", "T") + "Z"),
      author: m[2].trim(),
      bodyStart: header.lastIndex,
    });
  }
  if (hits.length === 0) {
    return [{ timestamp: new Date(0), author: "—", text: raw.trim() }];
  }
  const out: ParsedNote[] = [];
  for (let i = 0; i < hits.length; i++) {
    const start = hits[i].bodyStart;
    const end = i + 1 < hits.length ? hits[i + 1].index : raw.length;
    const text = raw.slice(start, end).trim();
    if (text) out.push({ timestamp: hits[i].ts, author: hits[i].author, text });
  }
  return out;
}

// ─────────────── page ───────────────
export default function LeadDetailPage() {
  const params = useParams<{ id: string }>();
  const id = Number(params.id);
  const { isAdmin } = useAuth();

  const [lead, setLead] = useState<Lead | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [l, t] = await Promise.all([
        api.get<Lead>(`/api/leads/${id}`),
        api.get<TimelineResponse>(`/api/leads/${id}/timeline`),
      ]);
      setLead(l);
      setTimeline(t.events);
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to load lead");
    } finally {
      setBusy(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  const now = Date.now();

  return (
    <div className="max-w-5xl mx-auto space-y-4 pb-10">
      {/* Top toolbar — back + refresh, always above the fold */}
      <div className="flex items-center justify-between">
        <Link href="/leads" className="btn-ghost text-sm">
          <ArrowLeft className="h-4 w-4" />
          Back to leads
        </Link>
        <button onClick={load} disabled={busy} className="btn-ghost text-sm">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Refresh
        </button>
      </div>

      {err && (
        <div className="card p-4 text-sm text-danger bg-danger/5 border-danger/30">{err}</div>
      )}

      {busy && !lead ? (
        <div className="card p-8 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-brand" />
        </div>
      ) : lead ? (
        <>
          {/* Sticky lead header — stays visible while you scroll the timeline. */}
          <LeadHeaderCard lead={lead} isAdmin={isAdmin} nowMs={now} />

          {/* Last AI conversation — always-on transcript view of the most
              recent AI call. The counsellor reads what was said, then
              writes their own notes in the "Add a note" box below. */}
          <LastAITranscriptPanel events={timeline} />

          {/* Add a note — inline editor that POSTs to the new
              /api/leads/{id}/notes endpoint and refreshes the timeline. */}
          <AddNotePanel leadId={lead.id} onSaved={load} />

          {/* CRM/Excel-style collapsible timeline */}
          <CrmTimeline
            leadId={lead.id}
            lead={lead}
            events={timeline}
            nowMs={now}
          />
        </>
      ) : null}
    </div>
  );
}

// Find the most recent event that carries a call_id in its payload.
// Most calls write multiple events (ai_call_ended, temperature_set, ...).
// Older calls (pre-Phase-1) only have `temperature_set` with a call_id,
// so we look at ALL events, not just `ai_call_ended`.
function findLatestCallId(events: TimelineEvent[] | null): number | null {
  if (!events) return null;
  for (const e of events) {
    const cid = e.payload?.call_id;
    if (cid != null) {
      const n = Number(cid);
      if (!Number.isNaN(n) && n > 0) return n;
    }
  }
  return null;
}

// ─────────────── Last AI conversation panel ───────────────
function LastAITranscriptPanel({
  events,
}: {
  events: TimelineEvent[] | null;
}) {
  const latestAiCallId = useMemo(() => findLatestCallId(events), [events]);

  const [detail, setDetail] = useState<CallDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (latestAiCallId == null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setErr(null);
    api.get<CallDetail>(`/api/calls/${latestAiCallId}`)
      .then((d) => {
        if (!cancelled) setDetail(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setErr(e instanceof Error ? e.message : "Failed to load");
      })
      .finally(() => {
        if (!cancelled) setBusy(false);
      });
    return () => { cancelled = true; };
  }, [latestAiCallId]);

  if (latestAiCallId == null) return null;

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
          <Bot className="h-4 w-4 text-sky-400" />
          Last AI conversation
        </h2>
        <Link
          href={`/calls/${latestAiCallId}`}
          className="text-[11px] text-brand hover:underline flex items-center gap-1"
        >
          Open call detail
          <ExternalLink className="h-3 w-3" />
        </Link>
      </div>
      <div className="p-4">
        {busy ? (
          <div className="flex items-center gap-2 text-xs text-ink-faint">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Loading transcript…
          </div>
        ) : err ? (
          <div className="text-xs text-danger">Couldn’t load transcript: {err}</div>
        ) : detail ? (
          <>
            <div className="flex items-center gap-3 flex-wrap text-[11px] text-ink-faint mb-3">
              <span>
                Outcome:{" "}
                <span className="text-ink font-medium">{detail.outcome ?? "—"}</span>
              </span>
              {detail.duration_s != null && (
                <span>
                  Duration:{" "}
                  <span className="text-ink font-medium tabular-nums">
                    {Math.round(detail.duration_s)}s
                  </span>
                </span>
              )}
              {detail.lead_temperature && (
                <span>
                  Classified:{" "}
                  <span className="text-ink font-medium">{detail.lead_temperature}</span>
                </span>
              )}
              {detail.lead_program && (
                <span>
                  Program:{" "}
                  <span className="text-ink font-medium">{detail.lead_program}</span>
                </span>
              )}
            </div>
            {detail.lead_reasons && (
              <p className="text-[11px] text-ink-dim italic mb-3 border-l-2 border-amber-500/40 pl-2">
                Classifier note: {detail.lead_reasons}
              </p>
            )}
            {detail.turns.length === 0 ? (
              <p className="text-xs text-ink-faint italic">
                No transcript available — caller didn’t speak in this call.
              </p>
            ) : (
              <div className="bg-bg border border-border rounded p-3 max-h-96 overflow-y-auto space-y-1.5">
                {detail.turns.map((t) =>
                  t.text && t.text.trim() ? (
                    <p key={t.id} className="text-xs leading-relaxed">
                      <span
                        className={clsx(
                          "font-semibold mr-1",
                          t.role === "user" ? "text-sky-300" : "text-ink",
                        )}
                      >
                        {t.role === "user" ? "Caller:" : "Agent:"}
                      </span>
                      <span className="text-ink-dim">{t.text}</span>
                    </p>
                  ) : null,
                )}
              </div>
            )}
          </>
        ) : null}
      </div>
    </div>
  );
}

// ─────────────── Add a note panel ───────────────
function AddNotePanel({
  leadId,
  onSaved,
}: {
  leadId: number;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    const t = text.trim();
    if (!t) return;
    setSaving(true);
    try {
      await api.post(`/api/leads/${leadId}/notes`, { text: t });
      setText("");
      toast.success("Note saved", "Added to the lead's timeline.");
      onSaved();
    } catch (e: unknown) {
      toast.error("Save failed", e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
          <PenLine className="h-4 w-4 text-amber-400" />
          Add a note
        </h2>
        <span className="text-[10px] text-ink-faint">
          appended to the timeline with your name + timestamp
        </span>
      </div>
      <textarea
        className="input min-h-[80px]"
        placeholder="What did the caller say in your follow-up call? Next steps? Parent involvement?"
        value={text}
        onChange={(e) => setText(e.target.value)}
        disabled={saving}
      />
      <div className="flex items-center justify-between mt-2">
        <span className="text-[10px] text-ink-faint">
          {text.trim().length} character{text.trim().length === 1 ? "" : "s"}
        </span>
        <button
          type="button"
          onClick={save}
          disabled={!text.trim() || saving}
          className="btn-primary text-sm"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
          Save note
        </button>
      </div>
    </div>
  );
}

// ─────────────── lead header (sticky) ───────────────
function LeadHeaderCard({
  lead,
  isAdmin,
  nowMs,
}: {
  lead: Lead;
  isAdmin: boolean;
  nowMs: number;
}) {
  return (
    <div className="card p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold text-ink truncate">
            {lead.name || "Unnamed lead"}
          </h1>
          <p className="text-sm text-ink-faint mt-1 flex items-center gap-3 flex-wrap">
            <span className="flex items-center gap-1">
              <Phone className="h-3 w-3" />
              {maskPhone(lead.phone, isAdmin)}
            </span>
            {lead.email && <span>{lead.email}</span>}
            {lead.program_interest && <span>· {lead.program_interest}</span>}
          </p>
        </div>
        {lead.lead_temperature && TEMP_ICON[lead.lead_temperature] && (
          <span
            className={clsx(
              "text-xs uppercase tracking-wider font-semibold px-2 py-1 rounded flex items-center gap-1",
              TEMP_TINT[lead.lead_temperature],
              "bg-bg border border-border"
            )}
          >
            {(() => {
              const Icon = TEMP_ICON[lead.lead_temperature!];
              return <Icon className="h-3 w-3" />;
            })()}
            {lead.lead_temperature}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-5 text-sm">
        <Stat label="Status" value={lead.dnd ? "DND" : lead.status} />
        <Stat label="Attempts" value={String(lead.attempt_count)} />
        <Stat
          label="Last call"
          value={lead.last_attempted_at ? fmtRel(lead.last_attempted_at, nowMs) : "—"}
        />
        <Stat
          label="Last disposition"
          value={lead.last_disposition ? lead.last_disposition.replace(/_/g, " ") : "—"}
        />
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-ink-faint">{label}</p>
      <p className="text-sm text-ink font-medium mt-1 truncate">{value}</p>
    </div>
  );
}

// ─────────────── CRM timeline ───────────────
function CrmTimeline({
  leadId,
  lead,
  events,
  nowMs,
}: {
  leadId: number;
  lead: Lead;
  events: TimelineEvent[] | null;
  nowMs: number;
}) {
  // Expanded row keys.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Collapsed date-group keys (default: all expanded).
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  // Lazy-fetched call transcripts.
  const [transcripts, setTranscripts] = useState<Map<number, CallDetail | "loading" | "error">>(new Map());

  const toggleRow = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const toggleGroup = (key: string) =>
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const fetchTranscript = useCallback(async (callId: number) => {
    setTranscripts((prev) => {
      if (prev.has(callId)) return prev;
      const next = new Map(prev);
      next.set(callId, "loading");
      return next;
    });
    try {
      const detail = await api.get<CallDetail>(`/api/calls/${callId}`);
      setTranscripts((prev) => new Map(prev).set(callId, detail));
    } catch {
      setTranscripts((prev) => new Map(prev).set(callId, "error"));
    }
  }, []);

  // Merge events + parsed notes, drop the redundant notes_updated events,
  // group by IST date, sort everything newest first.
  const groups = useMemo(() => {
    const evRows: Row[] = (events ?? [])
      .filter((ev) => ev.event_type !== "notes_updated")
      .map((ev) => ({ kind: "event", key: `ev-${ev.id}`, ts: ev.created_at, ev }));
    const noteRows: Row[] = parseNotesBlob(lead.notes).map((n, i) => ({
      kind: "note",
      key: `note-${i}-${n.timestamp.getTime()}`,
      ts: n.timestamp.toISOString(),
      note: n,
    }));
    const all = [...evRows, ...noteRows].sort(
      (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
    );

    const grouped: Array<{ key: string; label: string; rows: Row[] }> = [];
    for (const row of all) {
      const { key, label } = istDayKey(row.ts);
      const last = grouped[grouped.length - 1];
      if (last && last.key === key) {
        last.rows.push(row);
      } else {
        grouped.push({ key, label, rows: [row] });
      }
    }
    return grouped;
  }, [events, lead.notes]);

  const totalRows = groups.reduce((s, g) => s + g.rows.length, 0);

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink flex items-center gap-2">
          <Activity className="h-4 w-4" />
          Timeline
        </h2>
        <div className="flex items-center gap-3 text-xs text-ink-faint">
          <span>{totalRows} entr{totalRows === 1 ? "y" : "ies"}</span>
          {collapsedGroups.size > 0 ? (
            <button
              type="button"
              onClick={() => setCollapsedGroups(new Set())}
              className="text-brand hover:underline"
            >
              Expand all
            </button>
          ) : (
            <button
              type="button"
              onClick={() =>
                setCollapsedGroups(new Set(groups.map((g) => g.key)))
              }
              className="text-brand hover:underline"
            >
              Collapse all
            </button>
          )}
        </div>
      </div>

      {totalRows === 0 ? (
        <div className="px-4 py-10 text-sm text-ink-faint text-center">
          No events yet — once the AI dials this lead or a counsellor opens it,
          every action will land here.
        </div>
      ) : (
        <div>
          {groups.map((group) => {
            const isCollapsed = collapsedGroups.has(group.key);
            return (
              <div key={group.key} className="border-b border-border last:border-b-0">
                {/* Date-group header (Excel-style group) */}
                <button
                  type="button"
                  onClick={() => toggleGroup(group.key)}
                  className="w-full px-4 py-2 flex items-center gap-2 bg-surface hover:bg-hover text-xs uppercase tracking-wider text-ink-dim font-semibold"
                >
                  {isCollapsed ? (
                    <ChevronRight className="h-3 w-3" />
                  ) : (
                    <ChevronDown className="h-3 w-3" />
                  )}
                  <span>{group.label}</span>
                  <span className="text-ink-faint font-normal normal-case">
                    · {group.rows.length} entr{group.rows.length === 1 ? "y" : "ies"}
                  </span>
                </button>

                {!isCollapsed && (
                  <ol className="divide-y divide-border">
                    {group.rows.map((row) => (
                      <CrmRow
                        key={row.key}
                        row={row}
                        expanded={expanded.has(row.key)}
                        onToggle={() => {
                          toggleRow(row.key);
                          // Lazy-fetch the transcript whenever an event
                          // row that carries a call_id gets expanded —
                          // ai_call_ended, temperature_set, anything.
                          if (
                            row.kind === "event" &&
                            row.ev.payload?.call_id
                          ) {
                            const cid = Number(row.ev.payload.call_id);
                            if (!Number.isNaN(cid) && !transcripts.has(cid)) {
                              fetchTranscript(cid);
                            }
                          }
                        }}
                        transcripts={transcripts}
                        nowMs={nowMs}
                      />
                    ))}
                  </ol>
                )}
              </div>
            );
          })}
        </div>
      )}

      <div className="px-4 py-2 border-t border-border text-[10px] text-ink-faint flex items-center justify-end">
        Lead #{leadId} · times shown IST
      </div>
    </div>
  );
}

// ─────────────── CRM row (single line + inline expand) ───────────────
function CrmRow({
  row,
  expanded,
  onToggle,
  transcripts,
  nowMs,
}: {
  row: Row;
  expanded: boolean;
  onToggle: () => void;
  transcripts: Map<number, CallDetail | "loading" | "error">;
  nowMs: number;
}) {
  let icon: React.ComponentType<{ className?: string }>;
  let iconTint: string;
  let title: string;
  let blurb: string;
  let actorPill: string | null = null;
  let canExpand = false;
  let absoluteTs: string;

  if (row.kind === "note") {
    icon = PenLine;
    iconTint = "text-amber-400";
    title = "Note";
    blurb =
      row.note.text.length > 90 ? row.note.text.slice(0, 90) + "…" : row.note.text;
    actorPill = row.note.author;
    canExpand = true;
    absoluteTs = row.note.timestamp.toLocaleString();
  } else {
    const ev = row.ev;
    const meta = EVENT_META[ev.event_type] ?? {
      icon: CircleDot,
      label: ev.event_type.replace(/_/g, " "),
      tint: "text-ink-dim",
    };
    icon = meta.icon;
    iconTint = meta.tint;
    title = meta.label;
    blurb = eventDescription(ev);
    actorPill =
      ev.actor_kind === "system"
        ? null
        : ev.actor_kind + (ev.actor_name ? ` · ${ev.actor_name}` : "");
    // Any event that points at a call_id can expand to the transcript —
    // ai_call_ended is the canonical one but older calls only have a
    // temperature_set event with the call_id, so allow both.
    canExpand =
      !!ev.payload?.call_id ||
      blurb.length > 60;
    absoluteTs = fmtAbs(ev.created_at);
  }

  const Chevron = expanded ? ChevronDown : ChevronRight;
  const Icon = icon;

  return (
    <li className="text-xs">
      <button
        type="button"
        onClick={canExpand ? onToggle : undefined}
        className={clsx(
          "w-full px-3 py-2 flex items-center gap-2 text-left",
          canExpand && "hover:bg-hover cursor-pointer",
        )}
      >
        <div className="shrink-0 w-3">
          {canExpand && <Chevron className="h-3 w-3 text-ink-faint" />}
        </div>
        <Icon className={clsx("h-3.5 w-3.5 shrink-0", iconTint)} />
        <div className="min-w-0 flex-1 flex items-center gap-2">
          <span className="text-ink font-medium shrink-0">{title}</span>
          {blurb && (
            <span className="text-[11px] text-ink-dim truncate">{blurb}</span>
          )}
        </div>
        {actorPill && (
          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-bg border border-border text-ink-dim shrink-0">
            {actorPill}
          </span>
        )}
        <span
          className="text-[10px] text-ink-faint tabular-nums shrink-0 w-16 text-right"
          title={absoluteTs}
        >
          {fmtTime(row.ts)}
        </span>
        <span className="text-[10px] text-ink-faint tabular-nums shrink-0 w-16 text-right">
          {fmtRel(row.ts, nowMs)}
        </span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 pt-1 bg-surface/40">
          {row.kind === "note" ? (
            <div className="ml-8 max-w-3xl bg-bg border border-border rounded p-3">
              <p className="text-[10px] uppercase tracking-wider text-ink-faint mb-1.5">
                {row.note.author} · {row.note.timestamp.toLocaleString()}
              </p>
              <pre className="text-xs text-ink whitespace-pre-wrap leading-relaxed font-sans">
                {row.note.text}
              </pre>
            </div>
          ) : row.ev.payload?.call_id ? (
            // Any event tied to a call_id — render the transcript inline.
            // Works for ai_call_ended, temperature_set, or anything else
            // whose payload carries `call_id`.
            <div className="ml-8">
              <TranscriptInline
                callId={Number(row.ev.payload.call_id)}
                transcripts={transcripts}
              />
              <Link
                href={`/calls/${row.ev.payload.call_id}`}
                className="text-[11px] text-brand hover:underline mt-2 inline-flex items-center gap-1"
              >
                <ExternalLink className="h-3 w-3" />
                Open full call detail
              </Link>
            </div>
          ) : (
            <div className="ml-8 bg-bg border border-border rounded p-3 max-w-3xl">
              <p className="text-[10px] uppercase tracking-wider text-ink-faint mb-1">
                Payload
              </p>
              <pre className="text-[11px] text-ink-dim whitespace-pre-wrap break-all">
                {JSON.stringify(row.ev.payload ?? {}, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

function TranscriptInline({
  callId,
  transcripts,
}: {
  callId: number;
  transcripts: Map<number, CallDetail | "loading" | "error">;
}) {
  const state = transcripts.get(callId);
  if (state === undefined || state === "loading") {
    return (
      <div className="flex items-center gap-2 text-[11px] text-ink-faint p-2">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading transcript…
      </div>
    );
  }
  if (state === "error") {
    return (
      <div className="text-[11px] text-danger p-2">
        Couldn’t load transcript for call #{callId}.
      </div>
    );
  }
  if (state.turns.length === 0) {
    return (
      <div className="text-[11px] text-ink-faint italic p-2">
        No transcript available — caller didn’t speak.
      </div>
    );
  }
  return (
    <div className="bg-bg border border-border rounded p-3 max-w-3xl">
      {state.lead_reasons && (
        <p className="text-[11px] text-ink-dim italic mb-2">
          Classifier note: {state.lead_reasons}
        </p>
      )}
      <div className="max-h-72 overflow-y-auto space-y-1 pr-1">
        {state.turns.map((t) =>
          t.text && t.text.trim() ? (
            <p key={t.id} className="text-[11px] leading-snug">
              <span
                className={clsx(
                  "font-semibold",
                  t.role === "user" ? "text-sky-300" : "text-ink",
                )}
              >
                {t.role === "user" ? "Caller" : "Agent"}:
              </span>{" "}
              <span className="text-ink-dim">{t.text}</span>
            </p>
          ) : null,
        )}
      </div>
    </div>
  );
}
