"use client";

import { useQuery } from "@tanstack/react-query";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Card, CardHeader } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { relTime } from "@/lib/format";
import type { Upload } from "@/lib/types";

export default function VerificationPage() {
  const q = useQuery({ queryKey: ["uploads"], queryFn: () => api.get<Upload[]>("/api/uploads"), refetchInterval: 10_000 });
  return (
    <>
      <PageHeader title="Sent to companies"
        description="Approved lists that were sent. After sending, the agents read the list back and check every student arrived." />
      <Card>
        <CardHeader title="Sent lists" />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <LoadingRows /> : (
          <DataTable data={q.data} rowHref={(u) => `/verification/${u.id}`}
            empty={<EmptyState title="No uploads yet" description="Lists appear here after the Approver approves them." />}
            columns={[
              { accessorKey: "drive_title", header: "Drive" },
              { accessorKey: "submitted_count", header: "Students" },
              { accessorKey: "status", header: "Sent", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
              { id: "ver", header: "Checked", cell: ({ row }) => row.original.latest_verification
                ? <span className="flex items-center gap-2"><StatusBadge status={row.original.latest_verification.status} /><span className="tabular text-[12px] text-muted">{row.original.latest_verification.observed_count ?? "—"}/{row.original.latest_verification.expected_count}</span></span>
                : <StatusBadge status="PENDING_VERIFICATION" label="Not verified" /> },
              { accessorKey: "created_at", header: "Started", cell: ({ row }) => <span className="text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
            ]} />
        )}
      </Card>
    </>
  );
}
