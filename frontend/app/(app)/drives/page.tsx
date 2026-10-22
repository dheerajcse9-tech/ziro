"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Building2, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { CriteriaFields, criteriaSchema, toCriteriaPayload } from "@/components/drives/criteria-fields";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Dialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtSlot } from "@/lib/format";
import type { Destination, Drive, Employer } from "@/lib/types";

const driveSchema = z.object({
  employer_id: z.string().min(1, "Choose an employer"),
  title: z.string().trim().min(3, "Title is required"),
  role_title: z.string().optional(),
  location: z.string().optional(),
  date: z.string().min(1, "Date is required"),
  start: z.string().min(1, "Start time is required"),
  end: z.string().min(1, "End time is required"),
  timezone: z.string().min(1),
  destination_id: z.string(),
  criteria: criteriaSchema,
}).refine((v) => v.end > v.start, { path: ["end"], message: "End must be after start" });
type DriveValues = z.infer<typeof driveSchema>;

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
  const router = useRouter();
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [empOpen, setEmpOpen] = useState(false);
  const [empName, setEmpName] = useState("");
  const [empIndustry, setEmpIndustry] = useState("");
  const drives = useQuery({ queryKey: ["drives", status], queryFn: () => api.get<Drive[]>(`/api/drives${status ? `?status=${status}` : ""}`) });
  const employers = useQuery({ queryKey: ["employers"], queryFn: () => api.get<Employer[]>("/api/employers") });
  const destinations = useQuery({ queryKey: ["destinations"], queryFn: () => api.get<Destination[]>("/api/destinations") });

  const form = useForm<DriveValues>({
    resolver: zodResolver(driveSchema),
    defaultValues: { timezone: "Asia/Kolkata", destination_id: "", employer_id: "", title: "", date: "", start: "10:00", end: "13:00",
      criteria: { min_cgpa: "7.0", backlog_policy: "NO_ACTIVE_BACKLOGS", max_active_backlogs: "", allowed_branches: [], source_text: "", required_consent_scope: "SHARE_WITH_EMPLOYERS" } },
  });

  const create = useMutation({
    mutationFn: (v: DriveValues) => api.post<Drive>("/api/drives", {
      employer_id: Number(v.employer_id), title: v.title, role_title: v.role_title || null, location: v.location || null,
      starts_at: `${v.date}T${v.start}:00`, ends_at: `${v.date}T${v.end}:00`, timezone: v.timezone,
      destination_id: v.destination_id ? Number(v.destination_id) : null, criteria: toCriteriaPayload(v.criteria),
    }),
    onSuccess: (d) => { toast.success("Drive created with criteria v1"); setCreateOpen(false); qc.invalidateQueries(); router.push(`/drives/${d.id}`); },
    onError: toastError,
  });
  const createEmployer = useMutation({
    mutationFn: () => api.post<Employer>("/api/employers", { name: empName, industry: empIndustry || null }),
    onSuccess: (e) => { toast.success(`Employer '${e.name}' added`); setEmpOpen(false); setEmpName(""); setEmpIndustry(""); qc.invalidateQueries({ queryKey: ["employers"] }); },
    onError: toastError,
  });

  const editable = can(me.data, "manageDrives");
  const errs = form.formState.errors;

  return (
    <>
      <PageHeader title="Drives" description="Every placement drive, created from company emails."
        actions={editable ? <>
          <Button onClick={() => setEmpOpen(true)} icon={<Building2 className="size-4" />}>Add employer</Button>
          <Button variant="primary" onClick={() => setCreateOpen(true)} icon={<Plus className="size-4" />}>New drive</Button>
        </> : undefined} />
      <Card className="mb-4">
        <CardHeader title="Drives" actions={
          <Select aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option><option value="OPEN">Open</option><option value="DRAFT">Draft</option>
            <option value="ROSTER_SUBMITTED">Roster submitted</option><option value="CLOSED">Closed</option><option value="CANCELLED">Cancelled</option>
          </Select>} />
        {drives.isError ? <div className="p-4"><ErrorState error={drives.error} onRetry={() => drives.refetch()} /></div>
          : !drives.data ? <LoadingRows /> : <DataTable data={drives.data} columns={columns} rowHref={(d) => `/drives/${d.id}`}
            empty={<EmptyState title="No drives" description="Create a drive to start preparing rosters." />} />}
      </Card>
      <Card>
        <CardHeader title="Employers" />
        {!employers.data ? <LoadingRows rows={3} /> : (
          <DataTable data={employers.data} columns={[
            { accessorKey: "name", header: "Employer" }, { accessorKey: "industry", header: "Industry" },
            { accessorKey: "contact_email", header: "Campus contact" }, { accessorKey: "drive_count", header: "Drives" },
          ]} empty={<EmptyState title="No employers" />} />
        )}
      </Card>

      <Dialog open={createOpen} onOpenChange={setCreateOpen} wide title="New drive"
        description="Times are entered as local wall-clock time in the selected timezone and stored as UTC."
        footer={<><Button onClick={() => setCreateOpen(false)}>Cancel</Button><Button variant="primary" loading={create.isPending} onClick={form.handleSubmit((v) => create.mutate(v))}>Create drive</Button></>}>
        <form className="space-y-4" onSubmit={form.handleSubmit((v) => create.mutate(v))}>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Employer" htmlFor="employer_id" error={errs.employer_id?.message}>
              <Select id="employer_id" className="w-full" {...form.register("employer_id")}><option value="">Select…</option>{employers.data?.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}</Select>
            </Field>
            <Field label="Drive title" htmlFor="title" error={errs.title?.message}><Input id="title" {...form.register("title")} /></Field>
            <Field label="Role" htmlFor="role_title"><Input id="role_title" {...form.register("role_title")} /></Field>
            <Field label="Venue" htmlFor="location"><Input id="location" {...form.register("location")} /></Field>
            <Field label="Date" htmlFor="date" error={errs.date?.message}><Input id="date" type="date" {...form.register("date")} /></Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="Start" htmlFor="start" error={errs.start?.message}><Input id="start" type="time" {...form.register("start")} /></Field>
              <Field label="End" htmlFor="end" error={errs.end?.message}><Input id="end" type="time" {...form.register("end")} /></Field>
            </div>
            <Field label="Timezone" htmlFor="timezone">
              <Select id="timezone" className="w-full" {...form.register("timezone")}>
                {["Asia/Kolkata", "UTC", "Asia/Singapore", "Asia/Dubai", "Europe/London", "America/New_York"].map((t) => <option key={t}>{t}</option>)}
              </Select>
            </Field>
            <Field label="Upload destination" htmlFor="destination_id" hint="Only allowlisted destinations can be configured.">
              <Select id="destination_id" className="w-full" {...form.register("destination_id")}>
                <option value="">None yet</option>{destinations.data?.map((d) => <option key={d.id} value={d.id}>{d.name} ({d.form_ref})</option>)}
              </Select>
            </Field>
          </div>
          <div className="rounded-md border border-line p-3">
            <p className="mb-3 text-[13px] font-semibold text-ink">Eligibility criteria (v1)</p>
            <CriteriaFields form={form} prefix="criteria" />
          </div>
        </form>
      </Dialog>

      <Dialog open={empOpen} onOpenChange={setEmpOpen} title="Add employer"
        footer={<><Button onClick={() => setEmpOpen(false)}>Cancel</Button><Button variant="primary" disabled={empName.trim().length < 2} loading={createEmployer.isPending} onClick={() => createEmployer.mutate()}>Add employer</Button></>}>
        <div className="space-y-3">
          <Field label="Name" htmlFor="emp_name"><Input id="emp_name" value={empName} onChange={(e) => setEmpName(e.target.value)} /></Field>
          <Field label="Industry" htmlFor="emp_ind"><Input id="emp_ind" value={empIndustry} onChange={(e) => setEmpIndustry(e.target.value)} /></Field>
        </div>
      </Dialog>
    </>
  );
}
