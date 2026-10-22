"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CheckCircle2, CircleAlert, Info, Send, XCircle, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { Dialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Input } from "@/components/ui/primitives";
import { Banner, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { relTime } from "@/lib/format";
import type { AgentRun, ApprovalReview, ConsentFormSummary, FollowupCandidate, ReviewCheck, StudentRequest } from "@/lib/types";

const ICON = {
  PASS: <CheckCircle2 className="size-4 text-ok" />, WARN: <CircleAlert className="size-4 text-warn" />,
  FAIL: <XCircle className="size-4 text-bad" />, INFO: <Info className="size-4 text-info" />,
};
const VERDICT = {
  READY: { tone: "ok" as const, title: "Approval Review Agent: ready to approve" },
  READY_WITH_NOTES: { tone: "warn" as const, title: "Approval Review Agent: ready, with notes" },
  DO_NOT_APPROVE: { tone: "bad" as const, title: "Approval Review Agent: do not approve" },
};
const FIELD = { cgpa: "CGPA", active_backlogs: "backlogs", branch: "branch", graduation_year: "batch", consent: "consent" } as Record<string, string>;

function localInput(d: Date) {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function FollowupDialog({ driveId, open, onOpenChange, count }: { driveId: number; open: boolean; onOpenChange: (o: boolean) => void; count: number }) {
  const qc = useQueryClient();
  const [closes, setCloses] = useState(() => localInput(new Date(Date.now() + 2 * 86400_000)));
  const send = useMutation({
    mutationFn: () => api.post<AgentRun>(`/api/drives/${driveId}/followups`, { closes_at: `${closes}:00` }),
    onSuccess: (run) => {
      toast.success(run.summary ?? "Personal forms sent", { action: { label: "Open run", onClick: () => { window.location.href = `/agent/${run.id}`; } } });
      onOpenChange(false); qc.invalidateQueries();
    },
    onError: toastError,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={`Ask ${count} student(s) for missing details or consent`}
      description="The Form Builder Agent makes each student a personal form with exactly what is missing; the Follow-up Mailer Agent emails it. Answers are applied by the Response Reader Agent and re-checked against this drive."
      footer={<><Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" loading={send.isPending} onClick={() => send.mutate()} icon={<Send className="size-4" />}>Send personal forms</Button></>}>
      <Field label="Forms close at (drive's local time)" htmlFor="fu_close" hint="After this time the Deadline Agent closes the forms; late answers are not accepted. Must be before the drive starts.">
        <Input id="fu_close" type="datetime-local" value={closes} onChange={(e) => setCloses(e.target.value)} />
      </Field>
    </Dialog>
  );
}

function CheckRow({ c }: { c: ReviewCheck }) {
  return (
    <li className="px-4 py-2.5">
      <div className="flex items-start gap-2">
        <span className="mt-0.5">{ICON[c.status]}</span>
        <div className="min-w-0 flex-1">
          <p className="text-[13px] font-medium text-ink">{c.label}</p>
          <p className="text-[12.5px] text-muted">{c.detail}</p>
          {c.key === "students" && c.items.length > 0 && (
            <ul className="mt-1 text-[12px] text-bad">{c.items.map((x: { roll_number: string; problem: string }) => <li key={x.roll_number}><span className="font-mono">{x.roll_number}</span>: {x.problem}</li>)}</ul>
          )}
          {c.key === "self_reported" && (
            <p className="mt-1 text-[12px] text-ink-2">{c.items.map((x: { roll_number: string; fields: string[] }) => `${x.roll_number} (${x.fields.join(", ")})`).join(" · ")}</p>
          )}
        </div>
      </div>
    </li>
  );
}

export function ReviewPanel({ approvalId, driveId, pending }: { approvalId: number; driveId: number; pending: boolean }) {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const q = useQuery({ queryKey: ["approval-review", approvalId], queryFn: () => api.get<ApprovalReview>(`/api/approvals/${approvalId}/review`),
    enabled: can(me.data, "followUp") || can(me.data, "approve") });
  const fu = useQuery({ queryKey: ["followups", driveId], queryFn: () => api.get<{ candidates: FollowupCandidate[]; forms: StudentRequest[]; consent_form: ConsentFormSummary | null }>(`/api/drives/${driveId}/followups`),
    refetchInterval: 15_000 });
  if (!(can(me.data, "followUp") || can(me.data, "approve"))) return null;
  const r = q.data;
  const followCheck = r?.checks.find((c) => c.key === "followup");
  const notAsked = (fu.data?.candidates ?? []).filter((c) => !c.form_open);
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Bot className="size-4 text-faint" />Approval Review Agent</span>}
        description={r ? `Re-checked from live data ${relTime(r.reviewed_at)} · advisory only` : "Independent re-check of this proposal"}
        actions={<Button size="sm" variant="ghost" loading={q.isFetching} onClick={() => q.refetch()}>Re-check</Button>} />
      {q.isError ? <div className="p-4"><ErrorState error={q.error} /></div> : !r ? <LoadingRows rows={4} /> : (
        <>
          {r.summary && (
            <div className="mx-4 mt-3 rounded-lg border border-accent/30 bg-accent-soft/40 p-3">
              <p className="mb-1 flex items-center gap-1.5 text-[11.5px] font-semibold uppercase tracking-wide text-accent">
                <Sparkles className="size-3.5" />{r.summary.mode === "AI" ? "Summary by Gemini" : "Summary"}</p>
              <p className="text-[13.5px] leading-relaxed text-ink">{r.summary.text}</p>
              {r.summary.mode === "RULES" && r.summary.ai_error && <p className="mt-1 text-[11.5px] text-muted">Written from the checks below (AI not used: {r.summary.ai_error}).</p>}
            </div>
          )}
          <div className="px-4 pt-3"><Banner tone={VERDICT[r.verdict].tone} title={VERDICT[r.verdict].title} /></div>
          <ul className="divide-y divide-line">{r.checks.map((c) => <CheckRow key={c.key} c={c} />)}</ul>
          {followCheck && followCheck.items.length > 0 && (
            <div className="border-t border-line px-4 py-3">
              <p className="mb-2 text-[12px] font-semibold text-ink-2">Could still qualify</p>
              <ul className="space-y-1 text-[12.5px]">
                {followCheck.items.map((x: FollowupCandidate) => (
                  <li key={x.roll_number} className="flex items-center justify-between gap-2">
                    <span><span className="font-mono">{x.roll_number}</span> {x.full_name}</span>
                    <span className="flex flex-wrap justify-end gap-1">{x.fields.map((f) => <Tag key={f} tone={f === "consent" ? "info" : "warn"}>{FIELD[f] ?? f}</Tag>)}
                      {x.form_open && <Tag tone="ok">form sent</Tag>}</span>
                  </li>
                ))}
              </ul>
              {pending && notAsked.length > 0 && (
                <Button className="mt-3 w-full" variant="primary" icon={<Send className="size-4" />} onClick={() => setOpen(true)}>
                  Email {notAsked.length} student(s) a personal form
                </Button>
              )}
            </div>
          )}
        </>
      )}
      {fu.data && fu.data.forms.length > 0 && (
        <div className="border-t border-line px-4 py-3">
          <p className="mb-2 text-[12px] font-semibold text-ink-2">Personal forms for this drive</p>
          <ul className="space-y-1 text-[12.5px]">{fu.data.forms.slice(0, 12).map((f) => (
            <li key={f.id} className="flex items-center justify-between gap-2">
              <Link href={`/students/${f.student_id}`} className="hover:underline"><span className="font-mono">{f.roll_number}</span> {f.full_name}</Link>
              <StatusBadge status={f.status} tone={f.status === "APPLIED" ? "ok" : f.status === "EXPIRED" ? "neutral" : f.status === "SENT" ? "info" : "warn"}
                label={{ SENT: "Waiting", APPLIED: "Answered", SUBMITTED: "Awaiting officer", EXPIRED: "Closed, no answer", REJECTED: "Rejected" }[f.status] ?? f.status} />
            </li>))}</ul>
        </div>
      )}
      <FollowupDialog driveId={driveId} open={open} onOpenChange={setOpen} count={notAsked.length} />
    </Card>
  );
}
