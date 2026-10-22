"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { AlertCircle, Upload } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Button, ButtonLink, Card, Input, Select } from "@/components/ui/primitives";
import { EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api, qs } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import type { Drive, Paged, Student } from "@/lib/types";

type StudentsResponse = Paged<Student> & { branches: { code: string; label: string }[] };

const columns: ColumnDef<Student>[] = [
  { accessorKey: "roll_number", header: "Roll number", enableSorting: false,
    cell: ({ row }) => <Link href={`/students/${row.original.id}`} className="font-mono text-[12.5px] font-medium text-accent hover:underline">{row.original.roll_number}</Link> },
  { accessorKey: "full_name", header: "Name", enableSorting: false },
  { accessorKey: "branch", header: "Branch", enableSorting: false,
    cell: ({ row }) => row.original.branch ? <span title={row.original.branch_label ?? undefined}>{row.original.branch}</span>
      : <Tag tone="warn">{row.original.branch_raw ? `Unrecognised: ${row.original.branch_raw}` : "Missing"}</Tag> },
  { accessorKey: "cgpa", header: "CGPA", enableSorting: false,
    cell: ({ row }) => row.original.cgpa === null ? <Tag tone="warn">Missing</Tag> : <span className="tabular">{row.original.cgpa.toFixed(2)}</span> },
  { accessorKey: "active_backlogs", header: "Backlogs", enableSorting: false,
    cell: ({ row }) => row.original.active_backlogs === null ? <Tag tone="warn">Missing</Tag> : <span className="tabular">{row.original.active_backlogs}</span> },
  { id: "consent", header: "Employer-sharing consent", enableSorting: false,
    cell: ({ row }) => row.original.consent ? <StatusBadge status={row.original.consent.status} /> : <StatusBadge status="NONE" tone="warn" label="Not recorded" /> },
  { id: "selected", header: "Selected at", enableSorting: false,
    cell: ({ row }) => (row.original as Student & { selected_at?: string[] }).selected_at?.length
      ? <span className="flex flex-wrap gap-1">{(row.original as Student & { selected_at: string[] }).selected_at.map((e) => <Tag key={e} tone="ok">{e}</Tag>)}</span>
      : <span className="text-faint">—</span> },
  { id: "drive", header: "For selected drive", enableSorting: false,
    cell: ({ row }) => {
      const v = row.original.drive;
      if (!v) return <span className="text-faint">—</span>;
      return <span className="flex flex-wrap gap-1" title={v.reasons.join("; ")}>
        <StatusBadge status={v.roster_status === "INCLUDED" ? "INCLUDED" : v.decision} />
        <Tag tone={v.consent === "yes" ? "ok" : v.consent === "no" ? "neutral" : "warn"}>consent: {v.consent ?? "no answer"}</Tag>
      </span>;
    } },
  { id: "issues", header: "Data issues", enableSorting: false,
    cell: ({ row }) => row.original.data_issues.length ? <span className="inline-flex items-center gap-1 text-[12px] text-warn"><AlertCircle className="size-3.5" />{row.original.data_issues.length}</span> : <span className="text-faint">—</span> },
];

function StudentsInner() {
  const me = useMe();
  const router = useRouter();
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const branch = params.get("branch") ?? "";
  const consent = params.get("consent") ?? "";
  const issues = params.get("issues") ?? "";
  const sort = params.get("sort") ?? "roll_number";
  const page = Number(params.get("page") ?? 1);
  const f = {
    cgpa_min: params.get("cgpa_min") ?? "", cgpa_max: params.get("cgpa_max") ?? "", backlogs_max: params.get("backlogs_max") ?? "",
    year: params.get("year") ?? "", missing: params.get("missing") ?? "", drive_id: params.get("drive_id") ?? "",
    drive_status: params.get("drive_status") ?? "",
  };
  const [range, setRange] = useState({ cgpa_min: f.cgpa_min, cgpa_max: f.cgpa_max, backlogs_max: f.backlogs_max, year: f.year });
  const drives = useQuery({ queryKey: ["drives"], queryFn: () => api.get<Drive[]>("/api/drives") });

  useEffect(() => {
    setQ(params.get("q") ?? "");
    setRange({ cgpa_min: params.get("cgpa_min") ?? "", cgpa_max: params.get("cgpa_max") ?? "", backlogs_max: params.get("backlogs_max") ?? "", year: params.get("year") ?? "" });
  }, [params]);

  const setParam = (patch: Record<string, string | number | undefined>) => {
    const next = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(patch)) {
      if (v === undefined || v === "") next.delete(k); else next.set(k, String(v));
    }
    if (!("page" in patch)) next.delete("page");
    router.replace(`/students?${next.toString()}`);
  };

  const query = useQuery({
    queryKey: ["students", { q: params.get("q"), branch, consent, issues, sort, page, ...f }],
    queryFn: () => api.get<StudentsResponse>(`/api/students${qs({ q: params.get("q"), branch, consent, issues: issues || undefined, sort, page, page_size: 25, ...f })}`),
    placeholderData: keepPreviousData,
  });
  const data = query.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <>
      <PageHeader title="Student Registry"
        description="Synthetic roster. Students are identified by roll number — never by name. Consent is append-only and checked again before any upload."
        actions={can(me.data, "editData") ? <ButtonLink href="/students/import" variant="primary" icon={<Upload className="size-4" />}>Import roster</ButtonLink> : undefined} />
      <Card>
        <div className="flex flex-wrap items-end gap-2 border-b border-line p-3">
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setParam({ q }); }}>
            <Input aria-label="Search" placeholder="Roll number, name or email" value={q} onChange={(e) => setQ(e.target.value)} className="w-64" />
            <Button type="submit">Search</Button>
          </form>
          <Select aria-label="Branch" value={branch} onChange={(e) => setParam({ branch: e.target.value })}>
            <option value="">All branches</option>
            {data?.branches.map((b) => <option key={b.code} value={b.code}>{b.code} — {b.label}</option>)}
            <option value="UNKNOWN">Missing / unrecognised</option>
          </Select>
          <Select aria-label="Consent" value={consent} onChange={(e) => setParam({ consent: e.target.value })}>
            <option value="">Any consent</option>
            <option value="granted">Consent granted</option>
            <option value="missing">Consent missing / revoked</option>
          </Select>
          <Select aria-label="Data quality" value={issues} onChange={(e) => setParam({ issues: e.target.value })}>
            <option value="">All records</option>
            <option value="true">With data issues</option>
          </Select>
          <Select aria-label="Sort" value={sort} onChange={(e) => setParam({ sort: e.target.value })}>
            <option value="roll_number">Sort: roll number</option>
            <option value="full_name">Sort: name</option>
            <option value="-cgpa">Sort: CGPA (high → low)</option>
            <option value="cgpa">Sort: CGPA (low → high)</option>
            <option value="branch">Sort: branch</option>
          </Select>
          {(params.toString() !== "") && <Button variant="ghost" onClick={() => router.replace("/students")}>Clear filters</Button>}
        </div>
        <form className="flex flex-wrap items-end gap-2 border-b border-line bg-subtle/60 p-3" onSubmit={(e) => { e.preventDefault(); setParam(range); }}>
          <Input aria-label="CGPA from" placeholder="CGPA from" inputMode="decimal" className="w-28" value={range.cgpa_min} onChange={(e) => setRange({ ...range, cgpa_min: e.target.value })} />
          <Input aria-label="CGPA to" placeholder="CGPA to" inputMode="decimal" className="w-28" value={range.cgpa_max} onChange={(e) => setRange({ ...range, cgpa_max: e.target.value })} />
          <Input aria-label="Max backlogs" placeholder="Max backlogs" inputMode="numeric" className="w-32" value={range.backlogs_max} onChange={(e) => setRange({ ...range, backlogs_max: e.target.value })} />
          <Input aria-label="Batch" placeholder="Batch (e.g. 2026)" inputMode="numeric" className="w-36" value={range.year} onChange={(e) => setRange({ ...range, year: e.target.value })} />
          <Button type="submit">Apply</Button>
          <Select aria-label="Missing data" value={f.missing} onChange={(e) => setParam({ missing: e.target.value })}>
            <option value="">Any data</option><option value="any">Missing anything</option><option value="cgpa">Missing CGPA</option>
            <option value="active_backlogs">Missing backlogs</option><option value="branch">Missing branch</option><option value="graduation_year">Missing batch</option>
          </Select>
          <Select aria-label="Drive" value={f.drive_id} onChange={(e) => setParam({ drive_id: e.target.value, drive_status: e.target.value ? f.drive_status : "" })}>
            <option value="">For drive…</option>{drives.data?.map((d) => <option key={d.id} value={d.id}>{d.title}</option>)}
          </Select>
          {f.drive_id && <Select aria-label="Drive status" value={f.drive_status} onChange={(e) => setParam({ drive_status: e.target.value })}>
            <option value="">Everyone</option><option value="included">Would be on the roster</option><option value="eligible">Meets criteria</option>
            <option value="ineligible">Does not meet criteria</option><option value="unknown">Missing data for this drive</option>
            <option value="consent_yes">Said YES</option><option value="consent_no">Said NO</option><option value="consent_pending">No consent answer</option>
          </Select>}
        </form>
        {query.isError ? <div className="p-4"><ErrorState error={query.error} onRetry={() => query.refetch()} /></div>
          : !data ? <LoadingRows /> : (
            <>
              <DataTable data={data.items} columns={columns} rowHref={(s) => `/students/${s.id}`}
                empty={<EmptyState title="No students match these filters" />} />
              <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
                <span className="tabular">{data.total} students · page {data.page} of {pages}</span>
                <div className="flex gap-1">
                  <Button size="sm" disabled={page <= 1} onClick={() => setParam({ page: page - 1 })}>Previous</Button>
                  <Button size="sm" disabled={page >= pages} onClick={() => setParam({ page: page + 1 })}>Next</Button>
                </div>
              </div>
            </>
          )}
      </Card>
    </>
  );
}

export default function StudentsPage() {
  return <Suspense fallback={<LoadingRows />}><StudentsInner /></Suspense>;
}
