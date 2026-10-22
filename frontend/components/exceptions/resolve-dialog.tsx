"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { JsonView } from "@/components/ui/evidence";
import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Textarea } from "@/components/ui/primitives";
import { Banner } from "@/components/ui/states";
import { api } from "@/lib/api";
import { toastError } from "@/lib/errors";
import { cn, fmtDateTime } from "@/lib/format";
import type { ExceptionItem } from "@/lib/types";

export function ResolveDialog({ item, onClose }: { item: ExceptionItem | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [preferred, setPreferred] = useState<number | null>(null);
  const resolve = useMutation({
    mutationFn: (resolution: string) => api.post<ExceptionItem>(`/api/exceptions/${item!.id}/resolve`, {
      resolution, note: note || null, preferred_drive_id: resolution === "PREFER_DRIVE" ? preferred : null,
    }),
    onSuccess: () => { toast.success("Exception resolved; affected approvals were re-validated"); setNote(""); setPreferred(null); onClose(); qc.invalidateQueries(); },
    onError: toastError,
  });
  const review = useMutation({
    mutationFn: (apply: boolean) => api.post(`/api/student-requests/${item!.detail.request_id}/review`, { apply, note: note || null }),
    onSuccess: (_r, apply) => { toast.success(apply ? "Verified details applied to the student record" : "Submitted details rejected"); setNote(""); onClose(); qc.invalidateQueries(); },
    onError: toastError,
  });
  const emailStudent = useMutation({
    mutationFn: () => api.post(`/api/students/${item!.student_id}/data-requests`, {
      fields: (item!.detail.missing ?? []).map((m: string) => ({ CGPA: "cgpa", BACKLOGS: "active_backlogs", BRANCH: "branch", GRADUATION_YEAR: "graduation_year" } as Record<string, string>)[m]).filter(Boolean),
      drive_id: item!.drive_id,
    }),
    onSuccess: () => toast.success("Email sent with a personal link to provide the missing details"),
    onError: toastError,
  });
  if (!item) return null;
  const isSubmission = item.kind === "DATA_SUBMITTED";
  const isClash = item.kind === "SCHEDULE_CONFLICT";
  const d = item.detail ?? {};
  const isData = item.kind === "MISSING_STUDENT_DATA" || item.kind === "INVALID_STUDENT_DATA";

  return (
    <Dialog open={!!item} onOpenChange={(o) => !o && onClose()} wide title={item.title}
      description={`${item.kind.replace(/_/g, " ").toLowerCase()} · raised ${fmtDateTime(item.created_at)}`}
      footer={item.status !== "OPEN" ? <Button onClick={onClose}>Close</Button> : isSubmission ? <>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="danger" loading={review.isPending && review.variables === false} onClick={() => review.mutate(false)}>Reject</Button>
        <Button variant="success" loading={review.isPending && review.variables === true} onClick={() => review.mutate(true)}>Verify &amp; apply to record</Button>
      </> : isClash ? <>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="primary" disabled={!preferred} loading={resolve.isPending} onClick={() => resolve.mutate("PREFER_DRIVE")}>Assign to selected drive</Button>
      </> : <>
        <Button onClick={onClose}>Cancel</Button>
        <Button disabled={note.trim().length < 3} loading={resolve.isPending && resolve.variables === "DISMISSED"} onClick={() => resolve.mutate("DISMISSED")}>Dismiss</Button>
        <Button variant="primary" disabled={note.trim().length < 3} loading={resolve.isPending && resolve.variables === "ACKNOWLEDGED"} onClick={() => resolve.mutate("ACKNOWLEDGED")}>Acknowledge</Button>
      </>}>
      <div className="space-y-4">
        {item.status !== "OPEN" && <Banner tone="ok" title={`${item.status}: ${item.resolution ?? ""}`}>{item.resolution_note}</Banner>}
        {isClash && item.status === "OPEN" && (
          <>
            <p className="text-[13px] text-ink-2">Student <span className="font-mono font-semibold">{d.roll_number}</span> is eligible for both drives, whose slots overlap
              {d.overlap_start ? ` from ${fmtDateTime(d.overlap_start, "Asia/Kolkata", { year: undefined })} to ${fmtDateTime(d.overlap_end, "Asia/Kolkata", { year: undefined, day: undefined, month: undefined })} IST` : ""}. Choose the drive they will attend. The other assignment is withdrawn and both drives&apos; open approvals are re-validated.</p>
            <div className="grid gap-2 sm:grid-cols-2">
              {[{ id: d.drive_id, title: d.drive_title, note: "Drive being prepared when the clash was detected" },
                { id: d.other_drive_id, title: d.other_drive_title, note: `Existing assignment: ${String(d.other_assignment_status ?? "").toLowerCase()}` }].map((opt) => (
                <button key={opt.id} type="button" onClick={() => setPreferred(opt.id)} aria-pressed={preferred === opt.id}
                  className={cn("rounded-md border p-3 text-left", preferred === opt.id ? "border-accent bg-accent-soft ring-2 ring-accent/30" : "border-line hover:bg-subtle")}>
                  <p className="text-[13px] font-semibold">{opt.title}</p><p className="text-[12px] text-muted">{opt.note}</p>
                </button>))}
            </div>
            <Field label="Note" htmlFor="clash_note"><Textarea id="clash_note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. Student preference confirmed by email" /></Field>
          </>
        )}
        {isSubmission && item.status === "OPEN" && (
          <>
            <p className="text-[13px] text-ink-2">Student <span className="font-mono font-semibold">{d.roll_number}</span> submitted these details through their personal link. Check them against the official record (marksheet, ERP) before applying.</p>
            <table className="data-table w-full rounded-md border border-line"><thead><tr><th>Field</th><th>Currently on record</th><th>Submitted by student</th></tr></thead>
              <tbody>{Object.entries(d.submitted ?? {}).map(([k, v]) => <tr key={k}><td>{k}</td><td className="text-muted">{String(d.current?.[k] ?? "missing")}</td><td className="font-semibold">{String(v)}</td></tr>)}</tbody></table>
            <Field label="Note (optional)" htmlFor="sub_note"><Textarea id="sub_note" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          </>
        )}
        {!isClash && !isSubmission && item.status === "OPEN" && (
          <>
            {isData && item.student_id && <Banner tone="info" title="Fix the source record" action={(d.missing ?? []).length > 0 ? <Button size="sm" loading={emailStudent.isPending} onClick={() => emailStudent.mutate()}>Email student for missing data</Button> : undefined}>Email the student a personal link to provide the missing details, or correct the record yourself: <Link className="underline" href={`/students/${item.student_id}`}>open student record</Link>. Acknowledge only if the data cannot be obtained before the drive.</Banner>}
            {item.kind === "INTAKE_REVIEW" && <Banner tone="info" title="Resolve in the Company Inbox">Open the email in the <Link className="underline" href="/inbox">Company Inbox</Link>: correct the details or confirm them, and the agents continue (this item resolves automatically).</Banner>}
            {item.kind === "CRITERIA_MISMATCH" && item.drive_id && <Banner tone="info" title="Resolve by updating criteria">Create a new criteria version on <Link className="underline" href={`/drives/${item.drive_id}`}>the drive</Link> (auto-resolves this item), or answer the agent run&apos;s question.</Banner>}
            <Field label="Resolution note (required)" htmlFor="exc_note"><Textarea id="exc_note" rows={3} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
          </>
        )}
        <JsonView value={item.detail} label="Evidence" />
      </div>
    </Dialog>
  );
}
