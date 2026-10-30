"use client";

import { useState, useEffect, FormEvent } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useToast } from "@/lib/toast-context";
import { Loader2, UserPlus, Eye, EyeOff } from "lucide-react";
import { Logo, PoweredBy } from "@/components/brand";
import type { TokenResponse } from "@/lib/types";

export default function SignupPage() {
  const router = useRouter();
  const { refresh } = useAuth();
  const toast = useToast();

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Mounted gate — see login/page.tsx note. Skip SSR for the form so
  // LastPass / 1Password icon injection can't cause a hydration mismatch.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<TokenResponse>("/api/auth/signup", {
        email,
        name,
        password,
      });
      setToken(res.access_token);
      await refresh();
      toast.success("Welcome to Gunivox", `Signed in as ${res.user.email}`);
      router.push("/dashboard");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Signup failed";
      setError(msg);
      toast.error("Signup failed", msg);
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
            <h1 className="text-lg font-semibold text-ink">Create your account</h1>
            <p className="text-xs text-ink-dim mt-1">
              Counsellor signup for the Gunivox admissions portal.
            </p>
          </div>

          <div>
            <label className="label block mb-1.5">Name</label>
            <input
              type="text"
              className="input"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoComplete="name"
              required
            />
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
            />
          </div>
          <div>
            <label className="label block mb-1.5">Password</label>
            <div className="relative">
              <input
                type={showPassword ? "text" : "password"}
                className="input pr-10"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="new-password"
                minLength={6}
                required
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex items-center px-3 text-ink-faint hover:text-ink"
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

          <button type="submit" className="btn-primary w-full py-2.5" disabled={busy}>
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UserPlus className="h-4 w-4" />
            )}
            Create account
          </button>

          <p className="text-xs text-ink-dim text-center">
            Already have an account?{" "}
            <Link href="/login" className="text-ink hover:underline font-medium">
              Log in
            </Link>
          </p>
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
