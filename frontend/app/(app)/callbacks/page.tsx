"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Callback, CallbacksList } from "@/lib/types";
import { CheckCircle, Clock } from "lucide-react";
import { fmtIST } from "@/lib/datetime";

export default function CallbacksPage() {
  const [items, setItems] = useState<Callback[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchAll = async () => {
    setLoading(true);
    try {
      const r = await api.get<CallbacksList>("/api/callbacks?limit=100");
      setItems(r.items);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAll();
  }, []);

  const markDone = async (cb: Callback) => {
    try {
      await api.put(`/api/callbacks/${cb.id}`, { status: "done" });
      await fetchAll();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed");
    }
  };

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-bold">Callbacks</h1>

      <div className="card overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-ink-dim">Loading…</div>
        ) : items.length === 0 ? (
          <div className="p-10 text-center text-ink-dim">No callbacks scheduled.</div>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-ink-dim text-xs uppercase tracking-wider">
                <th className="text-left px-4 py-3">Lead</th>
                <th className="text-left px-4 py-3">Scheduled for</th>
                <th className="text-left px-4 py-3">Status</th>
                <th className="text-left px-4 py-3">Notes</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {items.map((cb) => (
                <tr key={cb.id} className="hover:bg-hover transition-colors">
                  <td className="px-4 py-3">Lead #{cb.lead_id}</td>
                  <td className="px-4 py-3 font-mono text-xs">{fmtIST(cb.scheduled_at)}</td>
                  <td className="px-4 py-3">
                    <span className={cb.status === "done" ? "badge-success" : cb.status === "pending" ? "badge-warning" : "badge-muted"}>
                      {cb.status === "pending" && <Clock className="h-3 w-3" />}
                      {cb.status === "done" && <CheckCircle className="h-3 w-3" />}
                      {cb.status}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-ink-dim">{cb.notes ?? "—"}</td>
                  <td className="px-4 py-3 text-right">
                    {cb.status === "pending" && (
                      <button onClick={() => markDone(cb)} className="btn-ghost text-xs">
                        Mark done
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
