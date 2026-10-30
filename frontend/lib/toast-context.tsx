"use client";

import { createContext, useContext, useState, useCallback, useRef, ReactNode } from "react";
import { CheckCircle2, XCircle, Info, AlertTriangle, X } from "lucide-react";

type ToastKind = "success" | "error" | "info" | "warning";

interface Toast {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
}

interface ToastCtx {
  toast: (t: Omit<Toast, "id">) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastCtx | undefined>(undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  // Monotonic counter — old impl used `Date.now() + Math.random()` which
  // collides under float64 precision when several toasts fire in the same
  // millisecond (the InboxNotifier can do this when 5+ hot leads land at
  // once). Resulting duplicate React keys triggered the "two children with
  // the same key" warnings.
  const seqRef = useRef(0);

  const remove = (id: number) => setItems((xs) => xs.filter((t) => t.id !== id));

  const toast = useCallback((t: Omit<Toast, "id">) => {
    seqRef.current += 1;
    const id = seqRef.current;
    setItems((xs) => [...xs, { ...t, id }]);
    setTimeout(() => remove(id), 5000);
  }, []);

  const success = useCallback((title: string, description?: string) =>
    toast({ kind: "success", title, description }), [toast]);
  const error = useCallback((title: string, description?: string) =>
    toast({ kind: "error", title, description }), [toast]);
  const info = useCallback((title: string, description?: string) =>
    toast({ kind: "info", title, description }), [toast]);

  return (
    <ToastContext.Provider value={{ toast, success, error, info }}>
      {children}
      <div className="fixed bottom-6 right-6 z-50 flex flex-col gap-2 pointer-events-none">
        {items.map((t) => {
          const Icon =
            t.kind === "success" ? CheckCircle2 :
            t.kind === "error" ? XCircle :
            t.kind === "warning" ? AlertTriangle : Info;
          const color =
            t.kind === "success" ? "text-success" :
            t.kind === "error" ? "text-danger" :
            t.kind === "warning" ? "text-warning" : "text-info";
          return (
            <div
              key={t.id}
              className="card shadow-lg p-3 pr-10 min-w-[280px] max-w-md pointer-events-auto animate-slide-up relative"
            >
              <div className="flex items-start gap-3">
                <Icon className={`h-5 w-5 mt-0.5 shrink-0 ${color}`} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-ink">{t.title}</p>
                  {t.description && (
                    <p className="text-xs text-ink-dim mt-0.5 break-words">{t.description}</p>
                  )}
                </div>
              </div>
              <button
                onClick={() => remove(t.id)}
                className="absolute top-2 right-2 text-ink-faint hover:text-ink"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastCtx {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be inside ToastProvider");
  return ctx;
}
