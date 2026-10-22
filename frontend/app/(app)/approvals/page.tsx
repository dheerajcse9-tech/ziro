"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Card, CardHeader, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { relTime } from "@/lib/format";
import type { Approval } from "@/lib/types";

function ApprovalsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const status = params.get("status") ?? "";
  const q = useQuery({ queryKey: ["approvals", status], queryFn: () => api.get<Approval[]>(`/api/approvals${status ? `?status=${status}` : ""}`), refetchInterval: 10_000 });
  return (
    <>
      <PageHeader title="Approvals"
        description="Student lists prepared by the placement officer's agents. The Approver checks each list and approves or rejects it. If anything changes after a list is prepared, it must be prepared again." />
      <Card>
        <CardHeader title="Approval queue" actions={
          <Select aria-label="Status" value={status} onChange={(e) => router.replace(`/approvals${e.target.value ? `?status=${e.target.value}` : ""}`)}>
            <option value="">All</option><option value="PENDING">Pending</option><option value="APPROVED">Approved (not yet sent)</option>
            <option value="CONSUMED">Sent</option><option value="REJECTED">Rejected</option><option value="INVALIDATED">Invalidated</option>
          </Select>} />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <LoadingRows /> : (
          <DataTable data={q.data} rowHref={(a) => `/approvals/${a.id}`}
            empty={<EmptyState title={status === "PENDING" ? "Nothing waiting for approval" : "No approvals"} description="A list appears here after the placement officer runs a drive." />}
            columns={[
              { accessorKey: "drive_title", header: "Drive", cell: ({ row }) => <span>{row.original.drive_title}{row.original.kind === "SUPPLEMENT" && <Tag tone="info" className="ml-1.5">Supplement</Tag>}</span> },
              { id: "counts", header: "Students in list", cell: ({ row }) => <span className="tabular">{row.original.included_count}{row.original.held_count > 0 && <span className="text-muted"> · {row.original.held_count} on hold</span>}</span> },
              { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} label={row.original.status === "CONSUMED" ? "Sent" : row.original.status === "INVALIDATED" ? "Out of date" : undefined} /> },
              { id: "decided", header: "Decision", cell: ({ row }) => row.original.decided_by ? <span className="text-[12px]">{row.original.decided_by.name} · {relTime(row.original.decided_at)}</span> : row.original.invalidated_reason ? <span className="text-[12px] text-warn">{row.original.invalidated_reason}</span> : <span className="text-faint">—</span> },
              { accessorKey: "created_at", header: "Prepared", cell: ({ row }) => <span className="text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
            ]} />
        )}
      </Card>
    </>
  );
}

export default function ApprovalsPage() {
  return <Suspense fallback={<LoadingRows />}><ApprovalsInner /></Suspense>;
}
