"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ExternalLink, ShieldCheck, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
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
        toast.success(`Approved. Agent run #${r.run.id} executed the upload: ${r.run.status.replace(/_/g, " ").toLowerCase()}`);
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
      <PageHeader title={`Approval #${a.id} · ${a.drive_title}`} crumbs={[{ label: "Approval Center", href: "/approvals" }, { label: `#${a.id}` }]}
        meta={<><StatusBadge status={a.status} label={a.status === "CONSUMED" ? "Executed" : undefined} />{p.kind === "SUPPLEMENT" && <Tag tone="info">Supplementary upload</Tag>}<Tag tone="accent">criteria v{p.criteria_version}</Tag><Tag>schedule v{p.schedule_version}</Tag>{p.destination.is_local_mock && <MockBadge />}</>}
        actions={canApprove ? <>
          <Button variant="secondary" onClick={() => { setNote(""); setMode("reject"); }} icon={<XCircle className="size-4" />}>Reject</Button>
          <Button variant="success" disabled={!a.is_current} onClick={() => { setNote(""); setAck(false); setMode("approve"); }} icon={<CheckCircle2 className="size-4" />}>Approve upload</Button>
        </> : a.status === "APPROVED" && can(me.data, "runAgent") ? <Button variant="primary" loading={execute.isPending} onClick={() => execute.mutate()}>Execute approved upload</Button> : undefined} />

      {a.status === "PENDING" && !a.is_current && (
        <div className="mb-4"><Banner tone="bad" title="This proposal is stale">The live roster no longer matches the reviewed proposal (live hash <span className="font-mono">{a.live_proposal_hash?.slice(0, 12) ?? a.live_error}</span>). Approving would be refused; ask the officer to re-run the agent.</Banner></div>)}
      {a.status === "PENDING" && !can(me.data, "approve") && (
        <div className="mb-4"><Banner tone="info" title="Approver action required">Only an approver (Head of Placements) or admin can approve. The agent and placement officers cannot approve proposals.</Banner></div>)}
      {a.status === "INVALIDATED" && <div className="mb-4"><Banner title="Invalidated">{a.invalidated_reason}</Banner></div>}

      {p.kind === "SUPPLEMENT" && (
        <div className="mb-4"><Banner tone="info" title={`Supplementary upload: only ${p.included_roll_numbers.length} new student(s) will be sent`}>
          {p.previously_submitted_roll_numbers.length} student(s) were already sent to this employer earlier and will not be sent again.
          After upload, verification checks the employer&apos;s full list (earlier students + these).</Banner></div>
      )}
      <div className="grid gap-3 sm:grid-cols-3">
        <KpiCard label={p.kind === "SUPPLEMENT" ? "New students to upload" : "Will be uploaded"} value={p.included_roll_numbers.length} tone="ok"
          hint={p.kind === "SUPPLEMENT" ? `${p.previously_submitted_roll_numbers.length} already sent earlier` : undefined} />
        <KpiCard label="Excluded" value={p.excluded.length} />
        <KpiCard label="Held for officer resolution" value={p.held.length} tone={p.held.length ? "warn" : "neutral"} />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title={p.kind === "SUPPLEMENT" ? "New students to upload" : "Roster to upload"} description="Exact roll numbers bound by the proposal hash." />
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
            <CardHeader title="Excluded students and reasons" />
            {p.excluded.length === 0 ? <EmptyState title="No exclusions" /> : (
              <table className="data-table w-full"><thead><tr><th>Roll</th><th>Name</th><th>Decision</th><th>Reasons</th></tr></thead>
                <tbody>{p.excluded.map((x) => <tr key={x.roll_number}><td className="font-mono text-[12px]">{x.roll_number}</td><td>{x.full_name}</td><td><StatusBadge status={x.decision} /></td><td className="text-[12px] text-muted">{x.reasons.join("; ")}</td></tr>)}</tbody></table>
            )}
          </Card>
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader title="What you are approving" />
            <div className="p-4"><DefinitionList cols={1} items={[
              { label: "Drive", value: <Link className="text-accent hover:underline" href={`/drives/${p.drive_id}`}>{p.drive_title}</Link> },
              { label: "Destination", value: <span>{p.destination.name}<br /><a className="inline-flex items-center gap-1 font-mono text-[11.5px] text-accent hover:underline" target="_blank" rel="noreferrer" href={`/mock-portal/forms/${p.destination.form_ref}`}>{p.destination.form_ref}<ExternalLink className="size-3" /></a></span> },
              { label: "Proposal hash", value: <HashValue value={p.proposal_hash} /> },
              { label: "Live hash now", value: a.is_current ? <span className="inline-flex items-center gap-1 text-ok"><ShieldCheck className="size-3.5" />matches</span> : <span className="text-bad">differs</span> },
              { label: "Destination fingerprint", value: <HashValue value={p.destination_fingerprint} /> },
              { label: "Requested by", value: <span className="font-mono text-[12px]">{a.requested_by}</span> },
              { label: "Requested", value: fmtDateTime(a.created_at) },
              ...(a.decided_by ? [{ label: "Decided by", value: `${a.decided_by.name} (${a.decided_by.role})` }, { label: "Decided at", value: fmtDateTime(a.decided_at) }] : []),
              ...(a.decision_note ? [{ label: "Note", value: a.decision_note }] : []),
              ...(p.run_id ? [{ label: "Agent run", value: <Link className="text-accent hover:underline" href={`/agent/${p.run_id}`}>#{p.run_id}</Link> }] : []),
            ]} /></div>
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
        title={`Approve ${p.kind === "SUPPLEMENT" ? "supplementary upload" : "upload"} of ${p.included_roll_numbers.length} student(s)?`} confirmLabel="Approve and execute"
        description="The backend re-computes the roster from live data. If it no longer matches this hash the approval is refused. On approval the agent uploads to the destination, independently verifies the result, then emails the employer contact this roster and tells each student they were registered."
        onConfirm={() => ack ? approve.mutate() : toast.error("Confirm that you reviewed the roster")}>
        <div className="space-y-3">
          <div className="rounded-md bg-subtle p-3 text-[12.5px]"><p>Destination: <strong>{p.destination.name}</strong></p><p>Hash: <span className="font-mono">{p.proposal_hash.slice(0, 24)}…</span></p></div>
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
