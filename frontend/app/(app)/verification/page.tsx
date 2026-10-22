"use client";

import { useQuery } from "@tanstack/react-query";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Card, CardHeader } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { MockBadge, StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { relTime } from "@/lib/format";
import type { Upload } from "@/lib/types";

export default function VerificationPage() {
  const q = useQuery({ queryKey: ["uploads"], queryFn: () => api.get<Upload[]>("/api/uploads"), refetchInterval: 10_000 });
  return (
    <>
      <PageHeader title="Sent to companies"
        description="A submission response is never treated as proof. After every upload the destination is re-read and the exact set of roll numbers is compared with the approved set." />
      <Card>
        <CardHeader title="Upload operations" />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div> : !q.data ? <LoadingRows /> : (
          <DataTable data={q.data} rowHref={(u) => `/verification/${u.id}`}
            empty={<EmptyState title="No uploads yet" description="Uploads appear after an approver approves a roster proposal." />}
            columns={[
              { accessorKey: "id", header: "#", cell: ({ row }) => <span className="font-mono text-[12px]">#{row.original.id}</span> },
              { accessorKey: "drive_title", header: "Drive" },
              { id: "dest", header: "Destination", cell: ({ row }) => <span className="flex items-center gap-1.5"><span className="font-mono text-[12px]">{row.original.destination.form_ref}</span>{row.original.destination.is_local_mock && <MockBadge />}</span> },
              { accessorKey: "submitted_count", header: "Approved IDs" },
              { accessorKey: "status", header: "Submission", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
              { id: "attempts", header: "Attempts", accessorFn: (u) => u.attempts },
              { id: "ver", header: "Verification", cell: ({ row }) => row.original.latest_verification
                ? <span className="flex items-center gap-2"><StatusBadge status={row.original.latest_verification.status} /><span className="tabular text-[12px] text-muted">{row.original.latest_verification.observed_count ?? "—"}/{row.original.latest_verification.expected_count}</span></span>
                : <StatusBadge status="PENDING_VERIFICATION" label="Not verified" /> },
              { accessorKey: "created_at", header: "Started", cell: ({ row }) => <span className="text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
            ]} />
        )}
      </Card>
    </>
  );
}
