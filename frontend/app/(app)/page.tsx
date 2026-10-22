"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, BadgeCheck, Bot, MailOpen, CalendarClock, ClipboardCheck, GraduationCap, Inbox, UserCheck, UserX } from "lucide-react";
import Link from "next/link";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ButtonLink, Card, CardHeader } from "@/components/ui/primitives";
import { KpiCard, PageHeader } from "@/components/ui/layout";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { PlannerBadge, StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { fmtDateTime, fmtSlot, relTime } from "@/lib/format";
import type { Dashboard } from "@/lib/types";

export default function OverviewPage() {
  const me = useMe();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<Dashboard>("/api/dashboard"), refetchInterval: 20_000 });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  const m = d?.metrics;
  const chartData = (d?.drive_readiness ?? []).filter((r) => r.included + r.excluded + r.held > 0).map((r) => ({
    name: r.employer, Included: r.included, Held: r.held, Excluded: r.excluded,
  }));

  return (
    <>
      <PageHeader
        title="Overview"
        description="Live operational status computed from the database. Every card opens the underlying records."
        actions={<>
          {can(me.data, "manageDrives") && <ButtonLink href="/inbox" icon={<MailOpen className="size-4" />}>Company Inbox</ButtonLink>}
          {can(me.data, "runAgent") && <ButtonLink href="/agent" variant="primary" icon={<Bot className="size-4" />}>Delegate a task to the agent</ButtonLink>}
        </>}
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <KpiCard label="Students on roster" value={m?.total_students ?? "—"} href="/students" icon={<GraduationCap className="size-4" />} />
        <KpiCard label="Upcoming drives" value={m?.upcoming_drives ?? "—"} href="/drives" icon={<CalendarClock className="size-4" />} />
        <KpiCard label="Eligible (latest evaluations)" value={m?.eligible_candidates ?? "—"} hint={m ? `${m.included_candidates} on proposed rosters` : undefined} href="/drives" tone="ok" icon={<UserCheck className="size-4" />} />
        <KpiCard label="Excluded (latest evaluations)" value={m?.excluded_candidates ?? "—"} href="/drives" icon={<UserX className="size-4" />} />
        <KpiCard label="Pending approvals" value={m?.pending_approvals ?? "—"} href="/approvals?status=PENDING" tone={m?.pending_approvals ? "accent" : "neutral"} icon={<ClipboardCheck className="size-4" />} />
        <KpiCard label="Unresolved exceptions" value={m?.unresolved_exceptions ?? "—"} href="/exceptions" tone={m?.unresolved_exceptions ? "warn" : "neutral"} icon={<Inbox className="size-4" />} />
        <KpiCard label="Overlapping drive pairs" value={m?.drive_conflicts ?? "—"} hint={m ? `${m.open_clash_exceptions} student clashes open` : undefined} href="/conflicts" tone={m?.open_clash_exceptions ? "warn" : "neutral"} icon={<AlertTriangle className="size-4" />} />
        <KpiCard label="Uploads needing attention" value={m?.verification_attention ?? "—"} hint="Not submitted or not VERIFIED" href="/verification" tone={m?.verification_attention ? "bad" : "neutral"} icon={<BadgeCheck className="size-4" />} />
      </div>

      <div className="mt-6 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Drive readiness" description="Pipeline stage per upcoming drive, from the latest eligibility decisions, approvals and verifications." />
          {!d ? <LoadingRows rows={4} /> : d.drive_readiness.length === 0 ? <EmptyState title="No upcoming drives" action={<ButtonLink href="/drives">Create a drive</ButtonLink>} /> : (
            <div className="overflow-x-auto">
              <table className="data-table w-full">
                <thead><tr><th>Drive</th><th>Slot</th><th>Stage</th><th className="text-right">Included</th><th className="text-right">Held</th><th className="text-right">Excluded</th><th className="text-right">Open issues</th></tr></thead>
                <tbody>
                  {d.drive_readiness.map((r) => (
                    <tr key={r.drive_id}>
                      <td><Link href={`/drives/${r.drive_id}`} className="font-medium text-ink hover:underline">{r.title}</Link></td>
                      <td className="whitespace-nowrap text-muted">{fmtDateTime(r.starts_at, r.timezone)}</td>
                      <td><StatusBadge status={r.stage} tone={r.stage === "Verified" ? "ok" : r.stage === "Awaiting approval" ? "accent" : r.stage.startsWith("Failed") ? "bad" : "neutral"} label={r.stage} /></td>
                      <td className="text-right tabular">{r.included}</td>
                      <td className="text-right tabular">{r.held || <span className="text-faint">0</span>}</td>
                      <td className="text-right tabular">{r.excluded}</td>
                      <td className="text-right tabular">{r.open_exceptions ? <Link href={`/exceptions?drive_id=${r.drive_id}`} className="font-medium text-warn hover:underline">{r.open_exceptions}</Link> : <span className="text-faint">0</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card>
          <CardHeader title="Upcoming schedule" description="Drive-local times" />
          {!d ? <LoadingRows rows={4} /> : d.upcoming_schedule.length === 0 ? <EmptyState title="Nothing scheduled" /> : (
            <ul className="divide-y divide-line">
              {d.upcoming_schedule.map((s) => (
                <li key={s.drive_id} className="px-4 py-2.5">
                  <Link href={`/drives/${s.drive_id}`} className="text-[13px] font-medium text-ink hover:underline">{s.title}</Link>
                  <div className="text-[12px] text-muted">{fmtSlot(s.starts_at, s.ends_at, s.timezone)}{s.location ? ` · ${s.location}` : ""}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="Roster composition by drive" description="Counts from the latest persisted evaluation of each drive (not estimates)." />
          <div className="h-64 px-2 py-3">
            {chartData.length === 0 ? <EmptyState title="No evaluations yet" description="Run the agent on a drive to evaluate eligibility." /> : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e2d9" vertical={false} />
                  <XAxis dataKey="name" tick={{ fontSize: 12, fill: "#5d667a" }} axisLine={false} tickLine={false} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 12, fill: "#5d667a" }} axisLine={false} tickLine={false} width={32} />
                  <Tooltip cursor={{ fill: "rgba(15,27,61,0.04)" }} contentStyle={{ fontSize: 12, borderRadius: 8, borderColor: "#e5e2d9" }} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="Included" stackId="a" fill="#17734a" />
                  <Bar dataKey="Held" stackId="a" fill="#6d3fb5" />
                  <Bar dataKey="Excluded" stackId="a" fill="#c9c4b5" radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </Card>
        <Card>
          <CardHeader title="Recent agent runs" actions={<ButtonLink href="/agent" size="sm">All runs</ButtonLink>} />
          {!d ? <LoadingRows rows={4} /> : d.recent_runs.length === 0 ? <EmptyState title="No agent runs yet" /> : (
            <ul className="divide-y divide-line">
              {d.recent_runs.map((r) => (
                <li key={r.id} className="px-4 py-2.5">
                  <div className="flex items-center justify-between gap-2">
                    <Link href={`/agent/${r.id}`} className="truncate text-[13px] font-medium text-ink hover:underline">#{r.id} {r.drive_title ?? r.objective}</Link>
                    <StatusBadge status={r.status} />
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-[11.5px] text-muted"><PlannerBadge mode={r.planner_mode} /> {relTime(r.created_at)}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}
