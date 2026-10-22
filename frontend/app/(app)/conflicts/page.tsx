"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ResolveDialog } from "@/components/exceptions/resolve-dialog";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { fmtSlot, fmtTime, relTime } from "@/lib/format";
import type { DrivePair, ExceptionItem } from "@/lib/types";

export default function ConflictsPage() {
  const me = useMe();
  const q = useQuery({ queryKey: ["conflicts"], queryFn: () => api.get<{ drive_pairs: DrivePair[]; student_conflicts: ExceptionItem[] }>("/api/conflicts") });
  const [selected, setSelected] = useState<ExceptionItem | null>(null);
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const open = q.data?.student_conflicts.filter((c) => c.status === "OPEN") ?? [];
  const closed = q.data?.student_conflicts.filter((c) => c.status !== "OPEN") ?? [];
  return (
    <>
      <PageHeader title="Schedule clashes"
        description="Drive slots are compared as half-open intervals in UTC (so drives in different timezones compare correctly, and back-to-back slots do not clash). A clash only matters for a student actually assigned to both drives." />
      <Card className="mb-4">
        <CardHeader title="Overlapping drive slots" />
        {!q.data ? <LoadingRows rows={3} /> : q.data.drive_pairs.length === 0 ? <EmptyState title="No overlapping drives" /> : (
          <table className="data-table w-full"><thead><tr><th>Drive A</th><th>Drive B</th><th>Overlap (IST)</th><th>Students assigned to both</th></tr></thead>
            <tbody>{q.data.drive_pairs.map((p, i) => (
              <tr key={i}>
                <td><Link className="font-medium hover:underline" href={`/drives/${p.drive_a.id}`}>{p.drive_a.title}</Link><div className="text-[12px] text-muted">{fmtSlot(p.drive_a.starts_at, p.drive_a.ends_at, p.drive_a.timezone)}</div></td>
                <td><Link className="font-medium hover:underline" href={`/drives/${p.drive_b.id}`}>{p.drive_b.title}</Link><div className="text-[12px] text-muted">{fmtSlot(p.drive_b.starts_at, p.drive_b.ends_at, p.drive_b.timezone)}</div></td>
                <td className="tabular">{p.overlap_start ? `${fmtTime(p.overlap_start, "Asia/Kolkata")}–${fmtTime(p.overlap_end, "Asia/Kolkata")}` : `within ${p.buffer_minutes} min buffer`}</td>
                <td className="tabular">{p.students_assigned_to_both.length}</td>
              </tr>))}</tbody></table>
        )}
      </Card>
      <Card className="mb-4">
        <CardHeader title={`Open student clashes (${open.length})`} description="Flagged before assignment. These students are held out of the later drive's roster until resolved." />
        {!q.data ? <LoadingRows rows={3} /> : open.length === 0 ? <EmptyState title="No open clashes" /> : (
          <table className="data-table w-full"><thead><tr><th>Student</th><th>Drive being prepared</th><th>Clashes with</th><th>Raised</th><th /></tr></thead>
            <tbody>{open.map((c) => (
              <tr key={c.id}>
                <td className="font-mono text-[12px]">{c.detail.roll_number}</td>
                <td>{c.detail.drive_title}</td>
                <td>{c.detail.other_drive_title} <StatusBadge status={c.detail.other_assignment_status} /></td>
                <td className="text-[12px] text-muted">{relTime(c.created_at)}</td>
                <td className="text-right"><Button size="sm" variant={can(me.data, "editData") ? "primary" : "secondary"} onClick={() => setSelected(c)}>{can(me.data, "editData") ? "Resolve" : "View"}</Button></td>
              </tr>))}</tbody></table>
        )}
      </Card>
      {closed.length > 0 && (
        <Card>
          <CardHeader title="Resolved clashes" />
          <table className="data-table w-full"><thead><tr><th>Student</th><th>Drives</th><th>Assigned to</th><th>Note</th></tr></thead>
            <tbody>{closed.map((c) => (
              <tr key={c.id}><td className="font-mono text-[12px]">{c.detail.roll_number}</td><td className="text-[12px]">{c.detail.drive_title} ↔ {c.detail.other_drive_title}</td>
                <td>{c.detail.preferred_drive_id === c.detail.drive_id ? c.detail.drive_title : c.detail.other_drive_title}</td><td className="text-[12px] text-muted">{c.resolution_note ?? "—"}</td></tr>))}</tbody></table>
        </Card>
      )}
      <ResolveDialog item={selected} onClose={() => setSelected(null)} />
    </>
  );
}
