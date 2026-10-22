"use client";

import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { NewDriveDialog } from "@/components/drives/new-drive-dialog";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { fmtSlot } from "@/lib/format";
import type { Drive, Employer } from "@/lib/types";

const columns: ColumnDef<Drive>[] = [
  { accessorKey: "title", header: "Drive", cell: ({ row }) => (
    <div><Link href={`/drives/${row.original.id}`} className="font-medium text-ink hover:underline">{row.original.title}</Link>
      <div className="text-[12px] text-muted">{row.original.employer.name}{row.original.location ? ` · ${row.original.location}` : ""}</div></div>) },
  { id: "slot", header: "Date & time", accessorFn: (d) => d.starts_at, cell: ({ row }) => <span className="whitespace-nowrap text-[12.5px]">{fmtSlot(row.original.starts_at, row.original.ends_at, row.original.timezone)}</span> },
  { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
  { id: "criteria", header: "Criteria", enableSorting: false, cell: ({ row }) => {
    const c = row.original.criteria;
    if (!c) return <Tag tone="bad">Missing</Tag>;
    return <span className="text-[12px] text-ink-2">{c.min_cgpa !== null ? `CGPA ≥ ${c.min_cgpa}` : "No CGPA min"} · {c.backlog_policy === "NO_ACTIVE_BACKLOGS" ? "0 backlogs" : c.backlog_policy === "MAX_ALLOWED" ? `≤ ${c.max_active_backlogs} backlogs` : "backlogs n/a"} · {c.allowed_branches.length ? c.allowed_branches.join("/") : "all branches"}</span>;
  } },
  { id: "eval", header: "Students", enableSorting: false, cell: ({ row }) => {
    const e = row.original.latest_evaluation;
    return e ? <span className="tabular text-[12px]">{e.included} in · {e.held} held · {e.excluded} out</span> : <span className="text-[12px] text-faint">Not evaluated</span>;
  } },
];

export default function DrivesPage() {
  const me = useMe();
  const [status, setStatus] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const drives = useQuery({ queryKey: ["drives", status], queryFn: () => api.get<Drive[]>(`/api/drives${status ? `?status=${status}` : ""}`) });
  const employers = useQuery({ queryKey: ["employers"], queryFn: () => api.get<Employer[]>("/api/employers") });
  const editable = can(me.data, "manageDrives");

  return (
    <>
      <PageHeader title="Drives" description="Every placement drive. Most are created by the agents from company emails; use New drive for one a company told you about another way."
        actions={editable ? <Button variant="primary" onClick={() => setCreateOpen(true)} icon={<Plus className="size-4" />}>New drive</Button> : undefined} />
      <Card className="mb-4">
        <CardHeader title="Drives" actions={
          <Select aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option><option value="OPEN">Open</option><option value="DRAFT">Draft</option>
            <option value="ROSTER_SUBMITTED">Roster submitted</option><option value="CLOSED">Closed</option><option value="CANCELLED">Cancelled</option>
          </Select>} />
        {drives.isError ? <div className="p-4"><ErrorState error={drives.error} onRetry={() => drives.refetch()} /></div>
          : !drives.data ? <LoadingRows /> : <DataTable data={drives.data} columns={columns} rowHref={(d) => `/drives/${d.id}`}
            empty={<EmptyState title="No drives yet" description="Drives appear when a company emails the placement mailbox, or use New drive." />} />}
      </Card>
      <Card>
        <CardHeader title="Companies" />
        {!employers.data ? <LoadingRows rows={3} /> : (
          <DataTable data={employers.data} columns={[
            { accessorKey: "name", header: "Company" }, { accessorKey: "contact_email", header: "Email" }, { accessorKey: "drive_count", header: "Drives" },
          ]} empty={<EmptyState title="No companies yet" />} />
        )}
      </Card>
      <NewDriveDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  );
}
