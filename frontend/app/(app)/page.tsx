"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Building2, CheckCircle2, ClipboardCheck, MailOpen, PlayCircle, Users } from "lucide-react";
import Link from "next/link";
import { ButtonLink, Card, CardHeader } from "@/components/ui/primitives";
import { PageHeader } from "@/components/ui/layout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { ROLE_LABEL, useMe } from "@/lib/auth";
import { cn, fmtDateTime, relTime } from "@/lib/format";
import type { Dashboard, Role } from "@/lib/types";

const STEPS: { n: number; who: Role; title: string; text: string; href: string; icon: React.ReactNode }[] = [
  { n: 1, who: "coordinator", title: "Company email → consent form", icon: <MailOpen className="size-4" />, href: "/inbox",
    text: "A company emails the placement mailbox. Agents read it, create the drive and send every student its consent form." },
  { n: 2, who: "officer", title: "Run the drive", icon: <PlayCircle className="size-4" />, href: "/agent",
    text: "Agents apply the company's criteria to students who said YES and prepare the list for approval." },
  { n: 3, who: "approver", title: "Approve and send", icon: <ClipboardCheck className="size-4" />, href: "/approvals",
    text: "Check the list, ask students with missing details to fill a form, approve. The list is sent and emailed to the company." },
];

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
    is("officer") && m.unresolved_exceptions, is("officer") && m.open_clash_exceptions,
  ].some(Boolean) : false;

  return (
    <>
      <PageHeader title={`Hello, ${me.data?.name.split(" (")[0] ?? ""}`} description={role ? `Signed in as ${ROLE_LABEL[role]}` : undefined} />

      <div className="grid gap-3 md:grid-cols-3">
        {STEPS.map((s) => {
          const mine = role === s.who;
          return (
            <Link key={s.n} href={s.href} className={cn("group rounded-[var(--radius-card)] border p-4 transition-colors",
              mine ? "border-accent bg-accent-soft/60" : "border-line bg-surface hover:bg-subtle")}>
              <div className="flex items-center gap-2 text-[12px] font-semibold text-muted">
                <span className={cn("flex size-6 items-center justify-center rounded-full text-[12px]", mine ? "bg-accent text-white" : "bg-subtle text-ink ring-1 ring-line")}>{s.n}</span>
                {ROLE_LABEL[s.who]}{mine && <span className="text-accent">· you</span>}
              </div>
              <p className="mt-2 flex items-center gap-2 text-[14px] font-semibold text-ink">{s.icon}{s.title}</p>
              <p className="mt-1 text-[12.5px] leading-snug text-muted">{s.text}</p>
            </Link>
          );
        })}
      </div>

      <div className="mt-5 grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardHeader title="Needs your attention" />
          {!m ? <LoadingRows rows={3} /> : !tasks ? (
            <EmptyState icon={<CheckCircle2 className="size-5" />} title="Nothing needs you right now" description="The agents keep working in the background; new items appear here." />
          ) : (
            <ul className="divide-y divide-line">
              <Task show={is("coordinator")} count={m.inbox_needs_review} text="company email(s) to check" href="/inbox" action="Open" />
              <Task show={is("officer")} count={toRun.length} text="drive(s) waiting to be run" href="/drives" action="Drives" />
              <Task show={is("approver")} count={m.pending_approvals} text="list(s) waiting for approval" href="/approvals?status=PENDING" action="Review" />
              <Task show={is("officer")} count={m.unresolved_exceptions} text="issue(s) to resolve" href="/exceptions" action="Open" />
              <Task show={is("officer")} count={m.open_clash_exceptions} text="schedule clash(es)" href="/conflicts" action="Resolve" />
            </ul>
          )}
          <div className="flex gap-4 border-t border-line px-4 py-3 text-[12.5px] text-muted">
            <Link href="/students" className="flex items-center gap-1.5 hover:text-ink"><Users className="size-3.5" />{m?.total_students ?? "—"} students</Link>
            <Link href="/drives" className="flex items-center gap-1.5 hover:text-ink"><Building2 className="size-3.5" />{m?.upcoming_drives ?? "—"} upcoming drives</Link>
          </div>
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
    </>
  );
}
