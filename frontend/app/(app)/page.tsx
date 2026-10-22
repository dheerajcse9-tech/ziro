"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bot, Building2, CheckCircle2, MailCheck, Send, Users } from "lucide-react";
import { runTitle } from "@/components/agents/agent-flow";
import Link from "next/link";
import { ButtonLink, Card, CardHeader } from "@/components/ui/primitives";
import { PageHeader } from "@/components/ui/layout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { ROLE_INFO, ROLE_LABEL, useMe } from "@/lib/auth";
import { fmtDateTime, relTime } from "@/lib/format";
import type { Dashboard, Role } from "@/lib/types";

function Task({ show, count, text, href, action }: { show: boolean; count: number; text: string; href: string; action: string }) {
  if (!show || count === 0) return null;
  return (
    <li className="flex items-center justify-between gap-3 px-4 py-3">
      <span className="text-[13.5px]"><strong className="tabular">{count}</strong> {text}</span>
      <ButtonLink size="sm" variant="primary" href={href} icon={<ArrowRight className="size-3.5" />}>{action}</ButtonLink>
    </li>
  );
}

export default function HomePage() {
  const me = useMe();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/api/dashboard"), refetchInterval: 20_000 });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const m = d?.metrics;
  const role = me.data?.role;
  const is = (...roles: Role[]) => !!role && (role === "admin" || roles.includes(role));
  const toRun = (d?.drive_readiness ?? []).filter((r) => r.stage === "Consent closed — ready to run" || r.stage === "Collecting consent" || r.stage === "Not evaluated");
  const tasks = m ? [
    is("coordinator") && m.inbox_needs_review, is("officer") && toRun.length, is("approver") && m.pending_approvals,
    is("officer") && m.unresolved_exceptions,
  ].some(Boolean) : false;

  return (
    <>
      <PageHeader title={`Hello, ${me.data?.name.split(" (")[0] ?? ""}`} description={role ? `${ROLE_LABEL[role]} — ${ROLE_INFO[role].job}` : undefined} />

      <div className="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={<Users className="size-4" />} label="Students" value={m?.total_students} href="/students" />
        <Kpi icon={<Building2 className="size-4" />} label="Upcoming drives" value={m?.upcoming_drives} href="/drives" />
        <Kpi icon={<MailCheck className="size-4" />} label="Consent answers" href="/drives"
          value={d ? `${d.activity.consent.accepted + d.activity.consent.declined}/${d.activity.consent.sent}` : undefined}
          hint={d ? `${d.activity.consent.accepted} YES · ${d.activity.consent.declined} NO · ${d.activity.consent.pending} waiting` : undefined}
          bar={d && d.activity.consent.sent ? (d.activity.consent.accepted + d.activity.consent.declined) / d.activity.consent.sent : undefined} />
        <Kpi icon={<Send className="size-4" />} label="Shortlisted & sent to companies" href="/drives"
          value={d ? d.shortlisted.reduce((n, r) => n + r.students, 0) : undefined}
          hint={d ? `${d.shortlisted.length} compan${d.shortlisted.length === 1 ? "y" : "ies"} · ${d.activity.emails_sent} emails sent` : undefined} />
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader title="Your to-do list" />
          {!m ? <LoadingRows rows={3} /> : !tasks ? (
            <EmptyState icon={<CheckCircle2 className="size-5" />} title="Nothing needs you right now" description="The agents keep working in the background; new items appear here." />
          ) : (
            <ul className="divide-y divide-line">
              <Task show={is("coordinator")} count={m.inbox_needs_review} text="company email(s) to check" href="/inbox" action="Open" />
              <Task show={is("officer")} count={toRun.length} text="drive(s) waiting to be run" href="/drives" action="Drives" />
              <Task show={is("approver")} count={m.pending_approvals} text="list(s) waiting for approval" href="/approvals?status=PENDING" action="Review" />
              <Task show={is("officer")} count={m.unresolved_exceptions} text="issue(s) to resolve" href="/exceptions" action="Open" />
            </ul>
          )}
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader title="Upcoming drives" actions={<ButtonLink size="sm" href="/drives">All drives</ButtonLink>} />
          {!d ? <LoadingRows rows={4} /> : d.drive_readiness.length === 0 ? (
            <EmptyState icon={<Building2 className="size-5" />} title="No drives yet" description="Drives are created automatically when a company emails the placement mailbox." />
          ) : (
            <ul className="divide-y divide-line">
              {d.drive_readiness.map((r) => (
                <li key={r.drive_id}>
                  <Link href={`/drives/${r.drive_id}`} className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-subtle">
                    <span className="min-w-0">
                      <span className="block truncate text-[13.5px] font-medium text-ink">{r.title}</span>
                      <span className="text-[12px] text-muted">{fmtDateTime(r.starts_at, r.timezone)}
                        {r.consent && ` · ${r.consent.accepted} of ${r.consent.sent} said YES`}
                        {r.consent?.status === "OPEN" && ` · form closes ${relTime(r.consent.closes_at)}`}</span>
                    </span>
                    <StatusBadge status={r.stage} label={r.stage} className="shrink-0"
                      tone={r.stage === "Verified" ? "ok" : r.stage === "Awaiting approval" ? "accent" : r.stage.startsWith("Consent closed") ? "warn" : r.stage.startsWith("Failed") ? "bad" : "info"} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-4 grid items-start gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader title="Shortlisted per company" description="Verified lists that reached each company" />
          {!d ? <LoadingRows rows={3} /> : d.shortlisted.length === 0 ? (
            <EmptyState title="No lists sent yet" description="A list appears here after the approver approves it and it is checked." />
          ) : (
            <ul className="space-y-3 p-4">
              {d.shortlisted.map((r) => {
                const max = Math.max(...d.shortlisted.map((x) => x.students), 1);
                return (
                  <li key={r.drive_id}>
                    <Link href={`/drives/${r.drive_id}`} className="block">
                      <div className="mb-1 flex justify-between gap-2 text-[13px]"><span className="truncate text-ink">{r.employer}</span><span className="tabular font-semibold">{r.students}</span></div>
                      <div className="h-2.5 rounded-full bg-subtle"><div className="h-2.5 rounded-full bg-accent" style={{ width: `${(r.students / max) * 100}%` }} /></div>
                    </Link>
                  </li>
                );
              })}
            </ul>
          )}
        </Card>
        <Card className="lg:col-span-3">
          <CardHeader title="Agents at work" description={d ? `${d.activity.agent_actions_7d} agent actions in the last 7 days` : undefined}
            actions={<ButtonLink size="sm" href="/agent">All tasks</ButtonLink>} />
          {!d ? <LoadingRows rows={4} /> : d.recent_runs.length === 0 ? (
            <EmptyState icon={<Bot className="size-5" />} title="No agent tasks yet" description="They start when a company email arrives." />
          ) : (
            <ul className="divide-y divide-line">
              {d.recent_runs.map((r) => (
                <li key={r.id}>
                  <Link href={`/agent/${r.id}`} className="flex items-center justify-between gap-3 px-4 py-2.5 hover:bg-subtle">
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium text-ink">{runTitle(r)}</span>
                      <span className="block truncate text-[12px] text-muted">{r.drive_title ?? "—"} · {relTime(r.created_at)}</span>
                    </span>
                    <StatusBadge status={r.status} className="shrink-0" />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function Kpi({ icon, label, value, hint, href, bar }: { icon: React.ReactNode; label: string; value?: number | string; hint?: string; href: string; bar?: number }) {
  return (
    <Link href={href} className="rounded-[var(--radius-card)] border border-line bg-surface p-4 transition-colors hover:bg-subtle">
      <p className="flex items-center gap-1.5 text-[12.5px] text-muted">{icon}{label}</p>
      <p className="mt-1 text-[28px] font-semibold tabular text-ink">{value ?? "—"}</p>
      {bar !== undefined && <div className="mt-1 h-1.5 rounded-full bg-subtle"><div className="h-1.5 rounded-full bg-accent" style={{ width: `${Math.round(bar * 100)}%` }} /></div>}
      {hint && <p className="mt-1 text-[12px] text-muted">{hint}</p>}
    </Link>
  );
}
