"use client";

import { DriveAgentFlows } from "@/components/agents/agent-flow";
import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CalendarClock, FlaskConical, ListChecks } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { DriveProgress } from "@/components/drives/drive-progress";
import { InvitationsPanel, ResultsPanel } from "@/components/drives/outreach-panels";
import { BRANCHES, CriteriaFields, criteriaSchema, type CriteriaValues, toCriteriaPayload } from "@/components/drives/criteria-fields";
import { DataTable } from "@/components/ui/data-table";
import { JsonView } from "@/components/ui/evidence";
import { DefinitionList, KpiCard, PageHeader } from "@/components/ui/layout";
import { Dialog, TabPanel, Tabs } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn, fmtDateTime, fmtSlot } from "@/lib/format";
import type { Criteria, DriveDetail, EligibilityResponse, StudentOutcome } from "@/lib/types";

function ruleCell(o: StudentOutcome, rule: string) {
  const r = o.rule_results.find((x) => x.rule === rule);
  if (!r || r.outcome === "NOT_APPLICABLE") return <span className="text-faint">n/a</span>;
  const cls = r.outcome === "PASS" ? "text-ok" : r.outcome === "FAIL" ? "text-bad font-medium" : "text-warn font-medium";
  return <span className={cn("tabular", cls)} title={r.reason}>{r.observed ?? "missing"}</span>;
}

// One plain result per student instead of separate decision / roster columns.
function resultOf(o: StudentOutcome): { label: string; tone: "ok" | "info" | "warn" | "neutral" | "hold"; rank: number } {
  if (o.roster_status === "INCLUDED") return { label: "On the list", tone: "ok", rank: 0 };
  if (o.roster_status === "HELD") return { label: "Held: schedule clash", tone: "hold", rank: 1 };
  if (o.decision === "ELIGIBLE") return { label: "Qualifies, no YES yet", tone: "info", rank: 2 };
  if (o.decision === "UNKNOWN") return { label: "Missing details", tone: "warn", rank: 3 };
  return { label: "Does not qualify", tone: "neutral", rank: 4 };
}

const eligibilityColumns: ColumnDef<StudentOutcome>[] = [
  { accessorKey: "roll_number", header: "Roll", cell: ({ row }) => <Link href={`/students/${row.original.student_id}`} className="font-mono text-[12px] text-accent hover:underline">{row.original.roll_number}</Link> },
  { accessorKey: "full_name", header: "Name" },
  { id: "cgpa", header: "CGPA", accessorFn: (o) => o.cgpa ?? -1, cell: ({ row }) => ruleCell(row.original, "CGPA") },
  { id: "backlogs", header: "Backlogs", accessorFn: (o) => o.active_backlogs ?? -1, cell: ({ row }) => ruleCell(row.original, "BACKLOGS") },
  { id: "branch", header: "Branch", accessorFn: (o) => o.branch ?? "", cell: ({ row }) => ruleCell(row.original, "BRANCH") },
  { id: "result", header: "Result", accessorFn: (o) => resultOf(o).rank, cell: ({ row }) => {
    const r = resultOf(row.original);
    return <span className="flex flex-wrap items-center gap-1"><StatusBadge status={r.label} label={r.label} tone={r.tone} />{row.original.already_submitted && <Tag tone="info">Sent</Tag>}</span>;
  } },
  { id: "reasons", header: "Why", enableSorting: false, cell: ({ row }) => <span className="text-[12px] text-muted">{row.original.reasons.join("; ") || "Meets every rule and said YES"}</span> },
];

export default function DriveWorkspace() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const qc = useQueryClient();
  const [tab, setTab] = useState("overview");
  const [rosterFilter, setRosterFilter] = useState("ALL");
  const drive = useQuery({ queryKey: ["drive", id], queryFn: () => api.get<DriveDetail>(`/api/drives/${id}`) });
  const elig = useQuery({ queryKey: ["eligibility", id], queryFn: () => api.get<EligibilityResponse>(`/api/drives/${id}/eligibility`), enabled: tab === "eligibility" || tab === "overview" });
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

  const requestData = useMutation({
    mutationFn: () => api.post<{ requested: { roll_number: string }[] }>(`/api/drives/${id}/missing-data-requests`),
    onSuccess: (r) => { toast.success(`Missing-data requests emailed to ${r.requested.length} student(s)`, { description: "Their answers appear in the Exception Inbox for you to verify." }); qc.invalidateQueries(); },
    onError: toastError,
  });
  const filteredStudents = useMemo(() => (elig.data?.students ?? []).filter((s) => rosterFilter === "ALL" || resultOf(s).label === rosterFilter)
    .sort((a, b) => resultOf(a).rank - resultOf(b).rank), [elig.data, rosterFilter]);

  if (drive.isError) return <ErrorState error={drive.error} onRetry={() => drive.refetch()} />;
  if (!drive.data) return <LoadingRows />;
  const d = drive.data;
  const c = d.criteria;
  const editable = can(me.data, "manageDrives");
  const counts = elig.data?.counts;

  function openCriteria() {
    critForm.reset({
      min_cgpa: c?.min_cgpa?.toString() ?? "", backlog_policy: c?.backlog_policy ?? "NO_ACTIVE_BACKLOGS",
      max_active_backlogs: c?.max_active_backlogs?.toString() ?? "", allowed_branches: c?.allowed_branches ?? [],
      source_text: c?.source_text ?? "", change_note: "",
      required_consent_scope: (c?.required_consent_scope as "SHARE_WITH_EMPLOYERS" | "DRIVE_REGISTRATION") ?? "SHARE_WITH_EMPLOYERS",
    });
    setCritOpen(true);
  }
  function openSchedule() {
    setSched({ date: d.starts_at_local.slice(0, 10), start: d.starts_at_local.slice(11, 16), end: d.ends_at_local.slice(11, 16), status: d.status });
    setSchedOpen(true);
  }

  return (
    <>
      <PageHeader title={d.title} crumbs={[{ label: "Drives", href: "/drives" }, { label: d.employer.name }]}
        description={<>{fmtSlot(d.starts_at, d.ends_at, d.timezone)}{d.location ? ` · ${d.location}` : ""}</>}
        meta={<StatusBadge status={d.status} />}
        actions={<>
          {editable && <Button onClick={openSchedule} icon={<CalendarClock className="size-4" />}>Change date / time</Button>}
        </>} />

      <DriveProgress drive={d} />

      <Tabs value={tab} onValueChange={setTab} tabs={[
        { value: "overview", label: "Overview" }, { value: "eligibility", label: "Who qualifies" },
        { value: "invitations", label: "Consent form" }, { value: "criteria", label: "Criteria" },
        ...(d.uploads.length > 0 ? [{ value: "results", label: "Results" }] : []),
      ]}>
        <TabPanel value="overview">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <KpiCard label="On the list" value={counts?.included ?? "—"} tone="ok" hint={counts?.already_submitted ? `${counts.already_submitted} already sent to the company` : "Qualify and said YES"} />
            <KpiCard label="Qualify, no YES yet" value={counts?.consent_missing ?? "—"} tone={counts?.consent_missing ? "accent" : "neutral"} />
            <KpiCard label="Missing details" value={counts?.unknown ?? "—"} tone={counts?.unknown ? "warn" : "neutral"} />
            <KpiCard label="Do not qualify" value={counts?.ineligible ?? "—"} hint={counts?.held ? `${counts.held} held for a schedule clash` : undefined} />
          </div>
          <div className="mt-4">
            <Card>
              <CardHeader title="Drive details" />
              <div className="p-4">
                <DefinitionList items={[
                  { label: "Employer", value: d.employer.name },
                  { label: "Role", value: d.role_title ?? "—" },
                  { label: "Criteria", value: c ? `CGPA ≥ ${c.min_cgpa ?? "—"} · ${c.backlog_policy.replace(/_/g, " ").toLowerCase()}${c.max_active_backlogs ? ` (${c.max_active_backlogs})` : ""} · ${c.allowed_branches.join(", ") || "all branches"}` : <Tag tone="bad">Not configured</Tag> },
                  { label: "Students register by", value: d.registration_deadline_local ? d.registration_deadline_local.replace("T", " ") : "—" },
                  { label: "Company wants the list by", value: d.roster_due_at_local ? d.roster_due_at_local.replace("T", " ") : "—" },
                  ...(d.source_email ? [{ label: "Created from", value: <Link className="text-accent hover:underline" href="/inbox">{d.source_email.from_address}: {d.source_email.subject}</Link> }] : []),
                ]} />
              </div>
            </Card>
          </div>
          <DriveAgentFlows runs={d.runs} />
        </TabPanel>

        <TabPanel value="eligibility">
          <Card>
            <CardHeader title="Who qualifies"
              description={elig.data ? `Company's criteria applied to all ${elig.data.counts.total} students. Hover a value to see the rule.` : undefined}
              actions={<>{editable && (counts?.unknown ?? 0) > 0 && <Button size="sm" loading={requestData.isPending} onClick={() => requestData.mutate()}>Ask {counts?.unknown} student(s) for missing details</Button>}
              <Select aria-label="Filter" value={rosterFilter} onChange={(e) => setRosterFilter(e.target.value)}>
                <option value="ALL">All students</option>
                {["On the list", "Qualifies, no YES yet", "Missing details", "Held: schedule clash", "Does not qualify"].map((l) => <option key={l} value={l}>{l}</option>)}
              </Select></>} />
            {elig.isError ? <div className="p-4"><ErrorState error={elig.error} /></div> : !elig.data ? <LoadingRows /> :
              <DataTable data={filteredStudents} columns={eligibilityColumns} pageSize={50} dense
                rowClassName={(o) => o.roster_status === "HELD" ? "bg-hold-soft/40" : undefined} />}
          </Card>
        </TabPanel>

        <TabPanel value="criteria">
          <Card>
            <CardHeader title="Eligibility criteria" description="Changing the criteria cancels any list waiting for approval; the old criteria stay in the history below."
              actions={editable ? <Button variant="primary" size="sm" onClick={openCriteria} icon={<ListChecks className="size-4" />}>Change criteria</Button> : undefined} />
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
            {c?.source_text && <details className="border-t border-line p-4"><summary className="cursor-pointer text-[12px] font-semibold text-muted">Company's original text</summary><pre className="mt-2 whitespace-pre-wrap rounded-md bg-subtle p-3 text-[12.5px] text-ink-2">{c.source_text}</pre></details>}
          </Card>
          <div className="mt-4">
            <p className="mb-2 text-[13px] font-semibold text-ink">What-if: try different criteria (nothing is saved)</p>
            <WhatIf driveId={d.id} criteria={c} startLocal={d.starts_at_local} endLocal={d.ends_at_local} />
          </div>
        </TabPanel>

        <TabPanel value="invitations"><InvitationsPanel drive={d} /></TabPanel>
        <TabPanel value="results"><ResultsPanel drive={d} /></TabPanel>
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
