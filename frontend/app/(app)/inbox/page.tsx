"use client";

/**
 * Inbox — counsellor's kanban for AI-classified leads.
 *
 * 4 columns: [🔥 Locked by me] [🔥 Hot] [⚠️ Warm] [❄️ Cold]
 *
 * Card click flow:
 *   1. POST /api/leads/{id}/lock  → on 200 open modal, on 409 toast
 *      "locked by Keval until 14:35"
 *   2. Modal shows live countdown, "+5 min" button (capped 2), notes
 *      textarea, disposition <select>, optional callback datetime,
 *      "Save & Release" button.
 *   3. Save calls POST /api/leads/{id}/unlock with body — backend
 *      validates required fields per disposition.
 *
 * Polls /api/inbox every 5s for live state. Hot SLA badge (red) shows
 * if hot_since is more than 30 min ago and the card is still unlocked.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  Inbox as InboxIcon,
  Flame,
  Thermometer,
  Snowflake,
  Lock,
  Loader2,
  RefreshCw,
  Phone,
  Clock,
  AlertTriangle,
  X,
  PlusCircle,
  Activity,
  Bot,
  Calendar,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  PenLine,
  CalendarClock,
  CircleDot,
  Trash2,
  Shield,
  User as UserIcon,
  LockOpen,
} from "lucide-react";
import clsx from "clsx";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useInboxNotifier } from "@/lib/inbox-notifications";
import { useToast } from "@/lib/toast-context";
import { maskPhone } from "@/lib/phone";

// ─────────────── types ───────────────
interface LeadCard {
  id: number;
  name: string | null;
  phone: string;
  program_interest: string | null;
  lead_temperature: "hot" | "warm" | "cold" | null;
  last_disposition: string | null;
  last_attempted_at: string | null;
  locked_by_user_id: number | null;
  locked_by_name: string | null;
  locked_until_at: string | null;
  lock_extensions: number;
  hot_since: string | null;
  is_locked_by_me: boolean;
  is_locked_by_other: boolean;
}

interface InboxResponse {
  hot: LeadCard[];
  warm: LeadCard[];
  cold: LeadCard[];
  my_active: LeadCard[];
  server_time: string;
  filter_from: string | null;
  filter_to: string | null;
}

interface LockResponse {
  lead_id: number;
  locked_by_user_id: number;
  locked_until_at: string;
  lock_extensions: number;
  server_time: string;
}

// ─────────────── disposition catalogue ───────────────
// Trimmed 2026-06-12 to the 2 essentials. Callback datetime removed
// 2026-06-21 — counsellors only need disposition + free-form notes now.
// The full enum still exists on the backend for historical events —
// this shortlist just controls what counsellors see in the modal.
const DISPOSITIONS: { value: string; label: string; notesOptional?: boolean }[] = [
  { value: "interested_nurture",   label: "Interested" },
  { value: "not_interested_other", label: "Not interested" },
];
const HOT_SLA_MIN = 30;
const MIN_NOTES_LEN = 10;

// ─────────────── helpers ───────────────
function fmtCountdown(targetIso: string, nowMs: number): string {
  const target = new Date(targetIso).getTime();
  const remaining = Math.max(0, Math.floor((target - nowMs) / 1000));
  const m = Math.floor(remaining / 60);
  const s = remaining % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function fmtRel(iso: string | null, nowMs: number): string {
  if (!iso) return "—";
  const sec = Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 1000));
  if (sec < 30) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

// ─────────────── page ───────────────
export default function InboxPage() {
  const { isAdmin } = useAuth();
  const toast = useToast();
  const { markAllSeen } = useInboxNotifier();
  const [data, setData] = useState<InboxResponse | null>(null);
  const [busy, setBusy] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [active, setActive] = useState<{ card: LeadCard; lock: LockResponse } | null>(null);

  // History-mode date filter. Both empty = live mode. Both set = history.
  const [dateFrom, setDateFrom] = useState<string>("");
  const [dateTo, setDateTo] = useState<string>("");
  const [rangeError, setRangeError] = useState<string | null>(null);
  const historyMode = !!(dateFrom && dateTo && !rangeError);

  // Today in IST as YYYY-MM-DD — used as the date input's `max` so users
  // can't pick a future date.
  const todayIst = (() => {
    const nowMs = Date.now() + 5.5 * 60 * 60 * 1000;
    const d = new Date(nowMs);
    const y = d.getUTCFullYear();
    const m = (d.getUTCMonth() + 1).toString().padStart(2, "0");
    const day = d.getUTCDate().toString().padStart(2, "0");
    return `${y}-${m}-${day}`;
  })();

  // Validate the from/to pair as the user edits.
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

  const load = useCallback(async () => {
    if (rangeError) return;
    try {
      const qs = new URLSearchParams();
      if (dateFrom && dateTo) {
        qs.set("from", dateFrom);
        qs.set("to", dateTo);
      }
      const url = qs.toString() ? `/api/inbox?${qs.toString()}` : "/api/inbox";
      const r = await api.get<InboxResponse>(url);
      setData(r);
      setErr(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Failed to load inbox");
    } finally {
      setBusy(false);
    }
  }, [dateFrom, dateTo, rangeError]);

  useEffect(() => {
    load();
    // History-mode views don't auto-refresh — the data is historical and
    // doesn't change. Live mode keeps the 5s poll.
    if (historyMode) return;
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load, historyMode]);

  const clearRange = () => {
    setDateFrom("");
    setDateTo("");
  };

  // Admin one-shot: delete every Lead whose `name` is NULL. Pre-flight
  // fetch shows the operator what's about to vanish; confirm dialog
  // requires explicit click-through; cascade delete on the backend.
  const [deleting, setDeleting] = useState(false);
  const deleteUnnamedLeads = async () => {
    setDeleting(true);
    try {
      const preview = await api.get<{ leads_to_delete: number; calls_to_delete: number }>(
        "/api/admin/unnamed_leads/preview"
      );
      if (preview.leads_to_delete === 0) {
        toast.success("Nothing to delete", "There are no unnamed leads.");
        return;
      }
      const msg =
        `Permanently delete ${preview.leads_to_delete} unnamed lead${preview.leads_to_delete === 1 ? "" : "s"}?\n\n` +
        `This will also remove ${preview.calls_to_delete} call${preview.calls_to_delete === 1 ? "" : "s"}, ` +
        `their transcripts, timeline events, callbacks, and campaign attachments.\n\nThis cannot be undone.`;
      if (!window.confirm(msg)) return;
      const res = await api.delete<{ leads_deleted: number; calls_deleted: number }>(
        "/api/admin/unnamed_leads"
      );
      toast.success(
        "Unnamed leads deleted",
        `${res.leads_deleted} leads, ${res.calls_deleted} calls removed.`
      );
      load();
    } catch (e: unknown) {
      toast.error("Delete failed", e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  };

  // Clear the unread badge — opening the inbox counts as having seen
  // every hot/warm card currently surfaced.
  useEffect(() => {
    markAllSeen();
  }, [markAllSeen]);

  const onCardClick = async (card: LeadCard) => {
    if (card.is_locked_by_other) {
      toast.error(
        `Locked by ${card.locked_by_name ?? "another counsellor"} until ${
          card.locked_until_at ? new Date(card.locked_until_at).toLocaleTimeString() : "—"
        }`
      );
      return;
    }
    try {
      const lock = await api.post<LockResponse>(`/api/leads/${card.id}/lock`, {});
      setActive({ card, lock });
      // Optimistically refresh to reflect the lock everywhere
      load();
    } catch (e: unknown) {
      if (e instanceof ApiError && e.status === 409) {
        const body = e.body as { detail?: { locked_by_name?: string; locked_until_at?: string } };
        const name = body?.detail?.locked_by_name ?? "another counsellor";
        toast.error(`Locked by ${name}`);
        load();
      } else {
        toast.error(e instanceof Error ? e.message : "Lock failed");
      }
    }
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-ink flex items-center gap-2.5">
            <InboxIcon className="h-6 w-6 text-brand" />
            Inbox
          </h1>
          <p className="text-sm text-ink-faint mt-1">
            {historyMode ? (
              <>
                Showing leads you closed out between{" "}
                <span className="text-brand font-medium">{dateFrom}</span> →{" "}
                <span className="text-brand font-medium">{dateTo}</span>{" "}
                <span className="text-ink-faint">(IST, inclusive)</span>
              </>
            ) : (
              "Hot leads classified by the AI are at the top. Click a card to claim a 5-minute lock and place the call."
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {isAdmin && (
            <button
              onClick={deleteUnnamedLeads}
              disabled={deleting}
              className="btn-ghost text-sm text-danger hover:bg-danger/10"
              title="Permanently delete every lead whose name is empty (Excel-mangled imports)"
            >
              {deleting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              Delete unnamed
            </button>
          )}
          <button onClick={load} disabled={busy || !!rangeError} className="btn-ghost text-sm">
            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </button>
        </div>
      </div>

      {/* Date-range filter — switches the page from LIVE to HISTORY mode. */}
      <div className="card p-3 flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Calendar className="h-3.5 w-3.5 text-ink-faint" />
          <span className="text-[10px] uppercase tracking-widest text-ink-faint">
            Show what I worked on
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
          {historyMode && (
            <button onClick={clearRange} className="btn-ghost text-xs" title="Back to live inbox">
              <X className="h-3 w-3" />
              Clear
            </button>
          )}
          {historyMode && (
            <span className="text-[10px] text-ink-faint italic ml-1">
              auto-refresh off · historical view
            </span>
          )}
        </div>
        {rangeError && (
          <span className="text-xs text-danger">{rangeError}</span>
        )}
      </div>

      {err && (
        <div className="card p-4 text-sm text-danger bg-danger/5 border-danger/30">{err}</div>
      )}

      {busy && !data ? (
        <div className="card p-8 flex justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-brand" />
        </div>
      ) : data ? (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
          <Column
            title="Locked by me"
            subtitle={`${data.my_active.length}`}
            icon={<Lock className="h-4 w-4 text-brand" />}
            cards={data.my_active}
            isAdmin={isAdmin}
            serverTimeIso={data.server_time}
            onClick={(c) => setActive({ card: c, lock: {
              lead_id: c.id,
              locked_by_user_id: c.locked_by_user_id!,
              locked_until_at: c.locked_until_at!,
              lock_extensions: c.lock_extensions,
              server_time: data.server_time,
            } })}
            emphasise="mine"
          />
          <Column
            title="Hot — call now"
            subtitle={`${data.hot.length}`}
            icon={<Flame className="h-4 w-4 text-amber-500" />}
            cards={data.hot}
            isAdmin={isAdmin}
            serverTimeIso={data.server_time}
            onClick={onCardClick}
            emphasise="hot"
          />
          <Column
            title="Warm"
            subtitle={`${data.warm.length}`}
            icon={<Thermometer className="h-4 w-4 text-emerald-500" />}
            cards={data.warm}
            isAdmin={isAdmin}
            serverTimeIso={data.server_time}
            onClick={onCardClick}
          />
          <Column
            title="Cold"
            subtitle={`${data.cold.length}`}
            icon={<Snowflake className="h-4 w-4 text-sky-500" />}
            cards={data.cold}
            isAdmin={isAdmin}
            serverTimeIso={data.server_time}
            onClick={onCardClick}
          />
        </div>
      ) : null}

      {active && (
        <DispositionModal
          card={active.card}
          lock={active.lock}
          isAdmin={isAdmin}
          onClose={() => {
            setActive(null);
            load();
          }}
        />
      )}
    </div>
  );
}

// ─────────────── column ───────────────
function Column({
  title,
  subtitle,
  icon,
  cards,
  isAdmin,
  serverTimeIso,
  onClick,
  emphasise,
}: {
  title: string;
  subtitle: string;
  icon: React.ReactNode;
  cards: LeadCard[];
  isAdmin: boolean;
  serverTimeIso: string;
  onClick: (c: LeadCard) => void;
  emphasise?: "hot" | "mine";
}) {
  const serverNowMs = new Date(serverTimeIso).getTime();
  return (
    <div className="card overflow-hidden flex flex-col min-h-[400px]">
      <div className="px-3 py-2.5 border-b border-border flex items-center justify-between bg-surface">
        <div className="flex items-center gap-2 text-sm font-semibold text-ink">
          {icon}
          {title}
        </div>
        <span className="text-xs text-ink-faint tabular-nums">{subtitle}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {cards.length === 0 ? (
          <div className="text-xs text-ink-faint text-center py-8">Empty</div>
        ) : (
          cards.map((c) => (
            <Card
              key={c.id}
              card={c}
              isAdmin={isAdmin}
              serverNowMs={serverNowMs}
              onClick={() => onClick(c)}
              emphasise={emphasise}
            />
          ))
        )}
      </div>
    </div>
  );
}

// ─────────────── card ───────────────
function Card({
  card,
  isAdmin,
  serverNowMs,
  onClick,
  emphasise,
}: {
  card: LeadCard;
  isAdmin: boolean;
  serverNowMs: number;
  onClick: () => void;
  emphasise?: "hot" | "mine";
}) {
  const isSlaBreached =
    card.lead_temperature === "hot" &&
    card.hot_since &&
    !card.locked_by_user_id &&
    (serverNowMs - new Date(card.hot_since).getTime()) / 1000 > HOT_SLA_MIN * 60;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={card.is_locked_by_other}
      className={clsx(
        "w-full text-left rounded-md border bg-bg px-3 py-2.5 transition-all",
        card.is_locked_by_other
          ? "border-border opacity-50 cursor-not-allowed"
          : "border-border hover:border-brand hover:bg-hover",
        emphasise === "mine" && "border-brand bg-brand-subtle/30",
        emphasise === "hot" && !card.is_locked_by_other && "border-amber-500/40"
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-ink truncate">
            {card.name || "Unnamed lead"}
          </p>
          <p className="text-xs text-ink-faint truncate flex items-center gap-1 mt-0.5">
            <Phone className="h-3 w-3" />
            {maskPhone(card.phone, isAdmin)}
          </p>
          {card.program_interest && (
            <p className="text-[11px] text-ink-faint truncate mt-1">
              {card.program_interest}
            </p>
          )}
        </div>
        {isSlaBreached && (
          <span
            className="text-[10px] uppercase tracking-wider font-semibold px-1.5 py-0.5 rounded bg-danger/15 text-danger flex items-center gap-1 shrink-0"
            title={`Hot for ${Math.floor((serverNowMs - new Date(card.hot_since!).getTime()) / 60000)} min — SLA breached`}
          >
            <AlertTriangle className="h-3 w-3" />
            SLA
          </span>
        )}
      </div>

      <div className="flex items-center justify-between mt-2 text-[10px] uppercase tracking-wide text-ink-faint">
        {card.is_locked_by_me && card.locked_until_at ? (
          <span className="text-brand font-semibold flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {fmtCountdown(card.locked_until_at, serverNowMs)} mine
          </span>
        ) : card.is_locked_by_other ? (
          <span className="text-ink-faint flex items-center gap-1">
            <Lock className="h-3 w-3" />
            {card.locked_by_name || "Locked"}
          </span>
        ) : card.last_attempted_at ? (
          <span>Last call · {fmtRel(card.last_attempted_at, serverNowMs)}</span>
        ) : (
          <span>Never called</span>
        )}
        {card.last_disposition && (
          <span className="truncate ml-2">{card.last_disposition.replace(/_/g, " ")}</span>
        )}
      </div>
    </button>
  );
}

// ─────────────── timeline (shown inside disposition modal) ───────────────
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

interface LeadFull {
  id: number;
  name: string | null;
  phone: string;
  notes: string | null;
  last_disposition: string | null;
  last_disposition_at: string | null;
  attempt_count: number;
  lead_temperature: string | null;
  funnel_stage?: string;
}

const TIMELINE_META: Record<
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
  manual_call_placed: { icon: Phone, label: "Manual call", tint: "text-emerald-400" },
  disposition_set: { icon: CircleDot, label: "Disposition set", tint: "text-brand" },
  notes_updated: { icon: PenLine, label: "Notes updated", tint: "text-ink-dim" },
  callback_scheduled: { icon: CalendarClock, label: "Callback scheduled", tint: "text-emerald-400" },
  funnel_stage_changed: { icon: Activity, label: "Stage advanced", tint: "text-emerald-400" },
  reassigned: { icon: Shield, label: "Reassigned", tint: "text-amber-400" },
  temperature_overridden: { icon: Thermometer, label: "Temperature overridden", tint: "text-amber-400" },
  dnd_set: { icon: Shield, label: "Added to DNC", tint: "text-danger" },
};

function eventBlurb(ev: TimelineEvent): string {
  const p = ev.payload || {};
  switch (ev.event_type) {
    case "temperature_set":
      return `${p.from ?? "—"} → ${p.to}${p.program ? ` · ${p.program}` : ""}`;
    case "ai_call_ended":
      return [
        p.outcome ? `outcome: ${p.outcome}` : null,
        p.duration_s ? `${Math.round(p.duration_s as number)}s` : null,
        p.temperature ? `${p.temperature}` : null,
      ]
        .filter(Boolean)
        .join(" · ");
    case "lock_released":
      return `${((p.disposition as string) || "").replace(/_/g, " ")}`;
    case "disposition_set":
      return `${((p.disposition as string) || "").replace(/_/g, " ")}`;
    case "funnel_stage_changed":
      return `${p.from} → ${p.to}`;
    case "callback_scheduled":
      return p.scheduled_at ? new Date(p.scheduled_at as string).toLocaleString() : "";
    default:
      return "";
  }
}

// ─────────────── disposition modal ───────────────
function DispositionModal({
  card,
  lock,
  isAdmin,
  onClose,
}: {
  card: LeadCard;
  lock: LockResponse;
  isAdmin: boolean;
  onClose: () => void;
}) {
  const toast = useToast();
  const [until, setUntil] = useState(lock.locked_until_at);
  const [extensions, setExtensions] = useState(lock.lock_extensions);
  const [disposition, setDisposition] = useState<string>("");
  const [notes, setNotes] = useState("");
  const [saving, setSaving] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  // Previous activity context — fetched once on modal open so the
  // counsellor can scan it BEFORE/DURING the call.
  const [leadFull, setLeadFull] = useState<LeadFull | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [l, t] = await Promise.all([
          api.get<LeadFull>(`/api/leads/${card.id}`),
          api.get<TimelineResponse>(`/api/leads/${card.id}/timeline?limit=20`),
        ]);
        if (cancelled) return;
        setLeadFull(l);
        setTimeline(t.events);
      } catch {
        // non-fatal — counsellor can still close out the call.
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [card.id]);

  // Local clock tick — pure cosmetic; backend is the source of truth on expiry.
  const tickRef = useRef<number | null>(null);
  useEffect(() => {
    tickRef.current = window.setInterval(() => setNow(Date.now()), 500);
    return () => {
      if (tickRef.current) window.clearInterval(tickRef.current);
    };
  }, []);

  const meta = DISPOSITIONS.find((d) => d.value === disposition);
  const notesOptional = meta?.notesOptional;
  const notesValid = notesOptional || notes.trim().length >= MIN_NOTES_LEN;
  const canSave = !!disposition && notesValid && !saving;

  const expired = new Date(until).getTime() < now;

  const extend = async () => {
    try {
      const r = await api.post<LockResponse>(`/api/leads/${card.id}/extend`, {});
      setUntil(r.locked_until_at);
      setExtensions(r.lock_extensions);
      toast.success("Lock extended by 5 minutes");
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Extend failed");
    }
  };

  const save = async () => {
    if (!canSave) return;
    setSaving(true);
    try {
      await api.post(`/api/leads/${card.id}/unlock`, {
        disposition,
        notes: notes.trim() || null,
        callback_at: null,
        place_manual_call: true,
      });
      toast.success("Lead closed out. Notes saved.");
      onClose();
    } catch (e: unknown) {
      toast.error(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4">
      <div className="card w-full max-w-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 p-4 border-b border-border">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-ink truncate">
              {card.name || "Unnamed lead"}
            </h2>
            <p className="text-xs text-ink-faint mt-0.5 truncate">
              {maskPhone(card.phone, isAdmin)}
              {card.program_interest && <span className="ml-2">· {card.program_interest}</span>}
            </p>
          </div>
          <button onClick={onClose} className="btn-ghost p-1.5" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Countdown bar */}
        <div className={clsx(
          "px-4 py-2.5 border-b border-border flex items-center justify-between gap-3 text-sm",
          expired ? "bg-danger/10 text-danger" : "bg-brand-subtle/30 text-brand"
        )}>
          <div className="flex items-center gap-2 font-semibold tabular-nums">
            <Clock className="h-4 w-4" />
            {expired ? "Lock expired" : fmtCountdown(until, now)}
            <span className="text-xs font-normal text-ink-faint ml-2">
              · {2 - extensions} extension{2 - extensions === 1 ? "" : "s"} left
            </span>
          </div>
          <button
            type="button"
            onClick={extend}
            disabled={extensions >= 2 || expired}
            className="btn-ghost text-xs"
          >
            <PlusCircle className="h-3.5 w-3.5" />
            +5 min
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-4 overflow-y-auto">
          {/* Previous activity timeline. The first entry is typically the
              most recent AI call — clicking it expands the full transcript
              so the counsellor can read what was said before dialing.
              Notes are split into separate dated cards (parsed out of the
              concatenated `lead.notes` blob). */}
          <RichTimeline
            leadId={card.id}
            lead={leadFull}
            events={timeline}
            loading={historyLoading}
            nowMs={now}
          />

          <div>
            <label className="label block mb-1.5">Disposition <span className="text-danger">*</span></label>
            <select
              className="input"
              value={disposition}
              onChange={(e) => setDisposition(e.target.value)}
            >
              <option value="">Choose how the call went…</option>
              {DISPOSITIONS.map((d) => (
                <option key={d.value} value={d.value}>{d.label}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="label block mb-1.5">
              Notes{" "}
              {notesOptional ? (
                <span className="text-xs text-ink-faint">(optional for this disposition)</span>
              ) : (
                <span className="text-xs text-ink-faint">
                  ({notes.trim().length}/{MIN_NOTES_LEN}+ chars required)
                </span>
              )}
            </label>
            <textarea
              className="input min-h-[100px]"
              placeholder="What did the caller say? Next steps? Parent involvement?"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </div>
        </div>

        {/* Footer */}
        <div className="p-4 border-t border-border flex items-center justify-end gap-2">
          <button onClick={onClose} className="btn-ghost">
            Cancel (keeps lock until expiry)
          </button>
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            className="btn-primary"
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            Save &amp; release
          </button>
        </div>
      </div>
    </div>
  );
}

// ─────────────── RichTimeline ───────────────
// Replaces the old PreviousActivity panel (2026-06-11). What's new:
//   • Each entry is a collapsible card — click the header to expand details.
//   • AI calls ("ai_call_ended") expand into the FULL transcript fetched
//     on demand from /api/calls/{id}. The first such card sits at the top.
//   • Counsellor notes are PARSED out of `lead.notes` (which the backend
//     stores as one concatenated string with timestamp + name markers)
//     and shown as individual timestamped cards interleaved with events.
//   • Newest first.

interface ParsedNote {
  timestamp: Date;
  author: string;
  text: string;
}

// Lead.notes is built by inbox.unlock as repeated stanzas:
//   [YYYY-MM-DD HH:MM UTC] {user_name}:
//   {note text}
// (and earlier stanzas separated by blank lines). Parse them back out so
// each note can be rendered as its own dated card on the timeline.
function parseNotesBlob(raw: string | null): ParsedNote[] {
  if (!raw || !raw.trim()) return [];
  const header = /\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) UTC\]\s*([^\n:]+):/g;
  const positions: Array<{ index: number; ts: Date; author: string; bodyStart: number }> = [];
  let m: RegExpExecArray | null;
  while ((m = header.exec(raw)) !== null) {
    positions.push({
      index: m.index,
      ts: new Date(m[1].replace(" ", "T") + "Z"),
      author: m[2].trim(),
      bodyStart: header.lastIndex,
    });
  }
  if (positions.length === 0) {
    // Legacy single-blob note with no markers — surface it once.
    return [{ timestamp: new Date(0), author: "—", text: raw.trim() }];
  }
  const out: ParsedNote[] = [];
  for (let i = 0; i < positions.length; i++) {
    const start = positions[i].bodyStart;
    const end = i + 1 < positions.length ? positions[i + 1].index : raw.length;
    const text = raw.slice(start, end).trim();
    if (text) {
      out.push({ timestamp: positions[i].ts, author: positions[i].author, text });
    }
  }
  return out;
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

// Unified timeline entry. `key` is unique per entry; `ts` drives sort.
type Entry =
  | { kind: "event"; key: string; ts: string; ev: TimelineEvent }
  | { kind: "note"; key: string; ts: string; note: ParsedNote };

function RichTimeline({
  leadId,
  lead,
  events,
  loading,
  nowMs,
}: {
  leadId: number;
  lead: LeadFull | null;
  events: TimelineEvent[] | null;
  loading: boolean;
  nowMs: number;
}) {
  // Set of expanded card keys.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  // Per-call transcript cache so each AI call fetches at most once.
  const [transcripts, setTranscripts] = useState<Map<number, CallDetail | "loading" | "error">>(new Map());

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

  // Merge events + parsed notes into a single newest-first timeline.
  const entries: Entry[] = (() => {
    const evEntries: Entry[] = (events ?? []).map((ev) => ({
      kind: "event",
      key: `ev-${ev.id}`,
      ts: ev.created_at,
      ev,
    }));
    // notes_updated events duplicate the same info as parsed notes, so
    // hide them from the timeline — the parsed-note cards are richer.
    const filteredEvents = evEntries.filter(
      (e) => e.kind !== "event" || e.ev.event_type !== "notes_updated"
    );
    const noteEntries: Entry[] = parseNotesBlob(lead?.notes ?? null).map((n, i) => ({
      kind: "note",
      key: `note-${i}-${n.timestamp.getTime()}`,
      ts: n.timestamp.toISOString(),
      note: n,
    }));
    return [...filteredEvents, ...noteEntries].sort(
      (a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime(),
    );
  })();

  if (loading) {
    return (
      <div className="rounded-md border border-border bg-bg p-3 flex items-center gap-2 text-xs text-ink-faint">
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        Loading timeline…
      </div>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-border bg-bg p-3 text-xs text-ink-faint italic">
        No prior activity — this is the first conversation with this lead.
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border bg-bg overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-border flex items-center justify-between bg-surface">
        <div className="flex items-center gap-2 text-xs font-semibold text-ink uppercase tracking-wider">
          <Activity className="h-3.5 w-3.5 text-brand" />
          Timeline
          <span className="text-ink-faint font-normal normal-case">
            · {entries.length} entr{entries.length === 1 ? "y" : "ies"}
          </span>
        </div>
        <Link
          href={`/leads/${leadId}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-[11px] text-brand hover:underline flex items-center gap-1"
          title="Open full lead detail in a new tab"
        >
          Full timeline
          <ExternalLink className="h-3 w-3" />
        </Link>
      </div>

      {/* Scrollable card list */}
      <ol className="divide-y divide-border max-h-[400px] overflow-y-auto">
        {entries.map((entry) => (
          <TimelineCard
            key={entry.key}
            entry={entry}
            expanded={expanded.has(entry.key)}
            onToggle={() => {
              toggle(entry.key);
              // Lazy-fetch transcript on first expand of an AI call.
              if (
                entry.kind === "event" &&
                entry.ev.event_type === "ai_call_ended" &&
                entry.ev.payload?.call_id
              ) {
                const cid = Number(entry.ev.payload.call_id);
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
    </div>
  );
}

// Individual timeline card with click-to-expand body.
function TimelineCard({
  entry,
  expanded,
  onToggle,
  transcripts,
  nowMs,
}: {
  entry: Entry;
  expanded: boolean;
  onToggle: () => void;
  transcripts: Map<number, CallDetail | "loading" | "error">;
  nowMs: number;
}) {
  // Header data depends on the entry kind.
  let icon: React.ComponentType<{ className?: string }>;
  let iconTint: string;
  let title: string;
  let blurb: string;
  let actorPill: string | null = null;
  let canExpand = false;
  let absoluteTs: string;

  if (entry.kind === "note") {
    icon = PenLine;
    iconTint = "text-amber-400";
    title = "Note added";
    blurb =
      entry.note.text.length > 80
        ? entry.note.text.slice(0, 80) + "…"
        : entry.note.text;
    actorPill = entry.note.author;
    canExpand = entry.note.text.length > 80;
    absoluteTs = entry.note.timestamp.toLocaleString();
  } else {
    const ev = entry.ev;
    const meta = TIMELINE_META[ev.event_type] ?? {
      icon: CircleDot,
      label: ev.event_type.replace(/_/g, " "),
      tint: "text-ink-dim",
    };
    icon = meta.icon;
    iconTint = meta.tint;
    title = meta.label;
    blurb = eventBlurb(ev);
    actorPill =
      ev.actor_kind === "system"
        ? null
        : ev.actor_kind + (ev.actor_name ? ` · ${ev.actor_name}` : "");
    canExpand = ev.event_type === "ai_call_ended" && !!ev.payload?.call_id;
    absoluteTs = new Date(ev.created_at).toLocaleString();
  }

  const Chevron = expanded ? ChevronDown : ChevronRight;
  const Icon = icon;

  return (
    <li className="text-xs">
      {/* Clickable header row */}
      <button
        type="button"
        onClick={canExpand || entry.kind === "note" ? onToggle : undefined}
        className={clsx(
          "w-full px-3 py-2 flex items-start gap-2 text-left",
          (canExpand || entry.kind === "note") && "hover:bg-hover",
        )}
      >
        <div className="shrink-0 mt-0.5">
          {(canExpand || entry.kind === "note") ? (
            <Chevron className="h-3 w-3 text-ink-faint" />
          ) : (
            <span className="inline-block w-3" />
          )}
        </div>
        <Icon className={clsx("h-3.5 w-3.5 mt-0.5 shrink-0", iconTint)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5 flex-wrap">
            <span className="text-ink font-medium">{title}</span>
            {actorPill && (
              <span className="text-[10px] text-ink-faint">· {actorPill}</span>
            )}
          </div>
          {blurb && (
            <p className="text-[11px] text-ink-dim mt-0.5 truncate">{blurb}</p>
          )}
        </div>
        <span
          className="text-[10px] text-ink-faint tabular-nums shrink-0"
          title={absoluteTs}
        >
          {fmtRel(entry.ts, nowMs)}
        </span>
      </button>

      {/* Expanded body */}
      {expanded && entry.kind === "note" && (
        <div className="px-3 pb-3 pt-1 pl-12">
          <pre className="text-[11px] text-ink whitespace-pre-wrap leading-snug font-sans bg-surface rounded p-2 border border-border">
            {entry.note.text}
          </pre>
          <p className="text-[10px] text-ink-faint mt-1 tabular-nums">
            {absoluteTs}
          </p>
        </div>
      )}

      {expanded && entry.kind === "event" && entry.ev.event_type === "ai_call_ended" && (
        <TranscriptBody
          callId={Number(entry.ev.payload?.call_id)}
          transcripts={transcripts}
        />
      )}
    </li>
  );
}

function TranscriptBody({
  callId,
  transcripts,
}: {
  callId: number;
  transcripts: Map<number, CallDetail | "loading" | "error">;
}) {
  const state = transcripts.get(callId);
  if (state === "loading" || state === undefined) {
    return (
      <div className="px-3 pb-3 pt-1 pl-12 flex items-center gap-1.5 text-[11px] text-ink-faint">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading transcript…
      </div>
    );
  }
  if (state === "error") {
    return (
      <div className="px-3 pb-3 pt-1 pl-12 text-[11px] text-danger">
        Couldn’t load transcript for call #{callId}.
      </div>
    );
  }
  if (state.turns.length === 0) {
    return (
      <div className="px-3 pb-3 pt-1 pl-12 text-[11px] text-ink-faint italic">
        No transcript available — caller didn’t speak.
      </div>
    );
  }
  return (
    <div className="px-3 pb-3 pt-1 pl-12">
      {state.lead_reasons && (
        <p className="text-[11px] text-ink-dim italic mb-2">
          Classifier note: {state.lead_reasons}
        </p>
      )}
      <div className="bg-surface rounded p-2 border border-border max-h-60 overflow-y-auto space-y-1">
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
