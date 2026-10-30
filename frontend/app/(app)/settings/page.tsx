"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { User } from "@/lib/types";

export default function SettingsPage() {
  const { user, refresh } = useAuth();
  const [busy, setBusy] = useState(false);

  const toggleAvailability = async () => {
    if (!user) return;
    setBusy(true);
    try {
      await api.put<User>(`/api/me/availability?is_available=${!user.is_available}`);
      await refresh();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5 max-w-2xl">
      <h1 className="text-2xl font-bold">Settings</h1>

      <div className="card p-5">
        <p className="label mb-3">Profile</p>
        <div className="space-y-2 text-sm">
          <Row k="Name" v={user?.name ?? "—"} />
          <Row k="Email" v={user?.email ?? "—"} />
          <Row k="Role" v={user?.role ?? "—"} />
          <Row k="Phone" v={user?.phone ?? "—"} />
        </div>
      </div>

      {user?.role === "counsellor" && (
        <div className="card p-5">
          <p className="label mb-3">Availability for warm transfers</p>
          <button
            onClick={toggleAvailability}
            disabled={busy}
            className={user.is_available ? "btn-primary" : "btn-outline"}
          >
            <span className={user.is_available ? "h-2 w-2 rounded-full bg-success animate-pulse" : "h-2 w-2 rounded-full bg-ink-faint"} />
            {user.is_available ? "Online — accepting transfers" : "Offline"}
          </button>
          <p className="text-xs text-ink-faint mt-2">
            When you're online, the AI can warm-transfer admissions calls to you mid-conversation.
          </p>
        </div>
      )}

    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between border-b border-border last:border-b-0 py-2">
      <span className="text-ink-dim">{k}</span>
      <span>{v}</span>
    </div>
  );
}
