"use client";

import { useState, useEffect, FormEvent, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2, KeyRound, Eye, EyeOff, ArrowLeft } from "lucide-react";
import { Logo, PoweredBy } from "@/components/brand";
import { useToast } from "@/lib/toast-context";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { success } = useToast();

  const [token, setToken] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Mounted gate — see login/page.tsx note (LastPass hydration mismatch).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Pre-fill token from ?token=... query param (editable).
  useEffect(() => {
    const t = searchParams.get("token");
    if (t) setToken(t);
  }, [searchParams]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/auth/reset-password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, new_password: newPassword }),
      });
      if (!res.ok) {
        let detail = "Password reset failed.";
        try {
          const body = await res.json();
          if (body && typeof body === "object") {
            if (typeof body.detail === "string") detail = body.detail;
            else if (typeof body.message === "string") detail = body.message;
          }
        } catch {
          try {
            const text = await res.text();
            if (text) detail = text;
          } catch {
            /* ignore */
          }
        }
        throw new Error(detail);
      }
      success("Password reset. Sign in.");
      router.push("/login");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Password reset failed.");
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
        ) : (
        <form onSubmit={submit} className="card p-6 space-y-4">
          <div>
            <h1 className="text-lg font-semibold text-ink">Reset password</h1>
            <p className="text-xs text-ink-dim mt-1">
              Paste the token from your reset email and choose a new password.
            </p>
          </div>

          <div>
            <label className="label block mb-1.5">Token</label>
            <input
              type="text"
              className="input font-mono text-xs"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="Paste reset token"
              autoComplete="off"
              required
            />
          </div>

          <div>
            <label className="label block mb-1.5">New password</label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                className="input pr-10"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                autoComplete="new-password"
                required
                minLength={8}
              />
              <button
                type="button"
                onClick={() => setShowPassword((s) => !s)}
                className="absolute inset-y-0 right-0 px-3 flex items-center text-ink-faint hover:text-ink"
                aria-label={showPassword ? "Hide password" : "Show password"}
                tabIndex={-1}
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          {error && (
            <div className="text-sm text-danger bg-danger/10 border border-danger/30 rounded px-3 py-2">
              {error}
            </div>
          )}

          <button
            type="submit"
            className="btn-primary w-full py-2.5"
            disabled={busy || !token || !newPassword}
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <KeyRound className="h-4 w-4" />
            )}
            Reset password
          </button>

          <div className="pt-2 text-center">
            <Link
              href="/login"
              className="inline-flex items-center gap-1.5 text-xs text-ink-dim hover:text-ink"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              Back to login
            </Link>
          </div>
        </form>
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

export default function ResetPasswordPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-bg flex items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-ink-dim" />
        </main>
      }
    >
      <ResetPasswordForm />
    </Suspense>
  );
}
