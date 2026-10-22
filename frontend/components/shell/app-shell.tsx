"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity, BadgeCheck, Bot, Building2, CalendarClock, ClipboardCheck, FileSearch, FlaskConical, Gauge,
  Inbox, LogOut, Mail, MailOpen, Menu, Network, Search, Settings, ShieldCheck, Users, X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import * as React from "react";
import { api } from "@/lib/api";
import { ROLE_LABEL, useMe } from "@/lib/auth";
import { cn } from "@/lib/format";
import type { Dashboard, Health } from "@/lib/types";
import { Skeleton } from "../ui/primitives";

type NavItem = { href: string; label: string; icon: React.ComponentType<{ className?: string }>; badge?: (d?: Dashboard) => number | undefined };

const NAV: { section: string; items: NavItem[] }[] = [
  { section: "Operate", items: [
    { href: "/", label: "Overview", icon: Gauge },
    { href: "/inbox", label: "Company Inbox", icon: MailOpen, badge: (d) => d?.metrics.inbox_needs_review },
    { href: "/agents", label: "Agent Crew", icon: Network },
    { href: "/agent", label: "Agent Control Room", icon: Bot },
    { href: "/approvals", label: "Approval Center", icon: ClipboardCheck, badge: (d) => d?.metrics.pending_approvals },
    { href: "/verification", label: "Verification Center", icon: BadgeCheck, badge: (d) => d?.metrics.verification_attention },
    { href: "/exceptions", label: "Exception Inbox", icon: Inbox, badge: (d) => d?.metrics.unresolved_exceptions },
    { href: "/communications", label: "Communications", icon: Mail },
  ] },
  { section: "Records", items: [
    { href: "/students", label: "Student Registry", icon: Users },
    { href: "/drives", label: "Employer Drives", icon: Building2 },
    { href: "/conflicts", label: "Schedule Conflicts", icon: CalendarClock, badge: (d) => d?.metrics.open_clash_exceptions },
  ] },
  { section: "Assurance", items: [
    { href: "/test-lab", label: "Test Lab", icon: FlaskConical },
    { href: "/audit", label: "Audit Explorer", icon: FileSearch },
    { href: "/settings", label: "Settings & Integrations", icon: Settings },
  ] },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const dash = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/api/dashboard"), enabled: !!me.data, refetchInterval: 20_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/api/health"), staleTime: 60_000 });
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState("");

  React.useEffect(() => setOpen(false), [pathname]);
  React.useEffect(() => {
    if (me.isError) router.replace(`/login?next=${encodeURIComponent(pathname)}`);
  }, [me.isError, pathname, router]);

  async function logout() {
    await api.post("/api/auth/logout");
    qc.clear();
    router.replace("/login");
  }

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname.startsWith(href));

  const sidebar = (
    <nav aria-label="Main" className="flex h-full flex-col">
      <div className="flex h-14 items-center gap-2 border-b border-white/10 px-4">
        <div className="flex size-7 items-center justify-center rounded-md bg-white/10 ring-1 ring-white/15"><ShieldCheck className="size-4 text-white" /></div>
        <div className="leading-tight">
          <div className="text-[14px] font-semibold text-white">PlacementOS</div>
          <div className="text-[10.5px] text-white/55">Placement operations</div>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-3">
        {NAV.map((group) => (
          <div key={group.section} className="mb-4">
            <div className="px-2 pb-1 text-[10.5px] font-semibold uppercase tracking-wider text-white/40">{group.section}</div>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                const badge = item.badge?.(dash.data);
                const active = isActive(item.href);
                return (
                  <li key={item.href}>
                    <Link href={item.href} aria-current={active ? "page" : undefined}
                      className={cn("flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] transition-colors",
                        active ? "bg-white/12 text-white" : "text-white/70 hover:bg-white/6 hover:text-white")}>
                      <item.icon className="size-4 shrink-0" />
                      <span className="flex-1 truncate">{item.label}</span>
                      {!!badge && <span className="rounded-full bg-white/15 px-1.5 text-[11px] font-semibold tabular text-white">{badge}</span>}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
      <div className="border-t border-white/10 p-3 text-[11px] text-white/55">
        {health.data ? (
          <div className="space-y-1">
            <div className="flex items-center gap-1.5"><Activity className="size-3" /> API {health.data.status} · {health.data.database_dialect}</div>
            <div>{health.data.planner === "LLM" ? `LLM planner: ${health.data.llm_model}` : "Deterministic fallback planner"}</div>
          </div>
        ) : <span>Checking health…</span>}
      </div>
    </nav>
  );

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 bg-ink lg:block">{sidebar}</aside>
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div className="absolute inset-0 bg-ink/40" onClick={() => setOpen(false)} />
          <aside className="absolute inset-y-0 left-0 w-64 bg-ink">
            <button className="absolute right-2 top-3 rounded p-1 text-white/70" onClick={() => setOpen(false)} aria-label="Close menu"><X className="size-5" /></button>
            {sidebar}
          </aside>
        </div>
      )}
      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur">
          <button className="rounded p-1.5 text-muted hover:bg-subtle lg:hidden" onClick={() => setOpen(true)} aria-label="Open menu"><Menu className="size-5" /></button>
          <form role="search" className="relative w-full max-w-md" onSubmit={(e) => { e.preventDefault(); if (q.trim()) router.push(`/students?q=${encodeURIComponent(q.trim())}`); }}>
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-faint" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search students by roll number or name…"
              aria-label="Search students" className="h-9 w-full rounded-md border border-line bg-subtle pl-8 pr-3 text-[13px] placeholder:text-faint focus:border-accent focus:bg-surface focus:outline-none focus:ring-2 focus:ring-accent/20" />
          </form>
          <div className="ml-auto flex items-center gap-3">
            {health.data?.demo_mode && <span className="hidden rounded-md bg-warn-soft px-2 py-1 text-[11px] font-semibold text-warn ring-1 ring-warn/25 sm:inline">DEMO · synthetic data</span>}
            {me.data ? (
              <div className="flex items-center gap-2">
                <div className="hidden text-right leading-tight sm:block">
                  <div className="text-[13px] font-medium text-ink">{me.data.name}</div>
                  <div className="text-[11px] text-muted">{ROLE_LABEL[me.data.role]}</div>
                </div>
                <button onClick={logout} className="rounded-md p-2 text-muted hover:bg-subtle hover:text-ink" aria-label="Sign out" title="Sign out"><LogOut className="size-4" /></button>
              </div>
            ) : <Skeleton className="h-8 w-32" />}
          </div>
        </header>
        <main className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6">
          {me.data ? children : <div className="space-y-3"><Skeleton className="h-8 w-64" /><Skeleton className="h-40 w-full" /></div>}
        </main>
      </div>
    </div>
  );
}
