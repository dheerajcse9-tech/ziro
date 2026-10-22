"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ResolveDialog } from "@/components/exceptions/resolve-dialog";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Button, ButtonLink, Card, CardHeader, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api, qs } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { relTime, titleCase } from "@/lib/format";
import type { ExceptionItem } from "@/lib/types";

const KINDS = ["SCHEDULE_CONFLICT", "MISSING_STUDENT_DATA", "MISSING_CONSENT", "CRITERIA_MISMATCH", "AMBIGUOUS_CRITERIA", "CRITERIA_MISSING",
  "SUSPICIOUS_SOURCE_CONTENT", "SOURCE_RETRIEVAL_FAILED", "APPROVAL_INVALIDATED", "UPLOAD_FAILED", "UPLOAD_DISCREPANCY", "WITHDRAWAL_REQUIRED"];

function Inner() {
  const params = useSearchParams();
  const router = useRouter();
  const me = useMe();
  const status = params.get("status") ?? "OPEN";
  const kind = params.get("kind") ?? "";
  const driveId = params.get("drive_id") ?? "";
  const [selected, setSelected] = useState<ExceptionItem | null>(null);
  const q = useQuery({ queryKey: ["exceptions", status, kind, driveId], queryFn: () => api.get<{ items: ExceptionItem[]; open_counts_by_kind: Record<string, number> }>(`/api/exceptions${qs({ status, kind, drive_id: driveId })}`) });
  const set = (k: string, v: string) => { const n = new URLSearchParams(params.toString()); if (v) n.set(k, v); else n.delete(k); router.replace(`/exceptions?${n}`); };

  return (
    <>
      <PageHeader title="Issues" description="Things the agents could not decide alone, such as missing student details or a student booked on two drives at the same time."
        actions={<ButtonLink href="/conflicts">Schedule clashes</ButtonLink>} />
      {q.data && Object.keys(q.data.open_counts_by_kind).length > 0 && (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {Object.entries(q.data.open_counts_by_kind).map(([k, n]) => (
            <button key={k} onClick={() => set("kind", kind === k ? "" : k)} className={`rounded-full px-2.5 py-1 text-[12px] ring-1 ring-inset ${kind === k ? "bg-accent text-white ring-accent" : "bg-surface ring-line-strong hover:bg-subtle"}`}>{kindLabel(k)} · {n}</button>))}
        </div>
      )}
      <Card>
        <CardHeader title="Issues" actions={<>
          <Select aria-label="Status" value={status} onChange={(e) => set("status", e.target.value)}>
            <option value="OPEN">Open</option><option value="RESOLVED">Resolved</option><option value="DISMISSED">Dismissed</option><option value="ALL">All</option>
          </Select>
          <Select aria-label="Kind" value={kind} onChange={(e) => set("kind", e.target.value)}>
            <option value="">All kinds</option>{KINDS.map((k) => <option key={k} value={k}>{kindLabel(k)}</option>)}
          </Select>
          {driveId && <Button size="sm" variant="ghost" onClick={() => set("drive_id", "")}>Drive #{driveId} ✕</Button>}
        </>} />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <LoadingRows /> : (
          <DataTable data={q.data.items} pageSize={30}
            empty={<EmptyState title={status === "OPEN" ? "Inbox zero" : "No exceptions"} description="Nothing needs attention with these filters." />}
            columns={[
              { accessorKey: "severity", header: "Severity", cell: ({ row }) => <StatusBadge status={row.original.severity} /> },
              { accessorKey: "kind", header: "Kind", cell: ({ row }) => <Tag>{kindLabel(row.original.kind)}</Tag> },
              { accessorKey: "title", header: "Issue", cell: ({ row }) => <button className="text-left text-[13px] text-ink hover:underline" onClick={() => setSelected(row.original)}>{row.original.title}</button> },
              { id: "links", header: "Context", enableSorting: false, cell: ({ row }) => <span className="flex flex-wrap gap-2 text-[12px]">
                {row.original.drive_id && <Link className="text-accent hover:underline" href={`/drives/${row.original.drive_id}`}>drive #{row.original.drive_id}</Link>}
                {row.original.student_id && <Link className="text-accent hover:underline" href={`/students/${row.original.student_id}`}>student</Link>}
                {row.original.run_id && <Link className="text-accent hover:underline" href={`/agent/${row.original.run_id}`}>run #{row.original.run_id}</Link>}</span> },
              { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
              { accessorKey: "created_at", header: "Raised", cell: ({ row }) => <span className="whitespace-nowrap text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
              { id: "act", header: "", enableSorting: false, cell: ({ row }) => <Button size="sm" onClick={() => setSelected(row.original)}>{row.original.status === "OPEN" && can(me.data, "editData") ? "Resolve" : "View"}</Button> },
            ]} />
        )}
      </Card>
      <ResolveDialog item={selected} onClose={() => setSelected(null)} />
    </>
  );
}

const KIND_LABEL: Record<string, string> = {
  MISSING_CONSENT: "No YES/NO answer", MISSING_STUDENT_DATA: "Missing details", SCHEDULE_CONFLICT: "Schedule clash",
  CRITERIA_MISMATCH: "Criteria differ", AMBIGUOUS_CRITERIA: "Unclear criteria", INTAKE_REVIEW: "Company email to check",
  DATA_SUBMITTED: "Student sent details", INVALID_STUDENT_DATA: "Invalid details", APPROVAL_INVALIDATED: "List out of date",
  VERIFICATION_FAILED: "Upload check failed", RETRIEVAL_FAILED: "Company form unreachable",
};
const kindLabel = (k: string) => KIND_LABEL[k] ?? titleCase(k);

export default function ExceptionsPage() {
  return <Suspense fallback={<LoadingRows />}><Inner /></Suspense>;
}
