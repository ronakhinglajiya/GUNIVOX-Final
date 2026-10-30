"use client";

/**
 * Right-side dialer dock — visible ONLY on /dashboard.
 *
 * Redesigned 2026-05-20 as a phone-app-style "device" panel:
 *   - Floats inside a 380px outer column with padding, so the inner
 *     card is a rounded device sitting alongside the page content
 *     rather than welded to the edge.
 *   - Circular dialpad keys with iOS-style sub-letters (ABC, DEF…).
 *   - Big round Call button.
 *   - Pressing Enter (or tapping Call) triggers an expanding ring
 *     ripple animation around the Call button so the action feels
 *     tactile, especially on touch screens.
 */
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { Phone, Loader2, Delete, RotateCcw, PhoneCall, History, PhoneOff } from "lucide-react";
import { api } from "@/lib/api";
import { useToast } from "@/lib/toast-context";
import { useLiveEvents, type LiveEvent } from "@/lib/live";
import type { Call, CallsList, Lead } from "@/lib/types";

// Routes where the dock is visible. Anything else returns null.
const DOCK_ROUTES = ["/dashboard"];

// Phone-app dialpad layout. Each entry is [digit, letters]. Letters
// shown as a small sub-line under the digit (the classic '2 ABC',
// '3 DEF', '7 PQRS' look). * and # get no sub-letters.
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

export function DialerDock() {
  const pathname = usePathname();
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

  const visible = DOCK_ROUTES.some(
    (r) => pathname === r || pathname?.startsWith(r + "/")
  );

  const press = (k: string) => setNumber((n) => n + k);
  const backspace = () => setNumber((n) => n.slice(0, -1));
  const reset = () => setNumber("+91");

  const refresh = useCallback(async () => {
    try {
      const c = await api.get<CallsList>("/api/calls?limit=6&offset=0");
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
    // The LiveEvent union has a permissive fallback that blocks strict
    // narrowing here — cast to the strict variant once we know `type`.
    if (e.type === "call.queued") {
      const ev = e as { type: "call.queued"; call_id: number; lead_id: number; phone: string };
      if (typeof ev.call_id === "number" && typeof ev.phone === "string") {
        setActive({ call_id: ev.call_id, phone: ev.phone, started_at: Date.now() });
      }
      refresh();
    }
    if (e.type === "call.ended") {
      const ev = e as { type: "call.ended"; call_id?: number };
      setActive((cur) => (cur && (!ev.call_id || cur.call_id === ev.call_id) ? null : cur));
      refresh();
    }
  });

  // Tick the active-call elapsed timer once per second so the UI shows
  // a live mm:ss while the call is in progress.
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
    // Restart the animation by toggling the flag in the next frame.
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
      toast.success(
        "Call queued",
        `#${res.call_id} → ${trimmed}. The phone should ring shortly.`
      );
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
      // We optimistically clear the active panel; the call.ended WS event
      // will also clear it (idempotent).
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

  if (!visible) return null;

  return (
    // Outer column: 380px wide, gives breathing room around the device.
    // The dock itself is a rounded "phone-device" card that visually
    // separates from the rest of the UI.
    <aside className="w-[380px] shrink-0 bg-surface border-l border-border flex flex-col p-4">
      <div className="phone-device flex-1 flex flex-col">
        {/* Header — like a phone status bar */}
        <div className="px-5 py-3 border-b border-border flex items-center justify-between bg-surface/60">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-7 w-7 rounded-full bg-brand items-center justify-center shadow-md shadow-brand/30">
              <Phone className="h-3.5 w-3.5 text-brand-fg" />
            </span>
            <div>
              <h2 className="text-sm font-semibold text-ink leading-none">Dialer</h2>
              <p className="text-[10px] uppercase tracking-widest text-ink-faint mt-0.5">
                always on
              </p>
            </div>
          </div>
          <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-widest text-ink-faint font-mono">
            <span className="h-1.5 w-1.5 rounded-full bg-success animate-pulse" />
            ready
          </span>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-5">
          {/* ACTIVE CALL panel — only shows while a call is in progress.
              Provides the working Hangup button (POST /api/calls/{id}/hangup). */}
          {active && (
            <div className="relative rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4 overflow-hidden">
              {/* breathing dot animation behind the icon */}
              <div className="absolute -top-6 -left-6 h-20 w-20 rounded-full bg-emerald-500/10 animate-pulse" />
              <div className="relative flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />
                    <p className="label text-emerald-400">In call</p>
                  </div>
                  <p className="font-mono text-sm text-ink truncate">{active.phone}</p>
                  <p className="font-mono text-xs text-ink-faint mt-0.5">
                    #{active.call_id} · {mmss(elapsed)}
                  </p>
                </div>
                <button
                  onClick={hangup}
                  disabled={hangingUp}
                  className="shrink-0 inline-flex items-center justify-center w-12 h-12 rounded-full bg-rose-600 hover:bg-rose-700 text-white shadow-lg shadow-rose-600/40 transition-all active:scale-95 disabled:opacity-50"
                  title="Hang up"
                  aria-label="Hang up"
                >
                  {hangingUp ? (
                    <Loader2 className="h-5 w-5 animate-spin" />
                  ) : (
                    <PhoneOff className="h-5 w-5" />
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Big readout — the "screen" of the phone */}
          <div>
            <label className="label">Number</label>
            <div className="relative mt-1.5">
              <input
                className="w-full bg-elevated/60 rounded-xl border border-border px-4 py-4 text-center font-mono text-[22px] tracking-wider text-ink focus:outline-none focus:border-brand focus:shadow-focus"
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

          {/* Dialpad — 3x4 grid of circular keys */}
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

          {/* Call row: BIG circular Call button + small backspace */}
          <div className="flex items-center justify-center gap-6 pt-1">
            <div className="w-9" />
            <button
              onClick={() => dial()}
              disabled={busy}
              data-ripple={ripple ? "on" : undefined}
              className="call-button"
              aria-label="Call"
            >
              {busy ? (
                <Loader2 className="h-7 w-7 animate-spin" />
              ) : (
                <Phone className="h-7 w-7" />
              )}
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

          {/* Recent calls strip */}
          <div className="pt-1">
            <div className="flex items-center justify-between mb-2">
              <h3 className="label flex items-center gap-1.5">
                <History className="h-3 w-3" /> Recent
              </h3>
              <span className="text-[10px] text-ink-faint font-mono">tap to redial</span>
            </div>
            {recent.length === 0 ? (
              <div className="text-xs text-ink-faint text-center py-6 border border-dashed border-border rounded-lg">
                No calls yet
              </div>
            ) : (
              <ul className="space-y-1">
                {recent.slice(0, 5).map((c) => {
                  const lead = leadsById[c.lead_id];
                  const phone = lead?.phone;
                  const displayName = c.caller_name || lead?.name;
                  return (
                    <li key={c.id}>
                      <button
                        type="button"
                        disabled={!phone}
                        onClick={() => phone && dial(phone)}
                        className="w-full text-left px-2.5 py-2 rounded-lg hover:bg-hover transition-colors flex items-center gap-2 group disabled:opacity-50"
                        title={phone ? `Redial ${phone}` : "Phone unavailable"}
                      >
                        <PhoneCall className="h-3.5 w-3.5 text-ink-faint group-hover:text-brand shrink-0" />
                        <div className="min-w-0 flex-1">
                          <p className="text-xs font-medium text-ink truncate">
                            {displayName || `Lead #${c.lead_id}`}
                          </p>
                          <p className="text-[10px] text-ink-faint font-mono truncate">
                            {phone ?? "—"}
                          </p>
                        </div>
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

        {/* Footer hint — looks like a phone's home indicator bar area */}
        <div className="px-5 py-3 border-t border-border bg-surface/40 text-center">
          <p className="text-[10px] text-ink-faint font-mono">
            Press{" "}
            <kbd className="px-1.5 py-0.5 rounded bg-elevated border border-border">Enter</kbd>{" "}
            to dial
          </p>
        </div>
      </div>
    </aside>
  );
}
