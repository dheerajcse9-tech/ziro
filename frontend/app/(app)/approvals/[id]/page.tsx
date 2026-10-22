"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, ShieldCheck, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { ReviewPanel } from "@/components/approvals/review-panel";
import { HashValue, RollList } from "@/components/ui/evidence";
import { DefinitionList, KpiCard, PageHeader } from "@/components/ui/layout";
import { ConfirmDialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Textarea } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { MockBadge, StatusBadge, Tag } from "@/components/ui/status";
import { api, ApiError } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import type { AgentRun, Approval } from "@/lib/types";

export default function ApprovalDetail() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["approval", id], queryFn: () => api.get<Approval>(`/api/approvals/${id}`) });
  const [mode, setMode] = useState<"approve" | "reject" | null>(null);
  const [note, setNote] = useState("");
  const [ack, setAck] = useState(false);

  const approve = useMutation({
    mutationFn: () => api.post<{ approval: Approval; run: AgentRun | null }>(`/api/approvals/${id}/approve`, { proposal_hash: q.data!.proposal_hash, note: note || null }),
    onSuccess: (r) => {
      setMode(null); qc.invalidateQueries();
      if (r.run) {
        toast.success(r.run.status === "VERIFIED" ? "Approved — the list was sent and checked" : `Approved — ${r.run.status.replace(/_/g, " ").toLowerCase()}`);
        router.push(`/agent/${r.run.id}`);
      } else toast.success("Approved");
    },
    onError: (e) => { setMode(null); toastError(e); if (e instanceof ApiError && e.status === 412) qc.invalidateQueries(); },
  });
  const reject = useMutation({
    mutationFn: () => api.post<Approval>(`/api/approvals/${id}/reject`, { reason: note || null }),
    onSuccess: () => { setMode(null); toast("Proposal rejected; tentative reservations released"); qc.invalidateQueries(); },
    onError: toastError,
  });
  const execute = useMutation({
    mutationFn: () => api.post<{ id: number }>(`/api/approvals/${id}/execute`),
    onSuccess: (u) => { qc.invalidateQueries(); router.push(`/verification/${u.id}`); },
    onError: toastError,
  });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data || !q.data.proposal) return <LoadingRows />;
  const a = q.data;
  const p = a.proposal!;
  const canApprove = can(me.data, "approve") && a.status === "PENDING";

  return (
    <>
      <PageHeader title={a.drive_title} crumbs={[{ label: "Approvals", href: "/approvals" }, { label: a.drive_title }]}
        meta={<><StatusBadge status={a.status} label={a.status === "CONSUMED" ? "Sent" : undefined} />{p.kind === "SUPPLEMENT" && <Tag tone="info">Additional students only</Tag>}</>}
        actions={canApprove ? <>
          <Button variant="secondary" onClick={() => { setNote(""); setMode("reject"); }} icon={<XCircle className="size-4" />}>Reject</Button>
          <Button variant="success" disabled={!a.is_current} onClick={() => { setNote(""); setAck(false); setMode("approve"); }} icon={<CheckCircle2 className="size-4" />}>Approve upload</Button>
        </> : a.status === "APPROVED" && can(me.data, "runAgent") ? <Button variant="primary" loading={execute.isPending} onClick={() => execute.mutate()}>Execute approved upload</Button> : undefined} />

      {a.status === "PENDING" && !a.is_current && (
        <div className="mb-4"><Banner tone="bad" title="This proposal is stale">Student details or consent changed after this list was prepared, so it can no longer be approved. Ask the placement officer to run the drive again.</Banner></div>)}
      {a.status === "PENDING" && !can(me.data, "approve") && (
        <div className="mb-4"><Banner tone="info" title="Approver action required">Only the Approver (Head of Placements) can approve or reject this list. Agents never approve.</Banner></div>)}
      {a.status === "INVALIDATED" && <div className="mb-4"><Banner title="Invalidated">{a.invalidated_reason}</Banner></div>}

      {p.kind === "SUPPLEMENT" && (
        <div className="mb-4"><Banner tone="info" title={`Supplementary upload: only ${p.included_roll_numbers.length} new student(s) will be sent`}>
          {p.previously_submitted_roll_numbers.length} student(s) were already sent to this employer earlier and will not be sent again.
          After upload, verification checks the employer&apos;s full list (earlier students + these).</Banner></div>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <KpiCard label={p.kind === "SUPPLEMENT" ? "New students to upload" : "Will be uploaded"} value={p.included_roll_numbers.length} tone="ok"
          hint={p.kind === "SUPPLEMENT" ? `${p.previously_submitted_roll_numbers.length} already sent earlier` : undefined} />
        <KpiCard label="Not included" value={p.excluded.length} />
        <KpiCard label="Held (schedule clash)" value={p.held.length} tone={p.held.length ? "warn" : "neutral"} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title={p.kind === "SUPPLEMENT" ? "Additional students to send" : "Students to send"} description="Exactly these students will be sent if you approve." />
            <div className="p-4"><RollList rolls={p.included_roll_numbers} tone="ok" empty="Empty roster" /></div>
          </Card>
          {p.previously_submitted_roll_numbers.length > 0 && (
            <Card>
              <CardHeader title="Already sent earlier (not re-sent)" description="Part of the bound proposal: if this list changes, the approval is invalidated." />
              <div className="p-4"><RollList rolls={p.previously_submitted_roll_numbers} /></div>
            </Card>
          )}
          {p.held.length > 0 && (
            <Card>
              <CardHeader title="Held — schedule clashes" description="Eligible and consented, but reserved on an overlapping drive. Not part of this upload." actions={<Link href="/conflicts" className="text-[13px] text-accent hover:underline">Resolve →</Link>} />
              <table className="data-table w-full"><thead><tr><th>Roll</th><th>Name</th><th>Reason</th></tr></thead>
                <tbody>{p.held.map((h) => <tr key={h.roll_number}><td className="font-mono text-[12px]">{h.roll_number}</td><td>{h.full_name}</td><td className="text-[12px] text-muted">{h.reasons.join("; ")}</td></tr>)}</tbody></table>
            </Card>
          )}
          <Card>
            <CardHeader title="Not included, and why" />
            {p.excluded.length === 0 ? <EmptyState title="No exclusions" /> : (
              <table className="data-table w-full"><thead><tr><th>Roll</th><th>Name</th><th>Decision</th><th>Reasons</th></tr></thead>
                <tbody>{p.excluded.map((x) => <tr key={x.roll_number}><td className="font-mono text-[12px]">{x.roll_number}</td><td>{x.full_name}</td><td><StatusBadge status={x.decision} /></td><td className="text-[12px] text-muted">{x.reasons.join("; ")}</td></tr>)}</tbody></table>
            )}
          </Card>
        </div>
        <div className="space-y-4">
          <ReviewPanel approvalId={a.id} driveId={p.drive_id} pending={a.status === "PENDING"} />
          <Card>
            <CardHeader title="What you are approving" />
            <div className="p-4"><DefinitionList cols={1} items={[
              { label: "Drive", value: <Link className="text-accent hover:underline" href={`/drives/${p.drive_id}`}>{p.drive_title}</Link> },
              { label: "Sent to", value: <span>{p.destination.name}{p.destination.is_local_mock && <span className="ml-1"><MockBadge /></span>}</span> },
              { label: "List unchanged since it was prepared", value: a.is_current ? <span className="inline-flex items-center gap-1 text-ok"><ShieldCheck className="size-3.5" />Yes</span> : <span className="text-bad">No — it changed, ask for a fresh list</span> },
              { label: "Prepared", value: fmtDateTime(a.created_at) },
              ...(a.decided_by ? [{ label: "Decided by", value: `${a.decided_by.name}` }, { label: "Decided at", value: fmtDateTime(a.decided_at) }] : []),
              ...(a.decision_note ? [{ label: "Note", value: a.decision_note }] : []),
            ]} />
            <details className="mt-4 text-[12.5px]">
              <summary className="cursor-pointer text-muted">Technical details</summary>
              <div className="mt-3"><DefinitionList cols={1} items={[
                { label: "Proposal hash", value: <HashValue value={p.proposal_hash} /> },
                { label: "Destination fingerprint", value: <HashValue value={p.destination_fingerprint} /> },
                { label: "Form", value: <a className="inline-flex items-center gap-1 font-mono text-[11.5px] text-accent hover:underline" target="_blank" rel="noreferrer" href={`/mock-portal/forms/${p.destination.form_ref}`}>{p.destination.form_ref}<ExternalLink className="size-3" /></a> },
                { label: "Criteria / schedule version", value: `v${p.criteria_version} / v${p.schedule_version}` },
                { label: "Requested by", value: <span className="font-mono text-[12px]">{a.requested_by}</span> },
                ...(p.run_id ? [{ label: "Agent run", value: <Link className="text-accent hover:underline" href={`/agent/${p.run_id}`}>#{p.run_id}</Link> }] : []),
              ]} /></div>
            </details>
            </div>
          </Card>
          {p.unresolved_issues.length > 0 && (
            <Card>
              <CardHeader title="Unresolved issues" description="Visible to the approver; they do not block this upload." />
              <ul className="divide-y divide-line">{p.unresolved_issues.map((u, i) => <li key={i} className="px-4 py-2 text-[12.5px]"><Tag tone="warn">{u.type.replace(/_/g, " ").toLowerCase()}</Tag> <span className="font-mono">{u.roll_number}</span><p className="text-muted">{u.detail}</p></li>)}</ul>
            </Card>
          )}
        </div>
      </div>

      <ConfirmDialog open={mode === "approve"} onOpenChange={(o) => !o && setMode(null)} tone="success" loading={approve.isPending}
        title={`Approve ${p.kind === "SUPPLEMENT" ? "supplementary upload" : "upload"} of ${p.included_roll_numbers.length} student(s)?`} confirmLabel="Approve and send"
        description="After you approve, the agents send this list, check that it arrived correctly, email it to the company from the placement mailbox, tell each listed student they are shortlisted, and tell the others why they were not."
        onConfirm={() => ack ? approve.mutate() : toast.error("Confirm that you reviewed the roster")}>
        <div className="space-y-3">
          <div className="rounded-md bg-subtle p-3 text-[12.5px]"><p>Destination: <strong>{p.destination.name}</strong></p></div>
          <label className="flex items-start gap-2 text-[13px]"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />I reviewed the roster, exclusions and destination for this exact proposal.</label>
          <Field label="Note (optional)" htmlFor="appr_note"><Textarea id="appr_note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        </div>
      </ConfirmDialog>
      <ConfirmDialog open={mode === "reject"} onOpenChange={(o) => !o && setMode(null)} tone="danger" loading={reject.isPending}
        title="Reject this proposal?" confirmLabel="Reject" description="Nothing is uploaded. Tentative drive reservations are released." onConfirm={() => reject.mutate()}>
        <Field label="Reason" htmlFor="rej_note"><Textarea id="rej_note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </ConfirmDialog>
    </>
  );
}
