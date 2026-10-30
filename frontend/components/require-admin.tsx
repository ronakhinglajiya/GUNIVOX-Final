"use client";

/**
 * Client-side route guard for admin-only pages.
 *
 * Wrap a page's children with <RequireAdmin>...</RequireAdmin> at the
 * very top of its return. Behaviour:
 *   - While auth is still loading, render nothing (avoids a flash of
 *     content before the role check runs).
 *   - If the user isn't an admin, redirect to /dashboard.
 *   - Once we know the user IS an admin, render the page.
 *
 * The actual data security still lives in the backend (every admin-only
 * endpoint has `require_admin`); this is just UX + defense-in-depth so a
 * counsellor pasting an admin URL doesn't see a half-rendered shell.
 */
import { ReactNode, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";

export function RequireAdmin({ children }: { children: ReactNode }) {
  const { isAdmin, loading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !isAdmin) {
      router.replace("/dashboard");
    }
  }, [loading, isAdmin, router]);

  if (loading || !isAdmin) return null;
  return <>{children}</>;
}
