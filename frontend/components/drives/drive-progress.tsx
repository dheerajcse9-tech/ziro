"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Check, CircleDashed, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button, ButtonLink, Card } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn, relTime } from "@/lib/format";
import type { AgentRun, DriveDetail } from "@/lib/types";

type State = "done" | "now" | "todo";

/** The three steps of a drive, where it is now, and the one thing the signed-in person can do next. */
export function DriveProgress({ drive: d }: { drive: DriveDetail }) {
  const me = useMe();
  const router = useRouter();
  const qc = useQueryClient();
  const run = useMutation({
    mutationFn: () => api.post<AgentRun>("/api/agent/runs", {
      objective: `Prepare the eligible roster for drive #${d.id} (${d.title}), identify unresolved records, and show me the proposal for approval.` }),
    onSuccess: (r) => { qc.invalidateQueries(); router.push(`/agent/${r.id}`); },
    onError: toastError,
  });

  const form = d.consent_form;
  const approval = [...d.approvals].sort((a, b) => b.id - a.id)[0];
  const upload = [...d.uploads].sort((a, b) => b.id - a.id)[0];
  const listRun = [...d.runs].filter((r) => r.intent === "PREPARE_ROSTER").sort((a, b) => b.id - a.id)[0];
  const sent = upload?.latest_verification?.status === "VERIFIED";
  const waitingApproval = approval?.status === "PENDING";
  const needsAnswer = listRun && ["NEEDS_REVIEW", "FAILED"].includes(listRun.status) && (!approval || listRun.created_at > approval.created_at);

  const s1: State = form ? (form.status === "OPEN" ? "now" : "done") : "todo";
  const s2: State = approval && approval.status !== "INVALIDATED" && approval.status !== "REJECTED" ? "done" : s1 === "now" ? "todo" : "now";
  const s3: State = sent ? "done" : waitingApproval || approval?.status === "APPROVED" ? "now" : "todo";

  const isOfficer = can(me.data, "runAgent");
  const isApprover = can(me.data, "approve");
  const steps = [
    { n: 1, title: "Consent form", state: s1,
      text: !form ? "Not sent yet" : `${form.accepted} of ${form.sent} said YES · ${form.status === "OPEN" ? `closes ${relTime(form.closes_at)}` : "closed"}`,
      who: "Drive upload officer" },
    { n: 2, title: "Student list", state: s2,
      text: approval && s2 === "done" ? `${approval.included_count} student(s) on the list`
        : needsAnswer ? "The agents need an answer" : approval?.status === "INVALIDATED" ? "Details changed — prepare the list again"
        : approval?.status === "REJECTED" ? "Rejected — prepare the list again"
        : s1 === "now" ? "Best prepared after the consent form closes" : "Not prepared yet",
      who: "Placement officer" },
    { n: 3, title: "Approved & sent to company", state: s3,
      text: sent ? `Sent and checked ${relTime(upload!.completed_at ?? upload!.created_at)}` : waitingApproval ? "Waiting for the Approver" : "Not yet",
      who: "Approver" },
  ];

  // The single next action for this person, if any.
  let action: React.ReactNode = null;
  if (isApprover && waitingApproval) {
    action = <ButtonLink variant="primary" href={`/approvals/${approval!.id}`} icon={<ArrowRight className="size-4" />}>Review the list</ButtonLink>;
  } else if (isOfficer && needsAnswer) {
    action = <ButtonLink variant="primary" href={`/agent/${listRun!.id}`} icon={<ArrowRight className="size-4" />}>Answer the agents</ButtonLink>;
  } else if (isOfficer && s2 !== "done" && !waitingApproval) {
    action = <Button variant="primary" loading={run.isPending} onClick={() => run.mutate()}>Prepare the student list</Button>;
  } else if (isOfficer && sent) {
    action = <Button loading={run.isPending} onClick={() => run.mutate()}>Check for late students</Button>;
  }
  if (sent && upload) {
    action = <>{action}<ButtonLink href={`/verification/${upload.id}`}>See what was sent</ButtonLink></>;
  }

  return (
    <Card className="mb-4">
      <div className="flex flex-wrap items-center gap-4 p-4">
        <ol className="flex flex-1 flex-wrap items-stretch gap-2">
          {steps.map((s, i) => (
            <li key={s.n} className="flex min-w-[12rem] flex-1 items-center gap-2">
              <div className={cn("flex-1 rounded-md border p-3", s.state === "now" ? "border-accent bg-accent-soft/50" : "border-line bg-surface")}>
                <p className="flex items-center gap-1.5 text-[13px] font-semibold text-ink">
                  {s.state === "done" ? <Check className="size-4 text-ok" /> : s.state === "now" ? <Loader2 className="size-4 text-accent" /> : <CircleDashed className="size-4 text-faint" />}
                  {s.n}. {s.title}
                </p>
                <p className="mt-0.5 text-[12.5px] text-muted">{s.text}</p>
                <p className="text-[11px] text-faint">{s.who}</p>
              </div>
              {i < steps.length - 1 && <ArrowRight className="hidden size-4 shrink-0 text-faint md:block" aria-hidden />}
            </li>
          ))}
        </ol>
        {action && <div className="flex flex-wrap gap-2">{action}</div>}
      </div>
    </Card>
  );
}
