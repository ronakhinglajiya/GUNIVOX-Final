"use client";

import { useState, useEffect, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Loader2, Mail, ArrowLeft, CheckCircle2 } from "lucide-react";
import { Logo, PoweredBy } from "@/components/brand";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type ForgotResponse = {
  ok: true;
  message: string;
  debug_token?: string;
  debug_user_email?: string;
};

export default function ForgotPasswordPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [debugToken, setDebugToken] = useState<string | null>(null);
  // Mounted gate — see login/page.tsx note (LastPass hydration mismatch).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/auth/forgot-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
      });
      if (!res.ok) {
        throw new Error(`Request failed (${res.status})`);
      }
      const data: ForgotResponse = await res.json();
      if (data.debug_token) {
        setDebugToken(data.debug_token);
      }
      setSubmitted(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="min-h-screen bg-bg flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-md">
        {/* Product brand */}
        <div className="flex items-center justify-center mb-8">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/portal.png"
            alt="Ganpat University Portal"
            className="h-16 w-auto object-contain"
          />
        </div>

        {!mounted ? (
          <div className="card p-6 flex justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-brand" />
          </div>
        ) : !submitted ? (
          <form onSubmit={submit} className="card p-6 space-y-4">
            <div>
              <h1 className="text-lg font-semibold text-ink mb-1">
                Forgot your password?
              </h1>
              <p className="text-sm text-ink-faint">
                Enter your email and we&apos;ll send you a link to reset it.
              </p>
            </div>
            <div>
              <label className="label block mb-1.5">Email</label>
              <input
                type="email"
                className="input"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                required
                autoFocus
              />
            </div>
            {error && (
              <div className="text-sm text-danger bg-danger/10 border border-danger/30 rounded px-3 py-2">
                {error}
              </div>
            )}
            <button
              type="submit"
              className="btn-primary w-full py-2.5"
              disabled={busy}
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Mail className="h-4 w-4" />
              )}
              Send reset link
            </button>
            <div className="pt-1 text-center">
              <Link
                href="/login"
                className="inline-flex items-center gap-1.5 text-sm text-ink-faint hover:text-ink transition-colors"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Back to login
              </Link>
            </div>
          </form>
        ) : (
          <div className="card p-6 space-y-4">
            <div className="flex flex-col items-center text-center">
              <div className="h-12 w-12 rounded-full bg-success/10 border border-success/30 flex items-center justify-center mb-3">
                <CheckCircle2 className="h-6 w-6 text-success" />
              </div>
              <h1 className="text-lg font-semibold text-ink mb-1">
                Check your inbox
              </h1>
              <p className="text-sm text-ink-faint">
                If an account exists for that email, a reset link has been sent.
              </p>
            </div>

            {debugToken && (
              <div className="rounded border border-yellow-500/40 bg-yellow-500/10 p-3 space-y-2">
                <div className="text-xs font-semibold text-yellow-500 uppercase tracking-wide">
                  Demo note
                </div>
                <p className="text-xs text-ink-faint">
                  Email isn&apos;t wired up in demo mode. Use this token to
                  continue:
                </p>
                <code className="block text-xs font-mono break-all bg-bg/60 border border-yellow-500/20 rounded px-2 py-1.5 text-ink">
                  {debugToken}
                </code>
                <button
                  type="button"
                  onClick={() =>
                    router.push(`/reset-password?token=${debugToken}`)
                  }
                  className="btn-primary w-full py-2"
                >
                  Continue to reset
                </button>
              </div>
            )}

            <div className="pt-1 text-center">
              <Link
                href="/login"
                className="inline-flex items-center gap-1.5 text-sm text-ink-faint hover:text-ink transition-colors"
              >
                <ArrowLeft className="h-3.5 w-3.5" />
                Back to login
              </Link>
            </div>
          </div>
        )}

        {/* Partner attribution — JATAS + TOMATOVATION */}
        <div className="mt-10 flex flex-col items-center gap-2">
          <Logo size={56} />
          <PoweredBy variant="bold" />
        </div>
      </div>
    </main>
  );
}
