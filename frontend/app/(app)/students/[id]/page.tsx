"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, Pencil, ShieldCheck, ShieldOff } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { DefinitionList, PageHeader } from "@/components/ui/layout";
import { Dialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import type { StudentDetail } from "@/lib/types";

const BRANCHES = ["CSE", "IT", "AIDS", "ECE", "EEE", "ME", "CE", "CHE"];

const editSchema = z.object({
  full_name: z.string().trim().min(2, "Name is required"),
  email: z.string().trim().email("Invalid email").or(z.literal("")),
  branch: z.string(),
  cgpa: z.string().refine((v) => v === "" || (!isNaN(Number(v)) && Number(v) >= 0 && Number(v) <= 10), "CGPA must be 0–10"),
  active_backlogs: z.string().refine((v) => v === "" || (/^\d+$/.test(v)), "Whole number ≥ 0"),
});
type EditValues = z.infer<typeof editSchema>;

export default function StudentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["student", id], queryFn: () => api.get<StudentDetail>(`/api/students/${id}`) });
  const [editing, setEditing] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);
  const [evidence, setEvidence] = useState("");
  const missingFields = q.data ? [
    ...(q.data.cgpa === null ? ["cgpa"] : []), ...(q.data.active_backlogs === null ? ["active_backlogs"] : []),
    ...(q.data.branch === null ? ["branch"] : []),
  ] : [];

  const consent = useMutation({
    mutationFn: (granted: boolean) => api.post<{ invalidated_proposals: number[] }>(`/api/students/${id}/consent`, { granted, evidence_ref: evidence }),
    onSuccess: (r) => {
      toast.success("Consent record appended", { description: r.invalidated_proposals.length ? `${r.invalidated_proposals.length} open proposal(s) invalidated and require re-approval.` : undefined });
      setConsentOpen(false); setEvidence("");
      qc.invalidateQueries();
    },
    onError: toastError,
  });

  const form = useForm<EditValues>({ resolver: zodResolver(editSchema) });
  const save = useMutation({
    mutationFn: (v: EditValues) => api.patch<{ changes: Record<string, unknown>; invalidated_proposals: number[] }>(`/api/students/${id}`, {
      full_name: v.full_name, email: v.email || null, branch: v.branch || null,
      cgpa: v.cgpa === "" ? null : Number(v.cgpa), active_backlogs: v.active_backlogs === "" ? null : Number(v.active_backlogs),
    }),
    onSuccess: (r) => {
      toast.success(Object.keys(r.changes).length ? `Updated ${Object.keys(r.changes).join(", ")}` : "No changes",
        { description: r.invalidated_proposals.length ? `${r.invalidated_proposals.length} open proposal(s) invalidated.` : undefined });
      setEditing(false);
      qc.invalidateQueries();
    },
    onError: toastError,
  });

  const requestData = useMutation({
    mutationFn: () => api.post(`/api/students/${id}/data-requests`, { fields: missingFields }),
    onSuccess: () => { toast.success("Email sent with a personal link to provide the missing details"); qc.invalidateQueries(); },
    onError: toastError,
  });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return <LoadingRows />;
  const s = q.data;
  const selfReported = (field: string) => (s.self_reported ?? []).some((x) => x.field === field)
    ? <Tag tone="info" className="ml-1.5">self-reported</Tag> : null;
  const editable = can(me.data, "editData");

  function openEdit() {
    form.reset({ full_name: s.full_name, email: s.email ?? "", branch: s.branch ?? "", cgpa: s.cgpa?.toString() ?? "", active_backlogs: s.active_backlogs?.toString() ?? "" });
    setEditing(true);
  }

  return (
    <>
      <PageHeader title={<span className="flex items-center gap-2"><span className="font-mono">{s.roll_number}</span><span className="text-muted">·</span>{s.full_name}</span>}
        crumbs={[{ label: "Students", href: "/students" }, { label: s.roll_number }]}
        meta={<>
          {s.consent_granted ? <StatusBadge status="GRANTED" label="Consent granted" /> : <StatusBadge status="REVOKED" label={s.consent ? "Consent revoked" : "Consent not recorded"} />}
          <Tag>Record v{s.record_version}</Tag>
        </>}
        actions={editable ? <>
          <Button onClick={openEdit} icon={<Pencil className="size-4" />}>Edit record</Button>
          {missingFields.length > 0 && <Button loading={requestData.isPending} onClick={() => requestData.mutate()} icon={<Mail className="size-4" />}>Request missing data by email</Button>}
          <Button onClick={() => setConsentOpen(true)} icon={s.consent_granted ? <ShieldOff className="size-4" /> : <ShieldCheck className="size-4" />}>{s.consent_granted ? "Record revocation" : "Record consent"}</Button>
        </> : undefined} />

      {s.data_issues.length > 0 && (
        <div className="mb-4"><Banner title="Data issues on this record">
          <ul className="list-disc pl-4">{s.data_issues.map((i, n) => <li key={n}>{i.message}</li>)}</ul>
        </Banner></div>
      )}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader title="Profile" />
          <div className="p-4">
            <DefinitionList cols={1} items={[
              { label: "Roll number (stable identifier)", value: <span className="font-mono">{s.roll_number}</span> },
              { label: "Branch", value: s.branch ? `${s.branch} — ${s.branch_label}` : <Tag tone="warn">{s.branch_raw ? `Unrecognised: ${s.branch_raw}` : "Missing"}</Tag> },
              { label: "CGPA", value: s.cgpa != null ? <span>{s.cgpa.toFixed(2)}{selfReported("cgpa")}</span> : <Tag tone="warn">Missing</Tag> },
              { label: "Active backlogs", value: s.active_backlogs != null ? <span>{s.active_backlogs}{selfReported("active_backlogs")}</span> : <Tag tone="warn">Missing</Tag> },
              { label: "Graduation year", value: s.graduation_year ?? "—" },
              { label: "Email", value: s.email ?? "—" },
              { label: "Last updated", value: fmtDateTime(s.updated_at) },
            ]} />
          </div>
        </Card>
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardHeader title="Placement history" description="Every drive: eligibility (and why), invitation answer, whether the profile was sent, and the employer's result." />
            {s.placement_history.length === 0 ? <EmptyState title="No drives yet" /> : (
              <table className="data-table w-full">
                <thead><tr><th>Drive</th><th>Eligibility</th><th>Invitation</th><th>Sent to employer</th><th>Result</th></tr></thead>
                <tbody>{s.placement_history.map((h) => (
                  <tr key={h.drive_id}>
                    <td><Link className="font-medium hover:underline" href={`/drives/${h.drive_id}`}>{h.employer}</Link><div className="text-[11.5px] text-muted">{fmtDateTime(h.starts_at, h.timezone)}</div></td>
                    <td>{h.decision ? <><StatusBadge status={h.decision} />{h.criteria_version && <span className="ml-1 text-[11px] text-muted">criteria v{h.criteria_version}</span>}
                      {h.reasons.length > 0 && <div className="mt-0.5 text-[11.5px] text-muted">{h.reasons.join("; ")}</div>}</> : <span className="text-faint">Not evaluated</span>}</td>
                    <td>{h.invitation ? <StatusBadge status={h.invitation} tone={h.invitation === "ACCEPTED" ? "ok" : h.invitation === "SENT" ? "warn" : "neutral"} label={h.invitation === "SENT" ? "Waiting" : undefined} /> : <span className="text-faint">—</span>}</td>
                    <td>{h.sent_to_employer ? <Tag tone="info">Sent</Tag> : <span className="text-faint">No</span>}</td>
                    <td>{h.outcome ? <StatusBadge status={h.outcome} tone={h.outcome === "SELECTED" ? "ok" : "neutral"} /> : <span className="text-faint">—</span>}</td>
                  </tr>))}</tbody>
              </table>
            )}
          </Card>
          <Card>
            <CardHeader title="Eligibility decisions" description="Latest decision per drive, with the criteria version that produced it." />
            {s.decisions.length === 0 ? <EmptyState title="Not evaluated for any drive yet" /> : (
              <table className="data-table w-full">
                <thead><tr><th>Drive</th><th>Criteria</th><th>Decision</th><th>Roster</th><th>Reasons</th></tr></thead>
                <tbody>{s.decisions.map((d) => (
                  <tr key={d.drive_id}>
                    <td><Link className="hover:underline" href={`/drives/${d.drive_id}`}>{d.drive_title}</Link></td>
                    <td>v{d.criteria_version}</td>
                    <td><StatusBadge status={d.decision} /></td>
                    <td><StatusBadge status={d.roster_status} /></td>
                    <td className="text-[12px] text-muted">{d.reasons.join("; ") || "All rules passed"}</td>
                  </tr>))}
                </tbody>
              </table>
            )}
          </Card>
          <Card>
            <CardHeader title="Drive assignments" description="Used for schedule-clash detection." />
            {s.assignments.length === 0 ? <EmptyState title="No assignments" /> : (
              <table className="data-table w-full">
                <thead><tr><th>Drive</th><th>Slot</th><th>Status</th><th>Note</th></tr></thead>
                <tbody>{s.assignments.map((a) => (
                  <tr key={a.drive_id}><td><Link className="hover:underline" href={`/drives/${a.drive_id}`}>{a.drive_title}</Link></td>
                    <td className="whitespace-nowrap text-muted">{fmtDateTime(a.starts_at, "Asia/Kolkata")}</td>
                    <td><StatusBadge status={a.status} /></td><td className="text-[12px] text-muted">{a.note}</td></tr>))}
                </tbody>
              </table>
            )}
          </Card>
          <Card>
            <CardHeader title="Emails & requests" description="Messages sent to this student and their personal links (invitations, data requests)." />
            {s.emails.length === 0 && s.requests.length === 0 ? <EmptyState title="Nothing sent yet" /> : (
              <ul className="divide-y divide-line">
                {s.requests.map((r) => (
                  <li key={`r${r.id}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-[12.5px]">
                    <span>{r.kind === "DRIVE_INVITATION" ? `Invitation · ${r.drive_title}` : `Data request · ${r.requested_fields.join(", ")}`}
                      {r.kind === "DATA_REQUEST" && r.response?.values && <span className="ml-1 text-muted">→ submitted {JSON.stringify(r.response.values)}</span>}</span>
                    <StatusBadge status={r.status} tone={["ACCEPTED", "APPLIED"].includes(r.status) ? "ok" : ["SENT", "SUBMITTED"].includes(r.status) ? "warn" : "neutral"} />
                  </li>))}
                {s.emails.map((m) => (
                  <li key={`e${m.id}`} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-[12.5px]">
                    <span><Link className="hover:underline" href="/communications">{m.subject}</Link> <span className="text-muted">· {fmtDateTime(m.created_at)}</span></span>
                    <StatusBadge status={m.status} tone={m.status === "FAILED" ? "bad" : "ok"} label={m.status === "DELIVERED_LOCAL" ? "Local mailbox" : undefined} />
                  </li>))}
              </ul>
            )}
          </Card>
          <Card>
            <CardHeader title="Consent history" description="Append-only. The most recent record is in effect." />
            {s.consent_history.length === 0 ? <EmptyState title="No consent recorded" description="Without a GRANTED record the student is never shared with employers." /> : (
              <table className="data-table w-full">
                <thead><tr><th>When</th><th>Status</th><th>Source</th><th>Evidence</th></tr></thead>
                <tbody>{s.consent_history.map((c) => (
                  <tr key={c.id}><td className="whitespace-nowrap">{fmtDateTime(c.effective_at)}</td><td><StatusBadge status={c.status} /></td>
                    <td className="font-mono text-[12px]">{c.source}</td><td className="text-[12px] text-muted">{c.evidence_ref ?? "—"}</td></tr>))}
                </tbody>
              </table>
            )}
          </Card>
        </div>
      </div>

      <Dialog open={editing} onOpenChange={setEditing} title="Edit student record"
        description="Changes increment the record version and invalidate any open approval that included this student."
        footer={<><Button onClick={() => setEditing(false)}>Cancel</Button><Button variant="primary" loading={save.isPending} onClick={form.handleSubmit((v) => save.mutate(v))}>Save changes</Button></>}>
        <form className="grid gap-3 sm:grid-cols-2" onSubmit={form.handleSubmit((v) => save.mutate(v))}>
          <Field label="Full name" htmlFor="full_name" error={form.formState.errors.full_name?.message} className="sm:col-span-2"><Input id="full_name" {...form.register("full_name")} /></Field>
          <Field label="Email" htmlFor="email" error={form.formState.errors.email?.message} className="sm:col-span-2"><Input id="email" {...form.register("email")} /></Field>
          <Field label="Branch" htmlFor="branch"><Select id="branch" className="w-full" {...form.register("branch")}><option value="">Missing</option>{BRANCHES.map((b) => <option key={b}>{b}</option>)}</Select></Field>
          <Field label="CGPA" htmlFor="cgpa" error={form.formState.errors.cgpa?.message}><Input id="cgpa" inputMode="decimal" {...form.register("cgpa")} /></Field>
          <Field label="Active backlogs" htmlFor="active_backlogs" error={form.formState.errors.active_backlogs?.message}><Input id="active_backlogs" inputMode="numeric" {...form.register("active_backlogs")} /></Field>
        </form>
      </Dialog>

      <Dialog open={consentOpen} onOpenChange={setConsentOpen} title={s.consent_granted ? "Record consent revocation" : "Record consent to share with employers"}
        description="Appends a consent record with a reference to the signed form, email or ticket. Open approvals affected by the change are invalidated."
        footer={<><Button onClick={() => setConsentOpen(false)}>Cancel</Button>
          <Button variant={s.consent_granted ? "danger" : "success"} disabled={evidence.trim().length < 3} loading={consent.isPending}
            onClick={() => consent.mutate(!s.consent_granted)}>{s.consent_granted ? "Revoke consent" : "Grant consent"}</Button></>}>
        <Field label="Evidence reference" htmlFor="evidence" hint="e.g. 'Signed consent form #2026-114' or 'Email from student, 20 Oct'">
          <Input id="evidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
        </Field>
      </Dialog>
    </>
  );
}
