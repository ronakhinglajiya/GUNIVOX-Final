"use client";

import { useEffect, useState } from "react";
import { Activity } from "lucide-react";
import { api } from "@/lib/api";
import { ThemeToggle } from "@/components/theme-toggle";

interface HealthOut {
  status: string;
  db?: { status?: string };
  openai?: { ok?: boolean };
  sarvam?: { ok?: boolean };
  vobiz_config?: { ok?: boolean };
}

export function Topbar() {
  const [health, setHealth] = useState<HealthOut | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const h = await api.get<HealthOut>("/api/health/full");
        if (!cancelled) setHealth(h);
      } catch {
        if (!cancelled) setHealth({ status: "error" });
      }
    };
    tick();
    const id = setInterval(tick, 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const status = health?.status ?? "checking";
  const dot =
    status === "ok" ? "bg-success" :
    status === "degraded" ? "bg-warning" :
    status === "checking" ? "bg-ink-faint" : "bg-danger";

  return (
    <header className="h-14 border-b border-border bg-bg px-6 flex items-center justify-between gap-4">
      <h1
        className="font-bold italic text-3xl tracking-tight select-none bg-gradient-to-r from-ink to-brand bg-clip-text text-transparent"
        style={{ fontFamily: '"Helvetica Neue", Helvetica, Arial, sans-serif' }}
      >
        Gunivox
      </h1>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-xs text-ink-dim font-mono uppercase tracking-widest">
          <Activity className="h-3.5 w-3.5" />
          <span>SYSTEM</span>
          <span className={`h-2 w-2 rounded-full ${dot}`} />
          <span>{status}</span>
        </div>
        <ThemeToggle />
      </div>
    </header>
  );
}
