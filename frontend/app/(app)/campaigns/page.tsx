"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Campaign, CampaignsList, Lead, LeadsList } from "@/lib/types";
import {
  Pause, Play, Plus, Users as UsersIcon, Upload, X, RefreshCcw,
  Loader2, FileText, CheckCircle, AlertCircle, Trash2,
} from "lucide-react";
import { fmtISTDate } from "@/lib/datetime";
import { useToast } from "@/lib/toast-context";
import { maskPhone } from "@/lib/phone";
import { useAuth } from "@/lib/auth-context";

interface Progress {
  total: number;
  queued: number;
  in_progress: number;
  done: number;
  failed: number;
  failed_retryable: number;
}

export default function CampaignsPage() {
  const [items, setItems] = useState<Campaign[]>([]);
  const [progress, setProgress] = useState<Record<number, Progress>>({});
  const [showCreate, setShowCreate] = useState(false);
  const [attachId, setAttachId] = useState<number | null>(null);
  const [showCsv, setShowCsv] = useState(false);

  const fetchAll = async () => {
    const r = await api.get<CampaignsList>("/api/campaigns?limit=200");
    setItems(r.items);
    const prog: Record<number, Progress> = {};
    await Promise.all(
      r.items.map(async (c) => {
        prog[c.id] = await api.get<Progress>(`/api/campaigns/${c.id}/progress`);
      })
    );
    setProgress(prog);
  };

  useEffect(() => {
    fetchAll();
    const id = setInterval(fetchAll, 15000);
    return () => clearInterval(id);
  }, []);

  const toggle = async (c: Campaign) => {
    const action = c.status === "active" ? "pause" : "start";
    try {
      await api.post(`/api/campaigns/${c.id}/${action}`);
      await fetchAll();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed");
    }
  };

  const retryFailed = async (c: Campaign) => {
    const p = progress[c.id];
    const n = p ? p.failed + p.failed_retryable : 0;
    if (n === 0) {
      alert("No failed leads to retry in this campaign.");
      return;
    }
    if (!window.confirm(
      `Retry ${n} failed lead${n > 1 ? "s" : ""} in "${c.name}"?\n\n` +
      `They'll be reset to queued (attempts cleared, dial timers cleared) ` +
      `and the campaign will auto-start. Existing done leads are untouched.`
    )) return;
    try {
      await api.post(`/api/campaigns/${c.id}/retry_failed`);
      await fetchAll();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Retry failed");
    }
  };

  const retryFailedAll = async () => {
    // Sum across all visible campaigns so the confirm shows the real total.
    const total = Object.values(progress).reduce(
      (s, p) => s + (p?.failed ?? 0) + (p?.failed_retryable ?? 0), 0,
    );
    if (total === 0) {
      alert("No failed leads to retry across any campaign.");
      return;
    }
    if (!window.confirm(
      `Retry ${total} failed lead${total > 1 ? "s" : ""} across ALL campaigns?\n\n` +
      `Every failed / failed_retryable lead will be reset to queued. Every ` +
      `paused campaign that had any reset leads will be auto-started.`
    )) return;
    try {
      await api.post("/api/campaigns/retry_failed_all");
      await fetchAll();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Bulk retry failed");
    }
  };

  const remove = async (c: Campaign) => {
    const p = progress[c.id];
    const leadInfo = p && p.total > 0 ? ` (${p.total} lead${p.total > 1 ? "s" : ""} attached)` : "";
    if (
      !window.confirm(
        `Delete campaign "${c.name}"${leadInfo}?\n\nThis removes the campaign + its lead attachments. The leads themselves stay in your leads list and keep their call history.`
      )
    ) {
      return;
    }
    try {
      await api.delete(`/api/campaigns/${c.id}`);
      await fetchAll();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed to delete");
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">Campaigns</h1>
          <p className="text-sm text-ink-dim mt-1">{items.length} total</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={retryFailedAll}
            className="btn-outline"
            title="Reset every failed lead in every campaign back to queued and auto-start"
          >
            <RefreshCcw className="h-4 w-4" /> Retry failed (all)
          </button>
          <button onClick={() => setShowCsv(true)} className="btn-outline">
            <Upload className="h-4 w-4" /> Upload CSV & start dialing
          </button>
          <button onClick={() => setShowCreate(true)} className="btn-primary">
            <Plus className="h-4 w-4" /> New campaign
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
        {items.map((c) => {
          const p = progress[c.id];
          const pct = p && p.total ? Math.round(((p.done + p.failed) / p.total) * 100) : 0;
          const active = c.status === "active";
          const hasReady = p ? p.queued > 0 : false;
          const needsStart = !active && hasReady;
          return (
            <div key={c.id} className="card p-5 flex flex-col gap-4 hover:border-ink-faint/30 transition-colors">
              {/* Header: name + status + delete */}
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <h3 className="text-lg font-semibold truncate" title={c.name}>{c.name}</h3>
                  <p className="text-xs text-ink-faint mt-1">Created {fmtISTDate(c.created_at)}</p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span
                    className={
                      active
                        ? "badge-success"
                        : c.status === "paused"
                        ? "badge-warning"
                        : "badge-muted"
                    }
                  >
                    {active && <span className="inline-block w-1.5 h-1.5 rounded-full bg-current animate-pulse mr-1.5" />}
                    {c.status}
                  </span>
                  <button
                    onClick={() => remove(c)}
                    className="p-1.5 rounded-md text-ink-faint hover:text-rose-400 hover:bg-rose-500/10 transition-colors"
                    title="Delete campaign"
                    aria-label={`Delete campaign ${c.name}`}
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {/* Progress + stats — fills the middle of the square */}
              {p ? (
                <div className="flex-1 flex flex-col gap-3">
                  <div>
                    <div className="flex justify-between text-xs text-ink-dim mb-1.5">
                      <span>{p.done + p.failed} / {p.total} processed</span>
                      <span className="font-mono">{pct}%</span>
                    </div>
                    <div className="h-2 bg-hover rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${active ? "bg-accent" : "bg-ink-faint/40"}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-2">
                    <StatTile label="Queued" v={p.queued} accent={hasReady ? "text-brand" : "text-ink-dim"} />
                    <StatTile label="In-progress" v={p.in_progress} accent="text-sky-400" />
                    <StatTile label="Done" v={p.done} accent="text-emerald-400" />
                    <StatTile label="Failed" v={p.failed + p.failed_retryable} accent="text-rose-400" />
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center text-xs text-ink-faint">No leads attached</div>
              )}

              {/* "Press Start" hint — the actual fix for "phones not ringing" */}
              {needsStart && (
                <div className="text-[11px] bg-amber-500/10 border border-amber-500/30 text-amber-300 rounded-md px-2.5 py-1.5 leading-snug">
                  {p!.queued} lead{p!.queued > 1 ? "s" : ""} waiting — press <strong>Start</strong> to begin dialing.
                </div>
              )}

              {/* Footer: actions */}
              <div className="flex gap-2 pt-1 flex-wrap">
                <button
                  onClick={() => setAttachId(c.id)}
                  className="btn-outline flex-1 justify-center"
                  title="Attach existing leads"
                >
                  <UsersIcon className="h-4 w-4" /> Attach
                </button>
                <button
                  onClick={() => toggle(c)}
                  className={`btn-primary flex-1 justify-center ${needsStart ? "ring-2 ring-brand/50" : ""}`}
                >
                  {active ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
                  {active ? "Pause" : "Start"}
                </button>
              </div>
              {/* Secondary action — only surfaces when there's something to retry */}
              {p && (p.failed + p.failed_retryable) > 0 && (
                <button
                  onClick={() => retryFailed(c)}
                  className="btn-ghost text-xs justify-center -mt-2"
                  title="Reset failed leads to queued and auto-start"
                >
                  <RefreshCcw className="h-3.5 w-3.5" />
                  Retry {p.failed + p.failed_retryable} failed lead
                  {p.failed + p.failed_retryable > 1 ? "s" : ""}
                </button>
              )}
            </div>
          );
        })}
        {items.length === 0 && (
          <div className="card p-10 text-center text-ink-dim col-span-full">No campaigns yet.</div>
        )}
      </div>

      {showCreate && <CreateCampaignModal onClose={() => setShowCreate(false)} onCreated={() => { setShowCreate(false); fetchAll(); }} />}
      {attachId !== null && <AttachLeadsModal cid={attachId} onClose={() => setAttachId(null)} />}
      <CsvCampaignUploadModal
        open={showCsv}
        onClose={() => setShowCsv(false)}
        onLaunched={() => { setShowCsv(false); fetchAll(); }}
      />
    </div>
  );
}

function StatTile({ label, v, accent }: { label: string; v: number; accent: string }) {
  return (
    <div className="bg-hover/40 rounded-md px-2.5 py-1.5">
      <div className={`font-mono text-base font-semibold ${accent}`}>{v}</div>
      <div className="text-[10px] uppercase tracking-wider text-ink-faint mt-0.5">{label}</div>
    </div>
  );
}

function CreateCampaignModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/campaigns", { name });
      onCreated();
    } catch (err: unknown) {
      alert(err instanceof Error ? err.message : "Failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <form onSubmit={save} className="card p-6 w-full max-w-md space-y-4">
        <h3 className="text-lg font-semibold">New campaign</h3>
        <div>
          <label className="label block mb-1">Name *</label>
          <input className="input" required value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
          <button type="submit" disabled={busy} className="btn-primary">{busy ? "Saving…" : "Create"}</button>
        </div>
      </form>
    </div>
  );
}

function AttachLeadsModal({ cid, onClose }: { cid: number; onClose: () => void }) {
  const { isAdmin } = useAuth();
  const [leads, setLeads] = useState<Lead[]>([]);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<LeadsList>("/api/leads?limit=200").then((r) => setLeads(r.items));
  }, []);
  const toggle = (id: number) => {
    const s = new Set(selected);
    s.has(id) ? s.delete(id) : s.add(id);
    setSelected(s);
  };
  const attach = async () => {
    setBusy(true);
    try {
      await api.post(`/api/campaigns/${cid}/leads`, { lead_ids: Array.from(selected) });
      onClose();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="card p-6 w-full max-w-xl max-h-[80vh] flex flex-col">
        <h3 className="text-lg font-semibold mb-3">Attach leads to campaign</h3>
        <div className="flex-1 overflow-auto border border-border rounded-lg">
          {leads.map((l) => (
            <label key={l.id} className="flex items-center gap-3 px-3 py-2 border-b border-border last:border-b-0 hover:bg-hover cursor-pointer">
              <input type="checkbox" checked={selected.has(l.id)} onChange={() => toggle(l.id)} />
              <div className="flex-1 text-sm">{l.name ?? maskPhone(l.phone, isAdmin)}</div>
              <div className="text-xs text-ink-faint font-mono">{maskPhone(l.phone, isAdmin)}</div>
            </label>
          ))}
          {leads.length === 0 && <div className="p-8 text-center text-ink-dim">No leads.</div>}
        </div>
        <div className="flex justify-between items-center mt-4">
          <p className="text-sm text-ink-dim">{selected.size} selected</p>
          <div className="flex gap-2">
            <button onClick={onClose} className="btn-ghost">Cancel</button>
            <button onClick={attach} disabled={busy || selected.size === 0} className="btn-primary">
              {busy ? "Attaching…" : `Attach ${selected.size}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── CSV → campaign → start-dialing flow ─────────────────────────────
interface CsvCampaignResult {
  campaign_id: number;
  campaign_name: string;
  inserted: number;
  updated: number;
  skipped: number;
  attached: number;
  errors: string[];
}

function CsvCampaignUploadModal({
  open, onClose, onLaunched,
}: {
  open: boolean;
  onClose: () => void;
  onLaunched: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<CsvCampaignResult | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const toast = useToast();

  if (!open) return null;

  const reset = () => {
    setFile(null); setName(""); setResult(null); setBusy(false);
    if (inputRef.current) inputRef.current.value = "";
  };
  const close = () => { reset(); onClose(); };

  const pick = (f: File | undefined | null) => {
    if (!f) return;
    if (!f.name.toLowerCase().endsWith(".csv")) {
      toast.error("Invalid file", "Please choose a .csv file.");
      return;
    }
    setFile(f);
    setResult(null);
    // Auto-fill campaign name from filename if user hasn't typed one.
    if (!name) setName(f.name.replace(/\.csv$/i, ""));
  };

  const launch = async () => {
    if (!file) return;
    const trimmed = name.trim() || `CSV ${new Date().toLocaleDateString()}`;
    setBusy(true);
    try {
      const r = await api.upload<CsvCampaignResult>(
        "/api/campaigns/upload-and-start",
        file,
        { name: trimmed },
      );
      setResult(r);
      toast.success(
        "Campaign launched",
        `${r.attached} leads attached, dialer started.`,
      );
    } catch (err: unknown) {
      toast.error("Upload failed", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="card w-full max-w-md p-5 animate-slide-up shadow-lg">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-semibold flex items-center gap-2">
            <Upload className="h-5 w-5 text-brand" /> Upload CSV & start dialing
          </h3>
          <button onClick={close} className="btn-ghost p-1">
            <X className="h-4 w-4" />
          </button>
        </div>

        {!result && (
          <>
            <label
              htmlFor="csv-camp-input"
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault(); setDragOver(false);
                pick(e.dataTransfer.files?.[0]);
              }}
              className={`block w-full text-center border-2 border-dashed rounded-lg p-6 cursor-pointer transition-colors ${
                dragOver ? "border-brand bg-brand-subtle" : "border-border hover:border-ink-faint"
              }`}
            >
              <input
                id="csv-camp-input"
                ref={inputRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={(e) => pick(e.target.files?.[0])}
              />
              {file ? (
                <div className="flex flex-col items-center gap-2">
                  <FileText className="h-9 w-9 text-brand" />
                  <p className="font-medium text-ink">{file.name}</p>
                  <p className="text-xs text-ink-faint font-mono">{(file.size / 1024).toFixed(1)} KB</p>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 text-ink-dim">
                  <Upload className="h-9 w-9" />
                  <p className="font-medium">Drop CSV here, or click to choose</p>
                  <p className="text-xs text-ink-faint">
                    phone in <strong>column L</strong>, first name in <strong>col E</strong>,
                    last name in <strong>col G</strong> (10-digit Indian mobile; +91 / 0 / dashes / spaces OK)
                  </p>
                </div>
              )}
            </label>

            <div className="mt-4">
              <label className="label block mb-1">Campaign name</label>
              <input
                className="input"
                placeholder="e.g. May 25 batch"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
              <p className="text-xs text-ink-faint mt-1">
                A new campaign with this name will be created and started immediately.
                Results appear in <strong>Pipeline</strong> and <strong>Calls</strong> as the dialer works through.
              </p>
            </div>

            <div className="flex gap-2 mt-5">
              <button onClick={close} className="btn-ghost flex-1" disabled={busy}>
                Cancel
              </button>
              <button
                onClick={launch}
                disabled={!file || busy}
                className="btn-primary flex-1 py-2.5"
              >
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
                {busy ? "Launching…" : "Upload & start"}
              </button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className="flex flex-col items-center gap-3 py-5">
              <CheckCircle className="h-12 w-12 text-emerald-500" />
              <h4 className="text-lg font-semibold text-ink">Campaign launched</h4>
              <p className="text-sm text-ink-dim">"{result.campaign_name}" is now dialing.</p>
            </div>
            <div className="grid grid-cols-2 gap-2 text-center mb-3">
              <Stat label="Attached" value={result.attached} accent="text-brand" />
              <Stat label="Skipped" value={result.skipped} accent="text-amber-500" />
            </div>
            <div className="grid grid-cols-2 gap-2 text-center mb-4">
              <Stat label="New leads" value={result.inserted} accent="text-emerald-500" />
              <Stat label="Updated" value={result.updated} accent="text-sky-500" />
            </div>
            {result.errors.length > 0 && (
              <div className="card bg-rose-500/5 border-rose-500/20 p-3 mb-4">
                <div className="flex items-center gap-2 mb-2">
                  <AlertCircle className="h-4 w-4 text-rose-500" />
                  <p className="text-sm font-medium text-rose-500">
                    {result.errors.length} row{result.errors.length > 1 ? "s" : ""} skipped
                  </p>
                </div>
                <ul className="text-xs text-ink-dim space-y-1 max-h-32 overflow-y-auto">
                  {result.errors.slice(0, 10).map((err, i) => (
                    <li key={i} className="font-mono">{err}</li>
                  ))}
                  {result.errors.length > 10 && (
                    <li className="text-ink-faint italic">… and {result.errors.length - 10} more</li>
                  )}
                </ul>
              </div>
            )}
            <button onClick={() => { onLaunched(); reset(); }} className="btn-primary w-full">
              View campaign
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: number; accent: string }) {
  return (
    <div className="card p-3">
      <p className={`text-2xl font-bold ${accent}`}>{value}</p>
      <p className="text-[10px] uppercase tracking-widest text-ink-faint mt-1">{label}</p>
    </div>
  );
}
