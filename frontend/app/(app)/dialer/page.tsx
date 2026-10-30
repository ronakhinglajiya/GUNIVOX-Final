"use client";

/**
 * Dialer page — its own dedicated section, accessible from the sidebar
 * nav. Replaced the right-side dock 2026-05-20 because the user wanted
 * the dialer to be a separate section, not a panel inside another page.
 *
 * Layout: centered phone "device" on the left, an "Active call" panel +
 * recent-calls list on the right. The device looks like a real iOS-
 * style phone dialer:
 *   - Big screen-style number readout
 *   - 3x4 circular dialpad with iOS-style sub-letters (ABC, DEF…)
 *   - Big round Call button (red) with expanding-ring ripple on Enter
 *   - Hangup button appears in the Active Call panel when a call is live
 *
 * Backend hangup endpoint: POST /api/calls/{id}/hangup (added in the
 * same sprint — thin wrapper around VobizClient.hangup()).
 */
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Phone,
  PhoneOff,
  PhoneCall,
  Loader2,
  Delete,
  RotateCcw,
  History,
} from "lucide-react";
import { api } from "@/lib/api";
import { useToast } from "@/lib/toast-context";
import { useLiveEvents, type LiveEvent } from "@/lib/live";
import { maskPhone } from "@/lib/phone";
import { useAuth } from "@/lib/auth-context";
import type { Call, CallsList, Lead } from "@/lib/types";

const DIAL_KEYS: { key: string; letters?: string }[] = [
  { key: "1" },
  { key: "2", letters: "ABC" },
  { key: "3", letters: "DEF" },
  { key: "4", letters: "GHI" },
  { key: "5", letters: "JKL" },
  { key: "6", letters: "MNO" },
  { key: "7", letters: "PQRS" },
  { key: "8", letters: "TUV" },
  { key: "9", letters: "WXYZ" },
  { key: "*" },
  { key: "0", letters: "+" },
  { key: "#" },
];

interface ActiveCall {
  call_id: number;
  phone: string;
  started_at: number;
}

export default function DialerPage() {
  const [number, setNumber] = useState("+91");
  const [busy, setBusy] = useState(false);
  const [hangingUp, setHangingUp] = useState(false);
  const [recent, setRecent] = useState<Call[]>([]);
  const [leadsById, setLeadsById] = useState<Record<number, Lead>>({});
  const [ripple, setRipple] = useState(false);
  const [active, setActive] = useState<ActiveCall | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const rippleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const toast = useToast();
  const { isAdmin } = useAuth();

  const press = (k: string) => setNumber((n) => n + k);
  const backspace = () => setNumber((n) => n.slice(0, -1));
  const reset = () => setNumber("+91");

  const refresh = useCallback(async () => {
    try {
      const c = await api.get<CallsList>("/api/calls?limit=8&offset=0");
      setRecent(c.items);
      const ids = Array.from(new Set(c.items.map((x) => x.lead_id)));
      const pairs = await Promise.all(
        ids.map(async (id) => {
          try {
            return [id, await api.get<Lead>(`/api/leads/${id}`)] as const;
          } catch {
            return [id, null] as const;
          }
        })
      );
      const out: Record<number, Lead> = {};
      for (const [id, l] of pairs) if (l) out[id] = l;
      setLeadsById(out);
    } catch {
      /* topbar shows error state */
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    return () => {
      if (rippleTimer.current) clearTimeout(rippleTimer.current);
    };
  }, []);

  useLiveEvents((e: LiveEvent) => {
    // The live event bus is GLOBAL — every admin client (every browser tab,
    // every machine) receives every call.queued / call.ended event. Using
    // call.queued to drive `active` would make Browser-A show the call that
    // Browser-B just dialled, and vice versa. So `active` is set ONLY from
    // this browser's own dial() response below, never from a broadcast event.
    if (e.type === "call.queued") {
      // Just refresh the shared recent-calls list — the in-call card stays
      // tied to whatever THIS browser dialled.
      refresh();
    }
    if (e.type === "call.ended") {
      const ev = e as { type: "call.ended"; call_id?: number };
      // Clear active ONLY when the ended call is the one we're tracking.
      // Other users' calls ending must not blank our card.
      setActive((cur) =>
        cur && typeof ev.call_id === "number" && cur.call_id === ev.call_id
          ? null
          : cur,
      );
      refresh();
    }
  });

  useEffect(() => {
    if (!active) {
      setElapsed(0);
      return;
    }
    setElapsed(Math.floor((Date.now() - active.started_at) / 1000));
    const id = setInterval(() => {
      setElapsed(Math.floor((Date.now() - active.started_at) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, [active]);

  const triggerRipple = () => {
    setRipple(false);
    requestAnimationFrame(() => {
      setRipple(true);
      if (rippleTimer.current) clearTimeout(rippleTimer.current);
      rippleTimer.current = setTimeout(() => setRipple(false), 900);
    });
  };

  const dial = async (overrideNumber?: string) => {
    const trimmed = (overrideNumber ?? number).trim();
    triggerRipple();
    if (trimmed.length < 8) {
      toast.error("Invalid number", "Enter a full phone number with country code.");
      return;
    }
    setBusy(true);
    try {
      const res = await api.post<{ call_id: number }>("/api/calls/originate", {
        to: trimmed,
      });
      // THIS browser dialled it — own the in-call card locally so a
      // concurrent dial on another browser doesn't overwrite our display.
      setActive({ call_id: res.call_id, phone: trimmed, started_at: Date.now() });
      toast.success("Call queued", `#${res.call_id} → ${trimmed}.`);
      refresh();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Originate failed";
      toast.error("Dial failed", msg);
    } finally {
      setBusy(false);
    }
  };

  const hangup = async () => {
    if (!active || hangingUp) return;
    setHangingUp(true);
    try {
      await api.post(`/api/calls/${active.call_id}/hangup`, {});
      toast.success("Hangup requested", `Call #${active.call_id} ended.`);
      setActive(null);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Hangup failed";
      toast.error("Hangup failed", msg);
    } finally {
      setHangingUp(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") dial();
  };

  const mmss = (sec: number) =>
    `${Math.floor(sec / 60)}:${(sec % 60).toString().padStart(2, "0")}`;

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold text-ink tracking-tight">Dialer</h1>
        <p className="text-sm text-ink-dim mt-1">
          Place outbound calls and hang up active calls from one place.
        </p>
      </div>

      {/* Two-column device layout */}
      <div className="grid grid-cols-1 lg:grid-cols-[420px_1fr] gap-6">
        {/* === The phone device === */}
        <div className="phone-device flex flex-col">
          <div className="px-5 py-3 border-b border-border flex items-center justify-between bg-surface/60">
            <div className="flex items-center gap-2">
              <span className="inline-flex h-7 w-7 rounded-full bg-brand items-center justify-center shadow-md shadow-brand/30">
                <Phone className="h-3.5 w-3.5 text-brand-fg" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-ink leading-none">Phone</h2>
                <p className="text-[10px] uppercase tracking-widest text-ink-faint mt-0.5">
                  outbound dialer
                </p>
              </div>
            </div>
            <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-ink-faint font-mono">
              <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse" />
              ready
            </span>
          </div>

          <div className="p-5 space-y-5">
            {/* Number readout */}
            <div>
              <label className="label">Number</label>
              <div className="relative mt-1.5">
                <input
                  className="w-full bg-elevated/60 rounded-xl border border-border px-4 py-4 text-center font-mono text-[24px] tracking-wider text-ink focus:outline-none focus:border-brand focus:shadow-focus"
                  value={number}
                  onChange={(e) => setNumber(e.target.value)}
                  onKeyDown={onKey}
                  placeholder="+91XXXXXXXXXX"
                  aria-label="Phone number"
                />
                {number !== "+91" && (
                  <button
                    onClick={reset}
                    className="absolute right-3 top-1/2 -translate-y-1/2 p-1.5 text-ink-faint hover:text-ink rounded transition-colors"
                    title="Clear"
                    aria-label="Clear number"
                  >
                    <RotateCcw className="h-4 w-4" />
                  </button>
                )}
              </div>
            </div>

            {/* Dialpad */}
            <div className="grid grid-cols-3 gap-3 justify-items-center">
              {DIAL_KEYS.map(({ key, letters }) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => press(key)}
                  className="dial-key"
                  tabIndex={-1}
                  aria-label={`Key ${key}`}
                >
                  <span className="digit">{key}</span>
                  {letters && <span className="letters">{letters}</span>}
                </button>
              ))}
            </div>

            {/* Call row */}
            <div className="flex items-center justify-center gap-6 pt-1">
              <div className="w-9" />
              <button
                onClick={() => dial()}
                disabled={busy}
                data-ripple={ripple ? "on" : undefined}
                className="call-button"
                aria-label="Call"
              >
                {busy ? <Loader2 className="h-7 w-7 animate-spin" /> : <Phone className="h-7 w-7" />}
              </button>
              <button
                onClick={backspace}
                type="button"
                tabIndex={-1}
                className="w-9 h-9 flex items-center justify-center rounded-full text-ink-dim hover:text-ink hover:bg-hover transition-colors"
                title="Backspace"
                aria-label="Backspace"
              >
                <Delete className="h-5 w-5" />
              </button>
            </div>
          </div>

          <div className="px-5 py-3 border-t border-border bg-surface/40 text-center">
            <p className="text-[10px] text-ink-faint font-mono">
              Press <kbd className="px-1.5 py-0.5 rounded bg-elevated border border-border">Enter</kbd> to dial
            </p>
          </div>
        </div>

        {/* === Side column: active call + recent === */}
        <div className="space-y-4">
          {/* Active call card */}
          {active ? (
            <div className="relative card p-5 overflow-hidden border-emerald-500/30 bg-emerald-500/[0.04]">
              <div className="absolute -top-8 -left-8 h-32 w-32 rounded-full bg-emerald-500/10 animate-pulse" />
              <div className="relative flex items-center justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                    <p className="label text-emerald-400">In call</p>
                  </div>
                  <p className="font-mono text-xl text-ink truncate">{maskPhone(active.phone, isAdmin)}</p>
                  <p className="font-mono text-sm text-ink-faint mt-1">
                    Call #{active.call_id} · {mmss(elapsed)}
                  </p>
                </div>
                <button
                  onClick={hangup}
                  disabled={hangingUp}
                  className="shrink-0 inline-flex items-center justify-center w-16 h-16 rounded-full bg-rose-600 hover:bg-rose-700 text-white shadow-lg shadow-rose-600/40 transition-all active:scale-95 disabled:opacity-50"
                  title="Hang up"
                  aria-label="Hang up"
                >
                  {hangingUp ? <Loader2 className="h-6 w-6 animate-spin" /> : <PhoneOff className="h-6 w-6" />}
                </button>
              </div>
            </div>
          ) : (
            <div className="card p-5 flex items-center justify-between gap-3">
              <div>
                <p className="label">No active call</p>
                <p className="text-sm text-ink-dim mt-1">
                  Dial a number on the left, then press <kbd className="px-1.5 py-0.5 rounded bg-elevated border border-border font-mono text-[10px]">Enter</kbd> or hit Call.
                </p>
              </div>
              <Phone className="h-6 w-6 text-ink-faint shrink-0" />
            </div>
          )}

          {/* Recent calls */}
          <div className="card">
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h3 className="font-semibold flex items-center gap-2 text-sm">
                <History className="h-4 w-4 text-ink-faint" /> Recent calls
              </h3>
              <span className="text-[11px] text-ink-faint font-mono">tap to redial</span>
            </div>
            {recent.length === 0 ? (
              <div className="p-8 text-center text-ink-dim text-sm">
                <Phone className="h-7 w-7 mx-auto mb-2 text-ink-faint" />
                <p>No calls yet.</p>
                <p className="text-xs text-ink-faint mt-1">Your recent dials will show here.</p>
              </div>
            ) : (
              <ul className="divide-y divide-border">
                {recent.map((c) => {
                  const lead = leadsById[c.lead_id];
                  const phone = lead?.phone;
                  const displayName = c.caller_name || lead?.name;
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        disabled={!phone}
                        onClick={() => phone && dial(phone)}
                        className="w-full text-left px-4 py-3 hover:bg-hover transition-colors flex items-center gap-3 group disabled:opacity-50"
                        title={phone ? `Redial ${maskPhone(phone, isAdmin)}` : "Phone unavailable"}
                      >
                        <PhoneCall className="h-4 w-4 text-ink-faint group-hover:text-brand shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-medium text-ink truncate">
                            {displayName || `Lead #${c.lead_id}`}
                          </p>
                          <p className="text-xs text-ink-faint font-mono truncate">{phone ? maskPhone(phone, isAdmin) : "—"}</p>
                        </div>
                        {c.lead_program && (
                          <span className="text-[11px] text-ink-dim truncate max-w-[140px] hidden sm:inline">
                            {c.lead_program}
                          </span>
                        )}
                        {c.lead_temperature && (
                          <span
                            className={
                              c.lead_temperature === "hot"
                                ? "pill-hot"
                                : c.lead_temperature === "warm"
                                ? "pill-warm"
                                : "pill-cold"
                            }
                          >
                            {c.lead_temperature}
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
