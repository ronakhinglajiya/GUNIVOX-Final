"use client";

import { useState, KeyboardEvent } from "react";
import { Phone, X, Loader2, Delete } from "lucide-react";
import { api } from "@/lib/api";
import { useToast } from "@/lib/toast-context";

const DIAL_KEYS = [
  ["1", "2", "3"],
  ["4", "5", "6"],
  ["7", "8", "9"],
  ["*", "0", "#"],
];

interface Props {
  open: boolean;
  onClose: () => void;
  onCalled?: () => void;
  defaultNumber?: string;
}

export function DialerModal({ open, onClose, onCalled, defaultNumber }: Props) {
  const [number, setNumber] = useState(defaultNumber ?? "+91");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  if (!open) return null;

  const press = (k: string) => setNumber((n) => n + k);
  const backspace = () => setNumber((n) => n.slice(0, -1));

  const dial = async () => {
    const trimmed = number.trim();
    if (trimmed.length < 8) {
      toast.error("Invalid number", "Enter a full phone number with country code.");
      return;
    }
    setBusy(true);
    try {
      const res = await api.post<{ call_id: number }>("/api/calls/originate", {
        to: trimmed,
      });
      toast.success("Call queued", `#${res.call_id} → ${trimmed}. The phone should ring shortly.`);
      onCalled?.();
      onClose();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Originate failed";
      toast.error("Dial failed", msg);
    } finally {
      setBusy(false);
    }
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") dial();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="card w-full max-w-sm p-5 animate-slide-up shadow-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <Phone className="h-5 w-5 text-brand" /> Dial
          </h3>
          <button onClick={onClose} className="btn-ghost p-1">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mb-3">
          <input
            autoFocus
            className="input text-center font-mono text-xl tracking-wider py-3"
            value={number}
            onChange={(e) => setNumber(e.target.value)}
            onKeyDown={onKey}
            placeholder="+91XXXXXXXXXX"
          />
        </div>

        {/* Numeric dial-pad. Keeps the corporate-dialer feel. */}
        <div className="grid grid-cols-3 gap-2 mb-4">
          {DIAL_KEYS.flat().map((k) => (
            <button
              key={k}
              type="button"
              onClick={() => press(k)}
              className="btn-secondary py-3 font-mono text-lg"
              tabIndex={-1}
            >
              {k}
            </button>
          ))}
          <button
            type="button"
            onClick={backspace}
            className="btn-secondary py-3 col-start-3 -mt-2"
            tabIndex={-1}
            aria-label="Backspace"
          >
            <Delete className="h-4 w-4" />
          </button>
        </div>

        <button onClick={dial} disabled={busy} className="btn-primary w-full py-3 text-base">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Phone className="h-4 w-4" />}
          {busy ? "Dialing…" : "Call"}
        </button>

        <p className="text-xs text-ink-faint text-center mt-3">
          Press <kbd className="px-1 rounded bg-elevated border border-border font-mono text-[10px]">Enter</kbd> to dial
        </p>
      </div>
    </div>
  );
}
