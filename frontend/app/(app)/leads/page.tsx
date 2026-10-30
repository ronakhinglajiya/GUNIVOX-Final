"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import type { Lead, LeadsList } from "@/lib/types";
import { Phone, Plus, Search } from "lucide-react";
import { DialerModal } from "@/components/dialer-modal";
import { useToast } from "@/lib/toast-context";
import { maskPhone } from "@/lib/phone";
import { useAuth } from "@/lib/auth-context";

export default function LeadsPage() {
  const { isAdmin } = useAuth();
  const [items, setItems] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [dialNumber, setDialNumber] = useState<string | null>(null);

  const fetchList = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: "50", offset: "0" });
      if (q) params.set("q", q);
      const r = await api.get<LeadsList>(`/api/leads?${params}`);
      setItems(r.items);
      setTotal(r.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const search = (e: React.FormEvent) => {
    e.preventDefault();
    fetchList();
  };

  const dial = (lead: Lead) => setDialNumber(lead.phone);

  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold text-ink">Leads</h1>
          <p className="text-sm text-ink-dim mt-1 font-mono">{total} total</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowCreate(true)} className="btn-primary">
            <Plus className="h-4 w-4" /> New lead
          </button>
        </div>
      </div>

      <form onSubmit={search} className="card p-2 flex items-center gap-2">
        <Search className="h-4 w-4 text-ink-faint ml-2" />
        <input
          className="input border-0 bg-transparent focus:shadow-none"
          placeholder="Search by name, phone, email, program…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </form>

      <div className="card overflow-hidden">
        {loading ? (
          <div className="p-10 text-center text-ink-dim">Loading…</div>
        ) : items.length === 0 ? (
          <div className="p-10 text-center text-ink-dim">No leads yet.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Phone</th>
                <th>Program</th>
                <th>Status</th>
                <th>Attempts</th>
                {isAdmin && <th className="text-right"></th>}
              </tr>
            </thead>
            <tbody>
              {items.map((l) => (
                <tr key={l.id}>
                  <td>
                    <Link
                      href={`/leads/${l.id}`}
                      className="text-ink hover:text-brand transition-colors"
                    >
                      {l.name ?? <span className="text-ink-faint">—</span>}
                    </Link>
                  </td>
                  <td className="font-mono text-xs">{maskPhone(l.phone, isAdmin)}</td>
                  <td>{l.program_interest ?? <span className="text-ink-faint">—</span>}</td>
                  <td>
                    <span className={l.dnd ? "badge-danger" : "badge-muted"}>
                      {l.dnd ? "DND" : l.status}
                    </span>
                  </td>
                  <td className="text-ink-dim font-mono">{l.attempt_count}</td>
                  {isAdmin && (
                    <td className="text-right">
                      <button onClick={() => dial(l)} className="btn-ghost text-xs" disabled={l.dnd}>
                        <Phone className="h-3 w-3" /> Dial
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showCreate && (
        <CreateLeadModal
          onClose={() => setShowCreate(false)}
          onCreated={() => { setShowCreate(false); fetchList(); }}
        />
      )}

      <DialerModal
        open={dialNumber !== null}
        defaultNumber={dialNumber ?? ""}
        onClose={() => setDialNumber(null)}
        onCalled={fetchList}
      />
    </div>
  );
}

function CreateLeadModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [phone, setPhone] = useState("+91");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [program, setProgram] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/api/leads", {
        phone,
        name: name || null,
        email: email || null,
        program_interest: program || null,
      });
      toast.success("Lead added", phone);
      onCreated();
    } catch (err: unknown) {
      toast.error("Failed to add lead", err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <form onSubmit={save} className="card p-6 w-full max-w-md space-y-4 animate-slide-up">
        <h3 className="text-lg font-semibold">New lead</h3>
        <div>
          <label className="label block mb-1">Phone *</label>
          <input className="input font-mono" required value={phone} onChange={(e) => setPhone(e.target.value)} />
        </div>
        <div>
          <label className="label block mb-1">Name</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label block mb-1">Email</label>
          <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label block mb-1">Program interest</label>
          <input className="input" value={program} onChange={(e) => setProgram(e.target.value)} />
        </div>
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
          <button type="submit" disabled={busy} className="btn-primary">{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </div>
  );
}
