"use client";

import { useState, useEffect, FormEvent, Suspense } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Loader2, LogIn } from "lucide-react";
import { Logo, PoweredBy } from "@/components/brand";

function LoginForm() {
  const { login } = useAuth();
  const searchParams = useSearchParams();
  const next = searchParams.get("next") || undefined;
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Mounted gate: skip SSR for the form so LastPass / 1Password icon
  // injection (which happens client-side after first paint) can't cause
  // a hydration mismatch. suppressHydrationWarning only covers a single
  // node and doesn't reach descendants, which is why the earlier fix
  // wasn't enough. Server renders a spinner placeholder; client mounts
  // the real form on first effect.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) {
    return (
      <div className="card p-6 flex justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-brand" />
      </div>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password, next);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      {/* suppressHydrationWarning — see signup/page.tsx note (LastPass injection). */}
      <form onSubmit={submit} className="card p-6 space-y-4" suppressHydrationWarning>
        <div>
          <label className="label block mb-1.5">Email</label>
          <input
            type="email"
            className="input"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            autoComplete="email"
            required
          />
        </div>
        <div>
          <label className="label block mb-1.5">Password</label>
          <input
            type="password"
            className="input"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
          />
        </div>
        {error && (
          <div className="text-sm text-danger bg-danger/10 border border-danger/30 rounded px-3 py-2">
            {error}
          </div>
        )}
        <button type="submit" className="btn-primary w-full py-2.5" disabled={busy}>
          {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
          Sign in
        </button>
      </form>

      {/* Auth navigation links */}
      <div className="mt-4 flex items-center justify-between text-sm">
        <Link
          href="/forgot-password"
          className="text-ink-dim hover:text-brand transition-colors"
        >
          Forgot password?
        </Link>
        <span className="text-ink-faint" aria-hidden="true">·</span>
        <Link
          href="/signup"
          className="text-ink-dim hover:text-brand transition-colors"
        >
          New here? Create an account
        </Link>
      </div>
    </>
  );
}

export default function LoginPage() {
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

        {/* useSearchParams() requires a Suspense boundary in Next.js 15 to
            avoid bailing out of static generation; the boundary lets the
            build prerender the shell while the form reads the URL at
            runtime. */}
        <Suspense
          fallback={
            <div className="card p-6 flex justify-center">
              <Loader2 className="h-5 w-5 animate-spin text-brand" />
            </div>
          }
        >
          <LoginForm />
        </Suspense>

        {/* Partner attribution — JATAS + TOMATOVATION */}
        <div className="mt-10 flex flex-col items-center gap-2">
          <Logo size={56} />
          <PoweredBy variant="bold" />
        </div>
      </div>
    </main>
  );
}
