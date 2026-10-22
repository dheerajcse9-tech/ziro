"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, Save, Send } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/ui/data-table";
import { KpiCard } from "@/components/ui/layout";
import { ConfirmDialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Input, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { relTime } from "@/lib/format";
import type { Drive, DriveResultRow, StudentRequest } from "@/lib/types";

export function InvitationsPanel({ drive }: { drive: Drive }) {
  const me = useMe();
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const q = useQuery({ queryKey: ["invitations", drive.id], queryFn: () => api.get<{ items: StudentRequest[]; counts: Record<string, number> }>(`/api/drives/${drive.id}/invitations`) });
  const send = useMutation({
    mutationFn: () => api.post<{ sent: string[]; already_invited: string[] }>(`/api/drives/${drive.id}/invitations`),
    onSuccess: (r) => { setConfirm(false); toast.success(`Consent form emailed to ${r.sent.length} student(s)`, { description: r.already_invited.length ? `${r.already_invited.length} already had it` : undefined }); qc.invalidateQueries(); },
    onError: (e) => { setConfirm(false); toastError(e); },
  });
  const perDrive = drive.criteria?.required_consent_scope === "DRIVE_REGISTRATION";
  const c = q.data?.counts ?? {};
  return (
    <div className="space-y-4">
      <Banner tone="info" title={perDrive ? "This drive only shares students who say YES on its consent form" : "This drive uses the consent already on file"}>
        {perDrive
          ? "Each student gets one personal link to the drive's consent form. Only students who say YES (and meet the criteria) are included when the roster is prepared."
          : "The consent form still tells students about the drive and records their answer; saying NO excludes them from this drive."}
      </Banner>
      <div className="grid gap-3 sm:grid-cols-4">
        <KpiCard label="Consent forms sent" value={q.data?.items.length ?? "—"} />
        <KpiCard label="Said YES" value={c.ACCEPTED ?? 0} tone="ok" />
        <KpiCard label="Said NO" value={c.DECLINED ?? 0} />
        <KpiCard label="No answer yet" value={(c.SENT ?? 0) + (c.EXPIRED ?? 0)} tone={(c.SENT ?? 0) ? "warn" : "neutral"} hint={c.EXPIRED ? `${c.EXPIRED} expired` : undefined} />
      </div>
      <Card>
        <CardHeader title="Consent form answers" description="One personal link per student per drive. Sending again only reaches students who do not have one yet."
          actions={can(me.data, "manageDrives") ? <Button variant="primary" size="sm" icon={<Send className="size-4" />} onClick={() => setConfirm(true)}>Send consent form</Button> : undefined} />
        {!q.data ? <LoadingRows rows={4} /> : (
          <DataTable data={q.data.items} pageSize={40} empty={<EmptyState icon={<Mail className="size-5" />} title="No consent forms sent yet" />}
            columns={[
              { accessorKey: "roll_number", header: "Roll", cell: ({ row }) => <Link className="font-mono text-[12px] text-accent hover:underline" href={`/students/${row.original.student_id}`}>{row.original.roll_number}</Link> },
              { accessorKey: "full_name", header: "Name" },
              { accessorKey: "status", header: "Answer", cell: ({ row }) => <StatusBadge status={row.original.status} tone={row.original.status === "ACCEPTED" ? "ok" : row.original.status === "DECLINED" ? "neutral" : row.original.status === "EXPIRED" ? "bad" : "warn"} label={row.original.status === "SENT" ? "Waiting" : undefined} /> },
              { accessorKey: "created_at", header: "Sent", cell: ({ row }) => <span className="text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
              { accessorKey: "responded_at", header: "Answered", cell: ({ row }) => <span className="text-[12px] text-muted">{row.original.responded_at ? relTime(row.original.responded_at) : "—"}</span> },
              { accessorKey: "criteria_version", header: "Criteria", cell: ({ row }) => `v${row.original.criteria_version}` },
            ]} />
        )}
      </Card>
      <ConfirmDialog open={confirm} onOpenChange={setConfirm} title="Send the consent form?" confirmLabel="Send consent form" loading={send.isPending} onConfirm={() => send.mutate()}
        description={`Students who do not have a consent form for ${drive.title} yet get an email with a personal yes/no link. Drives created from company emails send it to every student; other drives to students who meet the criteria.`} />
    </div>
  );
}

const OUTCOMES = [["", "Not recorded"], ["SELECTED", "Selected"], ["NOT_SELECTED", "Not selected"], ["ABSENT", "Absent"], ["WITHDRAWN", "Withdrew"]];

export function ResultsPanel({ drive }: { drive: Drive }) {
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["results", drive.id], queryFn: () => api.get<DriveResultRow[]>(`/api/drives/${drive.id}/results`) });
  const [draft, setDraft] = useState<Record<number, { outcome: string; note: string }>>({});
  const [notify, setNotify] = useState(true);
  useEffect(() => {
    if (q.data) setDraft(Object.fromEntries(q.data.map((r) => [r.student_id, { outcome: r.outcome ?? "", note: r.note ?? "" }])));
  }, [q.data]);
  const put = useMutation({
    mutationFn: () => api.put<{ changed: number; notified: number }>(`/api/drives/${drive.id}/results`, {
      results: Object.entries(draft).filter(([, v]) => v.outcome)
        .map(([sid, v]) => ({ student_id: Number(sid), outcome: v.outcome, note: v.note || null })),
      notify,
    }),
    onSuccess: (r) => { toast.success(`${r.changed} result(s) saved`, { description: notify ? `${r.notified} student email(s) sent` : "No emails sent" }); qc.invalidateQueries(); },
    onError: toastError,
  });
  const editable = can(me.data, "editData");
  const counts = Object.values(draft).reduce<Record<string, number>>((a, v) => ({ ...a, [v.outcome || "NONE"]: (a[v.outcome || "NONE"] ?? 0) + 1 }), {});
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4">
        <KpiCard label="Sent to employer" value={q.data?.length ?? "—"} />
        <KpiCard label="Selected" value={counts.SELECTED ?? 0} tone="ok" />
        <KpiCard label="Not selected" value={counts.NOT_SELECTED ?? 0} />
        <KpiCard label="Not recorded yet" value={counts.NONE ?? 0} tone={counts.NONE ? "warn" : "neutral"} />
      </div>
      <Card>
        <CardHeader title="Selection results" description="Record the employer's outcome for each student who was sent on this drive's roster."
          actions={editable ? <>
            <label className="flex items-center gap-1.5 text-[12.5px] text-ink-2"><input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} />Email students their result</label>
            <Button variant="primary" size="sm" icon={<Save className="size-4" />} loading={put.isPending} onClick={() => put.mutate()}>Save results</Button>
          </> : undefined} />
        {!q.data ? <LoadingRows rows={4} /> : q.data.length === 0 ? <EmptyState title="No students sent yet" description="Results can be recorded after the roster is uploaded and verified." /> : (
          <table className="data-table w-full">
            <thead><tr><th>Roll</th><th>Name</th><th>Branch</th><th>Outcome</th><th>Note</th></tr></thead>
            <tbody>{q.data.map((r) => (
              <tr key={r.student_id}>
                <td><Link className="font-mono text-[12px] text-accent hover:underline" href={`/students/${r.student_id}`}>{r.roll_number}</Link></td>
                <td className="whitespace-nowrap">{r.full_name}</td><td>{r.branch ?? "—"}</td>
                <td><Select aria-label={`Outcome for ${r.roll_number}`} disabled={!editable} value={draft[r.student_id]?.outcome ?? ""}
                  onChange={(e) => setDraft({ ...draft, [r.student_id]: { ...(draft[r.student_id] ?? { note: "" }), outcome: e.target.value } })}>
                  {OUTCOMES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></td>
                <td><Input aria-label={`Note for ${r.roll_number}`} disabled={!editable} value={draft[r.student_id]?.note ?? ""} placeholder="Optional"
                  onChange={(e) => setDraft({ ...draft, [r.student_id]: { ...(draft[r.student_id] ?? { outcome: "" }), note: e.target.value } })} /></td>
              </tr>))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
