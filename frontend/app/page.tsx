"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { Logo, PoweredBy } from "@/components/brand";
import { Loader2 } from "lucide-react";

export default function Home() {
  const router = useRouter();
  const { user, loading } = useAuth();

  useEffect(() => {
    if (loading) return;
    router.replace(user ? "/dashboard" : "/login");
  }, [user, loading, router]);

  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-bg gap-8 px-4">
      <Logo size={72} />
      <div className="text-center">
        <h1 className="text-3xl font-bold text-ink">GUNIVOX</h1>
        <p className="text-[10px] uppercase tracking-widest text-ink-faint mt-2">
          admissions dialer console
        </p>
      </div>
      <div className="flex items-center gap-2 text-sm text-ink-dim">
        <Loader2 className="h-4 w-4 animate-spin text-brand" />
        Loading…
      </div>
      <div className="absolute bottom-8">
        <PoweredBy />
      </div>
    </div>
  );
}
