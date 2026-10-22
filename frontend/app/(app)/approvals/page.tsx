"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { DataTable } from "@/components/ui/data-table";
import { HashValue } from "@/components/ui/evidence";
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
        description="Every external submission needs an explicit decision by an approver. Approvals bind to an exact proposal hash (roster, criteria version, schedule version, destination) and are invalidated automatically if anything changes." />
      <Card>
        <CardHeader title="Approval queue" actions={
          <Select aria-label="Status" value={status} onChange={(e) => router.replace(`/approvals${e.target.value ? `?status=${e.target.value}` : ""}`)}>
            <option value="">All</option><option value="PENDING">Pending</option><option value="APPROVED">Approved (not yet executed)</option>
            <option value="CONSUMED">Executed</option><option value="REJECTED">Rejected</option><option value="INVALIDATED">Invalidated</option>
          </Select>} />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <LoadingRows /> : (
          <DataTable data={q.data} rowHref={(a) => `/approvals/${a.id}`}
            empty={<EmptyState title={status === "PENDING" ? "Nothing waiting for approval" : "No approvals"} description="The agent creates approval requests when it prepares a roster." />}
            columns={[
              { accessorKey: "id", header: "#", cell: ({ row }) => <span className="font-mono text-[12px]">#{row.original.id}</span> },
              { accessorKey: "drive_title", header: "Drive", cell: ({ row }) => <span>{row.original.drive_title}{row.original.kind === "SUPPLEMENT" && <Tag tone="info" className="ml-1.5">Supplement</Tag>}</span> },
              { id: "counts", header: "Upload / excluded / held", cell: ({ row }) => <span className="tabular">{row.original.included_count} / {row.original.excluded_count} / {row.original.held_count}</span> },
              { accessorKey: "proposal_hash", header: "Proposal hash", enableSorting: false, cell: ({ row }) => <HashValue value={row.original.proposal_hash} n={10} /> },
              { accessorKey: "requested_by", header: "Requested by", cell: ({ row }) => <span className="font-mono text-[11.5px] text-muted">{row.original.requested_by}</span> },
              { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} label={row.original.status === "CONSUMED" ? "Executed" : undefined} /> },
              { id: "decided", header: "Decision", cell: ({ row }) => row.original.decided_by ? <span className="text-[12px]">{row.original.decided_by.name} · {relTime(row.original.decided_at)}</span> : row.original.invalidated_reason ? <span className="text-[12px] text-warn">{row.original.invalidated_reason}</span> : <span className="text-faint">—</span> },
              { accessorKey: "created_at", header: "Requested", cell: ({ row }) => <span className="text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
            ]} />
        )}
      </Card>
    </>
  );
}

export default function ApprovalsPage() {
  return <Suspense fallback={<LoadingRows />}><ApprovalsInner /></Suspense>;
}
