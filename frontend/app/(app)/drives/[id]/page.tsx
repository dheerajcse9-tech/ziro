"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Bot, CalendarClock, ExternalLink, FlaskConical, ListChecks } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { BRANCHES, CriteriaFields, criteriaSchema, type CriteriaValues, toCriteriaPayload } from "@/components/drives/criteria-fields";
import { DataTable } from "@/components/ui/data-table";
import { HashValue, JsonView } from "@/components/ui/evidence";
import { DefinitionList, KpiCard, PageHeader } from "@/components/ui/layout";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { MockBadge, PlannerBadge, StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn, fmtDateTime, fmtSlot, fmtTime, relTime } from "@/lib/format";
import type { Criteria, DriveDetail, DrivePair, EligibilityResponse, StudentOutcome } from "@/lib/types";

function ruleCell(o: StudentOutcome, rule: string) {
  const r = o.rule_results.find((x) => x.rule === rule);
  if (!r || r.outcome === "NOT_APPLICABLE") return <span className="text-faint">n/a</span>;
  const cls = r.outcome === "PASS" ? "text-ok" : r.outcome === "FAIL" ? "text-bad font-medium" : "text-warn font-medium";
  return <span className={cn("tabular", cls)} title={r.reason}>{r.observed ?? "missing"}</span>;
}

const eligibilityColumns: ColumnDef<StudentOutcome>[] = [
  { accessorKey: "roll_number", header: "Roll", cell: ({ row }) => <Link href={`/students/${row.original.student_id}`} className="font-mono text-[12px] text-accent hover:underline">{row.original.roll_number}</Link> },
  { accessorKey: "full_name", header: "Name" },
  { id: "cgpa", header: "CGPA", accessorFn: (o) => o.cgpa ?? -1, cell: ({ row }) => ruleCell(row.original, "CGPA") },
  { id: "backlogs", header: "Backlogs", accessorFn: (o) => o.active_backlogs ?? -1, cell: ({ row }) => ruleCell(row.original, "BACKLOGS") },
  { id: "branch", header: "Branch", accessorFn: (o) => o.branch ?? "", cell: ({ row }) => ruleCell(row.original, "BRANCH") },
  { id: "consent", header: "Consent", accessorFn: (o) => o.consent_ok, cell: ({ row }) => row.original.consent_ok ? <span className="text-ok">Granted</span> : <span className="font-medium text-bad">No</span> },
  { accessorKey: "decision", header: "Decision", cell: ({ row }) => <StatusBadge status={row.original.decision} /> },
  { accessorKey: "roster_status", header: "Roster", cell: ({ row }) => <StatusBadge status={row.original.roster_status} /> },
  { id: "reasons", header: "Reasons", enableSorting: false, cell: ({ row }) => <span className="text-[12px] text-muted">{row.original.reasons.join("; ") || "—"}</span> },
];

export default function DriveWorkspace() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const me = useMe();
  const qc = useQueryClient();
  const [tab, setTab] = useState("overview");
  const [rosterFilter, setRosterFilter] = useState("ALL");
  const drive = useQuery({ queryKey: ["drive", id], queryFn: () => api.get<DriveDetail>(`/api/drives/${id}`) });
  const elig = useQuery({ queryKey: ["eligibility", id], queryFn: () => api.get<EligibilityResponse>(`/api/drives/${id}/eligibility`), enabled: tab === "eligibility" || tab === "overview" });
  const conflicts = useQuery({ queryKey: ["drive-conflicts", id], queryFn: () => api.get<{ overlapping_drives: DrivePair[]; students: (StudentOutcome & { conflicts: any[] })[] }>(`/api/drives/${id}/conflicts`), enabled: tab === "conflicts" });
  const versions = useQuery({ queryKey: ["criteria", id], queryFn: () => api.get<Criteria[]>(`/api/drives/${id}/criteria`), enabled: tab === "criteria" });

  const [critOpen, setCritOpen] = useState(false);
  const [schedOpen, setSchedOpen] = useState(false);
  const critForm = useForm<CriteriaValues>({ resolver: zodResolver(criteriaSchema) });
  const addCriteria = useMutation({
    mutationFn: (v: CriteriaValues) => api.post<Criteria>(`/api/drives/${id}/criteria`, toCriteriaPayload(v)),
    onSuccess: (c) => { toast.success(`Criteria v${c.version} saved`, { description: "Open approvals for this drive were re-validated; stale ones are invalidated." }); setCritOpen(false); qc.invalidateQueries(); },
    onError: toastError,
  });
  const [sched, setSched] = useState({ date: "", start: "", end: "", status: "" });
  const updateDrive = useMutation({
    mutationFn: () => api.patch<{ changes: Record<string, unknown>; invalidated_proposals: number[] }>(`/api/drives/${id}`, {
      ...(sched.date && sched.start ? { starts_at: `${sched.date}T${sched.start}:00` } : {}),
      ...(sched.date && sched.end ? { ends_at: `${sched.date}T${sched.end}:00` } : {}),
      ...(sched.status && sched.status !== drive.data?.status ? { status: sched.status } : {}),
    }),
    onSuccess: (r) => {
      toast.success(Object.keys(r.changes).length ? `Updated: ${Object.keys(r.changes).join(", ")}` : "No changes",
        { description: r.invalidated_proposals.length ? `${r.invalidated_proposals.length} proposal(s) invalidated (clashes re-evaluated).` : undefined });
      setSchedOpen(false); qc.invalidateQueries();
    },
    onError: toastError,
  });

  const filteredStudents = useMemo(() => (elig.data?.students ?? []).filter((s) => rosterFilter === "ALL" || s.roster_status === rosterFilter || s.decision === rosterFilter), [elig.data, rosterFilter]);

  if (drive.isError) return <ErrorState error={drive.error} onRetry={() => drive.refetch()} />;
  if (!drive.data) return <LoadingRows />;
  const d = drive.data;
  const c = d.criteria;
  const editable = can(me.data, "editData");
  const counts = elig.data?.counts;

  function openCriteria() {
    critForm.reset({
      min_cgpa: c?.min_cgpa?.toString() ?? "", backlog_policy: c?.backlog_policy ?? "NO_ACTIVE_BACKLOGS",
      max_active_backlogs: c?.max_active_backlogs?.toString() ?? "", allowed_branches: c?.allowed_branches ?? [],
      source_text: c?.source_text ?? "", change_note: "",
    });
    setCritOpen(true);
  }
  function openSchedule() {
    setSched({ date: d.starts_at_local.slice(0, 10), start: d.starts_at_local.slice(11, 16), end: d.ends_at_local.slice(11, 16), status: d.status });
    setSchedOpen(true);
  }

  return (
    <>
      <PageHeader title={d.title} crumbs={[{ label: "Employer Drives", href: "/drives" }, { label: d.employer.name }]}
        description={<>{fmtSlot(d.starts_at, d.ends_at, d.timezone)}{d.location ? ` · ${d.location}` : ""}</>}
        meta={<><StatusBadge status={d.status} />{c && <Tag tone="accent">Criteria v{c.version}</Tag>}<Tag>Schedule v{d.schedule_version}</Tag>{d.destination?.is_local_mock && <MockBadge />}</>}
        actions={<>
          {editable && <Button onClick={openSchedule} icon={<CalendarClock className="size-4" />}>Edit schedule / status</Button>}
          {can(me.data, "runAgent") && <Button variant="primary" icon={<Bot className="size-4" />}
            onClick={() => router.push(`/agent?objective=${encodeURIComponent(`Prepare the eligible roster for drive #${d.id} (${d.title}), identify unresolved records, and show me the proposal for approval.`)}`)}>Prepare roster with agent</Button>}
        </>} />

      <Tabs value={tab} onValueChange={setTab} tabs={[
        { value: "overview", label: "Overview" }, { value: "eligibility", label: "Eligibility", count: counts?.total },
        { value: "conflicts", label: "Conflicts" }, { value: "criteria", label: "Criteria versions" }, { value: "whatif", label: "What-if simulator" },
      ]}>
        <TabPanel value="overview">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiCard label="Would be included now" value={counts?.included ?? "—"} tone="ok" hint="Live evaluation, not persisted" />
            <KpiCard label="Held (schedule clash)" value={counts?.held ?? "—"} tone={counts?.held ? "warn" : "neutral"} />
            <KpiCard label="Excluded" value={counts?.excluded ?? "—"} hint={counts ? `${counts.consent_missing} eligible but no consent` : undefined} />
            <KpiCard label="Unknown (missing data)" value={counts?.unknown ?? "—"} tone={counts?.unknown ? "warn" : "neutral"} />
          </div>
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Configuration" />
              <div className="p-4">
                <DefinitionList items={[
                  { label: "Employer", value: d.employer.name },
                  { label: "Role", value: d.role_title ?? "—" },
                  { label: "Starts (UTC)", value: <span className="font-mono text-[12px]">{d.starts_at.replace("+00:00", "Z")}</span> },
                  { label: "Timezone", value: d.timezone },
                  { label: "Criteria", value: c ? `CGPA ≥ ${c.min_cgpa ?? "—"} · ${c.backlog_policy.replace(/_/g, " ").toLowerCase()}${c.max_active_backlogs ? ` (${c.max_active_backlogs})` : ""} · ${c.allowed_branches.join(", ") || "all branches"}` : <Tag tone="bad">Not configured</Tag> },
                  { label: "Consent required", value: c?.required_consent_scope.replace(/_/g, " ").toLowerCase() ?? "—" },
                  { label: "Destination", value: d.destination ? <span className="flex flex-wrap items-center gap-1.5">{d.destination.name}
                    <a className="inline-flex items-center gap-0.5 text-accent hover:underline" target="_blank" rel="noreferrer" href={`/mock-portal/forms/${d.destination.form_ref}`}>open form <ExternalLink className="size-3" /></a></span> : <Tag tone="bad">None</Tag> },
                  { label: "Live proposal hash", value: <HashValue value={elig.data?.live_proposal_hash} /> },
                ]} />
              </div>
            </Card>
            <Card>
              <CardHeader title="Activity on this drive" />
              <div className="divide-y divide-line">
                {d.runs.length === 0 && d.approvals.length === 0 && d.uploads.length === 0 && <EmptyState title="No activity yet" />}
                {d.runs.map((r) => (
                  <Link key={`r${r.id}`} href={`/agent/${r.id}`} className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-subtle">
                    <span className="text-[13px]">Agent run #{r.id} <span className="text-muted">· {relTime(r.created_at)}</span></span>
                    <span className="flex items-center gap-2"><PlannerBadge mode={r.planner_mode} /><StatusBadge status={r.status} /></span>
                  </Link>))}
                {d.approvals.map((a) => (
                  <Link key={`a${a.id}`} href={`/approvals/${a.id}`} className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-subtle">
                    <span className="text-[13px]">Approval #{a.id} · {a.included_count} students</span><StatusBadge status={a.status} />
                  </Link>))}
                {d.uploads.map((u) => (
                  <Link key={`u${u.id}`} href={`/verification/${u.id}`} className="flex items-center justify-between gap-2 px-4 py-2 hover:bg-subtle">
                    <span className="text-[13px]">Upload #{u.id} · {u.submitted_count} IDs</span>
                    <span className="flex gap-1.5"><StatusBadge status={u.status} />{u.latest_verification && <StatusBadge status={u.latest_verification.status} />}</span>
                  </Link>))}
              </div>
            </Card>
          </div>
        </TabPanel>

        <TabPanel value="eligibility">
          <Card>
            <CardHeader title="Eligibility — live evaluation"
              description={elig.data ? `Criteria v${elig.data.criteria.version} applied to ${elig.data.counts.total} students at ${fmtDateTime(elig.data.evaluated_at)}. Hover a value for the rule explanation.` : undefined}
              actions={<Select aria-label="Filter" value={rosterFilter} onChange={(e) => setRosterFilter(e.target.value)}>
                <option value="ALL">All students</option><option value="INCLUDED">Included</option><option value="HELD">Held</option>
                <option value="EXCLUDED">Excluded</option><option value="UNKNOWN">Unknown decisions</option><option value="INELIGIBLE">Ineligible</option>
              </Select>} />
            {elig.data && Object.keys(elig.data.exclusion_reasons).length > 0 && (
              <div className="flex flex-wrap gap-1.5 border-b border-line px-4 py-2">
                {Object.entries(elig.data.exclusion_reasons).map(([r, n]) => <Tag key={r}>{r}: {n}</Tag>)}
              </div>
            )}
            {elig.isError ? <div className="p-4"><ErrorState error={elig.error} /></div> : !elig.data ? <LoadingRows /> :
              <DataTable data={filteredStudents} columns={eligibilityColumns} pageSize={50} dense
                rowClassName={(o) => o.roster_status === "HELD" ? "bg-hold-soft/40" : undefined} />}
          </Card>
        </TabPanel>

        <TabPanel value="conflicts">
          {!conflicts.data ? <LoadingRows /> : (
            <div className="space-y-4">
              <Card>
                <CardHeader title="Overlapping drives" description="Half-open intervals compared in UTC; touching slots do not clash." />
                {conflicts.data.overlapping_drives.length === 0 ? <EmptyState title="No overlapping drives" /> : (
                  <table className="data-table w-full"><thead><tr><th>Other drive</th><th>Its slot</th><th>Overlap</th><th>Students on both rosters</th></tr></thead>
                    <tbody>{conflicts.data.overlapping_drives.map((p, i) => {
                      const other = p.drive_a.id === d.id ? p.drive_b : p.drive_a;
                      return <tr key={i}><td><Link className="hover:underline" href={`/drives/${other.id}`}>{other.title}</Link></td>
                        <td>{fmtSlot(other.starts_at, other.ends_at, other.timezone)}</td>
                        <td>{p.overlap_start ? `${fmtTime(p.overlap_start, d.timezone)}–${fmtTime(p.overlap_end, d.timezone)}` : "buffer only"}</td>
                        <td className="tabular">{p.students_assigned_to_both.length}</td></tr>;
                    })}</tbody></table>
                )}
              </Card>
              <Card>
                <CardHeader title="Students with clashing assignments" description="Eligible students reserved on an overlapping drive are HELD out of this roster until an officer resolves the clash in the Exception Inbox."
                  actions={<Link href="/conflicts" className="text-[13px] text-accent hover:underline">Resolve clashes →</Link>} />
                {conflicts.data.students.length === 0 ? <EmptyState title="No student clashes" /> : (
                  <table className="data-table w-full"><thead><tr><th>Roll</th><th>Name</th><th>Status here</th><th>Clashes with</th><th>Resolution</th></tr></thead>
                    <tbody>{conflicts.data.students.map((s) => (
                      <tr key={s.roll_number}><td className="font-mono text-[12px]">{s.roll_number}</td><td>{s.full_name}</td><td><StatusBadge status={s.roster_status} /></td>
                        <td className="text-[12px]">{s.conflicts.map((c: any) => `${c.other_drive_title} (${c.other_assignment_status.toLowerCase()})`).join(", ")}</td>
                        <td className="text-[12px]">{s.conflicts.some((c: any) => c.resolution) ? <Tag tone="ok">Resolved</Tag> : <Tag tone="warn">Open</Tag>}</td></tr>))}
                    </tbody></table>
                )}
              </Card>
            </div>
          )}
        </TabPanel>

        <TabPanel value="criteria">
          <Card>
            <CardHeader title="Criteria versions" description="Criteria are immutable once created. A new version re-validates every open approval on this drive."
              actions={editable ? <Button variant="primary" size="sm" onClick={openCriteria} icon={<ListChecks className="size-4" />}>New version</Button> : undefined} />
            {!versions.data ? <LoadingRows rows={3} /> : (
              <table className="data-table w-full"><thead><tr><th>Version</th><th>CGPA</th><th>Backlogs</th><th>Branches</th><th>Change note</th><th>Created</th></tr></thead>
                <tbody>{versions.data.map((v) => (
                  <tr key={v.id}><td><Tag tone={v.version === c?.version ? "accent" : "neutral"}>v{v.version}{v.version === c?.version ? " · current" : ""}</Tag></td>
                    <td>{v.min_cgpa !== null ? `≥ ${v.min_cgpa}` : "—"}</td>
                    <td>{v.backlog_policy === "NO_ACTIVE_BACKLOGS" ? "None allowed" : v.backlog_policy === "MAX_ALLOWED" ? `≤ ${v.max_active_backlogs}` : "Not considered"}</td>
                    <td>{v.allowed_branches.join(", ") || "All"}</td><td className="text-[12px] text-muted">{v.change_note ?? "—"}</td>
                    <td className="whitespace-nowrap text-[12px]">{fmtDateTime(v.created_at)}</td></tr>))}
                </tbody></table>
            )}
            {c?.source_text && <div className="border-t border-line p-4"><p className="mb-1 text-[12px] font-semibold text-muted">Recorded employer source text (current version)</p><pre className="whitespace-pre-wrap rounded-md bg-subtle p-3 text-[12.5px] text-ink-2">{c.source_text}</pre></div>}
          </Card>
        </TabPanel>

        <TabPanel value="whatif"><WhatIf driveId={d.id} criteria={c} startLocal={d.starts_at_local} endLocal={d.ends_at_local} /></TabPanel>
      </Tabs>

      <Dialog open={critOpen} onOpenChange={setCritOpen} wide title={`New criteria version (v${(c?.version ?? 0) + 1})`}
        description="Saving creates an immutable version. Pending or approved rosters built on the old version are invalidated if their roster changes."
        footer={<><Button onClick={() => setCritOpen(false)}>Cancel</Button><Button variant="primary" loading={addCriteria.isPending} onClick={critForm.handleSubmit((v) => addCriteria.mutate(v))}>Save version</Button></>}>
        <CriteriaFields form={critForm} showNote />
      </Dialog>

      <Dialog open={schedOpen} onOpenChange={setSchedOpen} title="Edit schedule & status"
        description={`Local time in ${d.timezone}. A schedule change bumps the schedule version and re-checks clashes across all drives.`}
        footer={<><Button onClick={() => setSchedOpen(false)}>Cancel</Button><Button variant="primary" loading={updateDrive.isPending} disabled={!!sched.start && !!sched.end && sched.end <= sched.start} onClick={() => updateDrive.mutate()}>Save</Button></>}>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Date" htmlFor="s_date"><Input id="s_date" type="date" value={sched.date} onChange={(e) => setSched({ ...sched, date: e.target.value })} /></Field>
          <Field label="Start" htmlFor="s_start"><Input id="s_start" type="time" value={sched.start} onChange={(e) => setSched({ ...sched, start: e.target.value })} /></Field>
          <Field label="End" htmlFor="s_end" error={sched.end && sched.start && sched.end <= sched.start ? "End must be after start" : undefined}><Input id="s_end" type="time" value={sched.end} onChange={(e) => setSched({ ...sched, end: e.target.value })} /></Field>
          <Field label="Status" htmlFor="s_status" className="sm:col-span-3">
            <Select id="s_status" className="w-full" value={sched.status} onChange={(e) => setSched({ ...sched, status: e.target.value })}>
              {["DRAFT", "OPEN", "ROSTER_SUBMITTED", "CLOSED", "CANCELLED"].map((s) => <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>)}
            </Select>
          </Field>
        </div>
      </Dialog>
    </>
  );
}

function WhatIf({ driveId, criteria, startLocal, endLocal }: { driveId: number; criteria: Criteria | null; startLocal: string; endLocal: string }) {
  const [minCgpa, setMinCgpa] = useState(criteria?.min_cgpa?.toString() ?? "");
  const [policy, setPolicy] = useState<string>(criteria?.backlog_policy ?? "NO_ACTIVE_BACKLOGS");
  const [maxB, setMaxB] = useState(criteria?.max_active_backlogs?.toString() ?? "1");
  const [branches, setBranches] = useState<string[]>(criteria?.allowed_branches ?? []);
  const [start, setStart] = useState(startLocal.slice(11, 16));
  const [end, setEnd] = useState(endLocal.slice(11, 16));
  const date = startLocal.slice(0, 10);
  const sim = useMutation({
    mutationFn: () => api.post<any>(`/api/drives/${driveId}/simulate`, {
      min_cgpa: minCgpa === "" ? null : Number(minCgpa), backlog_policy: policy,
      max_active_backlogs: policy === "MAX_ALLOWED" ? Number(maxB) : null, allowed_branches: branches,
      starts_at: `${date}T${start}:00`, ends_at: `${date}T${end}:00`,
    }),
    onError: toastError,
  });
  const r = sim.data;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card className="lg:col-span-1">
        <CardHeader title="Scenario" description="Read-only. Nothing is saved, approved or uploaded." />
        <div className="space-y-3 p-4">
          <Field label="Minimum CGPA" htmlFor="w_cgpa"><Input id="w_cgpa" inputMode="decimal" value={minCgpa} onChange={(e) => setMinCgpa(e.target.value)} /></Field>
          <Field label="Backlog policy" htmlFor="w_pol"><Select id="w_pol" className="w-full" value={policy} onChange={(e) => setPolicy(e.target.value)}>
            <option value="NO_ACTIVE_BACKLOGS">No active backlogs</option><option value="MAX_ALLOWED">Up to N</option><option value="NOT_CONSIDERED">Not considered</option></Select></Field>
          {policy === "MAX_ALLOWED" && <Field label="Max backlogs" htmlFor="w_max"><Input id="w_max" inputMode="numeric" value={maxB} onChange={(e) => setMaxB(e.target.value)} /></Field>}
          <fieldset><legend className="mb-1.5 text-[13px] font-medium text-ink-2">Branches (none = all)</legend>
            <div className="flex flex-wrap gap-1">{BRANCHES.map((b) => {
              const on = branches.includes(b.code);
              return <button type="button" key={b.code} aria-pressed={on} onClick={() => setBranches(on ? branches.filter((x) => x !== b.code) : [...branches, b.code])}
                className={cn("rounded px-2 py-0.5 text-[12px] ring-1 ring-inset", on ? "bg-accent text-white ring-accent" : "ring-line-strong hover:bg-subtle")}>{b.code}</button>;
            })}</div></fieldset>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Start" htmlFor="w_s"><Input id="w_s" type="time" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
            <Field label="End" htmlFor="w_e"><Input id="w_e" type="time" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
          </div>
          <Button variant="primary" className="w-full" loading={sim.isPending} disabled={end <= start} onClick={() => sim.mutate()} icon={<FlaskConical className="size-4" />}>Simulate</Button>
        </div>
      </Card>
      <Card className="lg:col-span-2">
        <CardHeader title="Difference vs current roster" />
        {!r ? <EmptyState title="Run a simulation" description="Adjust the scenario and compare the resulting roster with the current one." /> : (
          <div className="space-y-4 p-4">
            <Banner tone="info" title="Simulation only">Criteria and schedule are unchanged. To apply, create a new criteria version or edit the schedule.</Banner>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <KpiCard label="Included now" value={r.baseline_counts.included} />
              <KpiCard label="Included in scenario" value={r.proposed_counts.included} tone={r.proposed_counts.included > r.baseline_counts.included ? "ok" : r.proposed_counts.included < r.baseline_counts.included ? "bad" : "neutral"} />
              <KpiCard label="Added" value={`+${r.added_to_roster.length}`} tone="ok" />
              <KpiCard label="Removed" value={`−${r.removed_from_roster.length}`} tone={r.removed_from_roster.length ? "bad" : "neutral"} />
            </div>
            {([["Added to roster", r.added_to_roster], ["Removed from roster", r.removed_from_roster], ["Other status changes", r.other_changes]] as [string, any[]][]).map(([label, rows]) => rows.length > 0 && (
              <div key={label}>
                <p className="mb-1 text-[13px] font-semibold">{label}</p>
                <table className="data-table w-full rounded-md border border-line"><thead><tr><th>Roll</th><th>Name</th><th>From → to</th><th>Reasons in scenario</th></tr></thead>
                  <tbody>{rows.map((x: any) => <tr key={x.roll_number}><td className="font-mono text-[12px]">{x.roll_number}</td><td>{x.full_name}</td>
                    <td><StatusBadge status={x.from} /> → <StatusBadge status={x.to} /></td><td className="text-[12px] text-muted">{x.reasons_now.join("; ") || "All rules pass"}</td></tr>)}</tbody></table>
              </div>
            ))}
            <JsonView value={r} label="Full simulation output" />
          </div>
        )}
      </Card>
    </div>
  );
}
