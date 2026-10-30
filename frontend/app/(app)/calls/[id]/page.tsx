"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { api } from "@/lib/api";
import { fmtIST } from "@/lib/datetime";
import type { CallDetail } from "@/lib/types";
import { ArrowLeft, PhoneForwarded, Loader2 } from "lucide-react";

function fmtDuration(s?: number | null): string {
  if (!s) return "—";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

export default function CallDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = Number(params.id);
  const [call, setCall] = useState<CallDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [transferring, setTransferring] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        setCall(await api.get<CallDetail>(`/api/calls/${id}`));
      } finally {
        setLoading(false);
      }
    })();
  }, [id]);

  useEffect(() => {
    let url: string | null = null;
    (async () => {
      if (!call?.recording_path) return;
      try {
        const token = localStorage.getItem("gunivox.jwt");
        const r = await fetch(`${api.base}/api/calls/${id}/recording`, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (r.ok) {
          const blob = await r.blob();
          url = URL.createObjectURL(blob);
          setAudioUrl(url);
        }
      } catch {
        /* no recording or fetch failed */
      }
    })();
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, call?.recording_path]);

  const transfer = async () => {
    if (!confirm("Transfer this call to an available counsellor?")) return;
    setTransferring(true);
    try {
      const r = await api.post<{ counsellor_id: number; conference_room: string }>(
        `/api/calls/${id}/transfer`,
        {}
      );
      alert(`Transferring to counsellor #${r.counsellor_id} in room ${r.conference_room}`);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Transfer failed");
    } finally {
      setTransferring(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-ink-dim">
        <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading…
      </div>
    );
  }
  if (!call) {
    return <div className="text-ink-dim">Call not found.</div>;
  }

  return (
    <div className="space-y-5 max-w-4xl">
      <button onClick={() => router.back()} className="btn-ghost text-sm">
        <ArrowLeft className="h-4 w-4" /> Back
      </button>

      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">Call #{call.id}</h1>
          <p className="text-sm text-ink-dim mt-1">
            Lead #{call.lead_id} · {fmtIST(call.started_at)}
          </p>
        </div>
        {call.ended_at == null && (
          <button onClick={transfer} disabled={transferring} className="btn-primary">
            {transferring ? <Loader2 className="h-4 w-4 animate-spin" /> : <PhoneForwarded className="h-4 w-4" />}
            Warm transfer
          </button>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Meta label="Duration" value={fmtDuration(call.duration_s)} />
        <Meta label="TTFA p50" value={call.ttfa_p50_ms ? `${call.ttfa_p50_ms} ms` : "—"} />
        <Meta label="Language" value={call.language ?? "—"} />
        <Meta label="Outcome" value={call.outcome ?? "—"} />
      </div>

      {audioUrl && (
        <div className="card p-4">
          <p className="label mb-2">Recording</p>
          <audio controls src={audioUrl} className="w-full" />
        </div>
      )}

      <div className="card">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="font-semibold">Transcript ({call.turns.length} turns)</h2>
        </div>
        {call.turns.length === 0 ? (
          <div className="p-8 text-center text-ink-dim text-sm">No turns recorded.</div>
        ) : (
          <div className="divide-y divide-border">
            {call.turns.map((t) => (
              <div key={t.id} className="px-4 py-3">
                <div className="flex items-center gap-2 mb-1">
                  <span className={t.role === "user" ? "badge-info" : "badge-success"}>
                    {t.role}
                  </span>
                  <span className="text-xs text-ink-faint font-mono">#{t.turn_index}</span>
                  {t.latency_ms != null && (
                    <span className="text-xs text-ink-faint font-mono">{t.latency_ms}ms</span>
                  )}
                </div>
                <p className="text-sm text-ink whitespace-pre-wrap">{t.text}</p>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="card p-3">
      <p className="label">{label}</p>
      <p className="text-sm font-mono mt-1">{value}</p>
    </div>
  );
}
