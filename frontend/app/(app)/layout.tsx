"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { InboxNotifierProvider } from "@/lib/inbox-notifications";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { Loader2 } from "lucide-react";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  if (loading || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-bg">
        <Loader2 className="h-6 w-6 animate-spin text-brand" />
      </div>
    );
  }

  // Two-column shell: sidebar (left) | content (center, with topbar).
  // InboxNotifierProvider runs the site-wide /api/inbox poll so a fresh
  // hot lead toasts the counsellor no matter what page they're on.
  return (
    <InboxNotifierProvider>
      <div className="flex h-screen bg-bg text-ink">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Topbar />
          <main className="flex-1 overflow-auto p-6 bg-bg">{children}</main>
        </div>
      </div>
    </InboxNotifierProvider>
  );
}
