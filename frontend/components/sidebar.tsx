"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  Users,
  UsersRound,
  PhoneCall,
  Megaphone,
  CalendarClock,
  Settings,
  LogOut,
  Phone,
  Flame,
  Receipt,
  Download,
  Inbox as InboxIcon,
  Trophy,
  BarChart3,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useInboxNotifier } from "@/lib/inbox-notifications";
import { PoweredBy } from "@/components/brand";
import clsx from "clsx";

// Dialer moved to its own page on 2026-05-20 (previously a docked side
// panel). It sits right under Dashboard so it's the second-most-
// prominent nav entry — the user spends most of their time on those two.
const NAV = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, role: "any" },
  { href: "/inbox",     label: "Inbox",     icon: InboxIcon,       role: "any" },
  { href: "/dialer",    label: "Dialer",    icon: Phone,           role: "any" },
  { href: "/pipeline",  label: "Pipeline",  icon: Flame,           role: "any" },
  { href: "/leads",     label: "Leads",     icon: Users,           role: "any" },
  { href: "/calls",     label: "Calls",     icon: PhoneCall,       role: "any" },
  { href: "/campaigns", label: "Campaigns", icon: Megaphone,       role: "any" },
  { href: "/callbacks", label: "Callbacks", icon: CalendarClock,   role: "any" },
  { href: "/closures",  label: "Closures",  icon: Trophy,          role: "any" },
  { href: "/team",      label: "Team",      icon: UsersRound,      role: "admin" },
  { href: "/analytics", label: "Analytics", icon: BarChart3,       role: "admin" },
  { href: "/billing",   label: "Usage",     icon: Receipt,         role: "admin" },
  { href: "/reports",   label: "Reports",   icon: Download,        role: "admin" },
  { href: "/settings",  label: "Settings",  icon: Settings,        role: "admin" },
] as const;

export function Sidebar() {
  const pathname = usePathname();
  const { user, isAdmin, logout } = useAuth();
  const { unreadHot, unreadWarm } = useInboxNotifier();

  return (
    <aside className="w-60 shrink-0 bg-surface border-r border-border flex flex-col">
      {/* Brand */}
      <div className="px-5 py-5 border-b border-border">
        <Link href="/dashboard" className="flex items-center justify-center group">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/portal.png"
            alt="Ganpat University Portal"
            className="h-12 w-auto object-contain transition-transform group-hover:scale-105"
          />
        </Link>
      </div>

      {/* Nav */}
      <nav className="flex-1 p-3 space-y-0.5">
        {NAV.map((item) => {
          if (item.role === "admin" && !isAdmin) return null;
          const Icon = item.icon;
          const active =
            pathname === item.href || pathname.startsWith(item.href + "/");
          return (
            <Link
              key={item.href}
              href={item.href}
              className={clsx(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-all relative",
                active
                  ? "bg-brand-subtle text-brand font-medium"
                  : "text-ink-dim hover:bg-hover hover:text-ink"
              )}
            >
              <Icon className="h-4 w-4" />
              {item.label}
              {/* Inbox unread badge — pulses if any HOT lead is waiting. */}
              {item.href === "/inbox" && (unreadHot > 0 || unreadWarm > 0) && (
                <span
                  className={clsx(
                    "ml-auto text-[10px] font-bold px-1.5 py-0.5 rounded-full tabular-nums",
                    unreadHot > 0
                      ? "bg-danger text-white animate-pulse"
                      : "bg-amber-500/20 text-amber-400"
                  )}
                  title={`${unreadHot} hot · ${unreadWarm} warm new`}
                >
                  {unreadHot + unreadWarm}
                </span>
              )}
              {active && !(item.href === "/inbox" && (unreadHot > 0 || unreadWarm > 0)) && (
                <span className="ml-auto h-1.5 w-1.5 rounded-full bg-brand" />
              )}
            </Link>
          );
        })}
      </nav>

      {/* User panel */}
      <div className="p-3 border-t border-border">
        <div className="px-3 py-2 mb-1">
          <p className="text-sm text-ink truncate font-medium">{user?.name}</p>
          <p className="text-[10px] uppercase tracking-widest text-ink-faint mt-0.5">
            {user?.role}
          </p>
        </div>
        <button onClick={logout} className="btn-ghost w-full justify-start text-sm">
          <LogOut className="h-4 w-4" />
          Logout
        </button>
        <div className="pt-3 mt-1 border-t border-border space-y-2">
          <p className="text-center text-[10px] text-ink-faint">
            Made with <span className="text-red-500">❤️</span> for{" "}
            <span className="font-semibold text-ink">Ganpat University</span>
          </p>
          <PoweredBy />
        </div>
      </div>
    </aside>
  );
}
