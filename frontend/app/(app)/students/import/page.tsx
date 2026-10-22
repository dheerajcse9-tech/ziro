"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, RefreshCw } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { toastError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import type { RosterImport } from "@/lib/types";

const FIELD_LABEL: Record<string, string> = {
  roll_number: "Roll number *", full_name: "Full name *", email: "Email", phone: "Phone", branch: "Branch",
  cgpa: "CGPA", active_backlogs: "Active backlogs", graduation_year: "Graduation year", consent: "Consent to share",
};

export default function ImportPage() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<RosterImport | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const history = useQuery({ queryKey: ["imports"], queryFn: () => api.get<RosterImport[]>("/api/roster-imports") });

  const previewMut = useMutation({
    mutationFn: (args: { file: File; mapping?: Record<string, string | null> }) => {
      const fd = new FormData();
      fd.append("file", args.file);
      if (args.mapping) fd.append("mapping", JSON.stringify(args.mapping));
      return api.post<RosterImport>("/api/roster-imports/preview", fd);
    },
    onSuccess: (r) => { setPreview(r); setMapping(r.column_mapping); qc.invalidateQueries({ queryKey: ["imports"] }); },
    onError: toastError,
  });
  const commit = useMutation({
    mutationFn: (id: number) => api.post<RosterImport>(`/api/roster-imports/${id}/commit`),
    onSuccess: (r) => {
      toast.success(`Import committed: ${r.created_count} created, ${r.updated_count} updated, ${r.unchanged_count} unchanged`);
      setPreview(null); setFile(null); if (fileRef.current) fileRef.current.value = "";
      qc.invalidateQueries();
    },
    onError: toastError,
  });
  const discard = useMutation({
    mutationFn: (id: number) => api.post<RosterImport>(`/api/roster-imports/${id}/discard`),
    onSuccess: () => { setPreview(null); qc.invalidateQueries({ queryKey: ["imports"] }); toast("Preview discarded"); },
    onError: toastError,
  });

  const rows = preview?.rows ?? [];
  const actionCounts = rows.reduce<Record<string, number>>((acc, r) => ({ ...acc, [r.action]: (acc[r.action] ?? 0) + 1 }), {});

  return (
    <>
      <PageHeader title="Import roster" crumbs={[{ label: "Students", href: "/students" }, { label: "Import" }]}
        description="CSV or XLSX. Rows are matched to existing students by roll number only. Nothing changes until you commit the validated preview."
        actions={<a href="/api/roster-imports/template.csv" className="inline-flex h-9 items-center gap-2 rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium hover:bg-subtle"><Download className="size-4" />CSV template</a>} />

      <Card className="mb-4">
        <CardHeader title="1 · Choose a file" />
        <div className="flex flex-wrap items-center gap-3 p-4">
          <input ref={fileRef} type="file" accept=".csv,.xlsx" aria-label="Roster file"
            onChange={(e) => { setFile(e.target.files?.[0] ?? null); setPreview(null); }}
            className="text-[13px] file:mr-3 file:rounded-md file:border file:border-line-strong file:bg-surface file:px-3 file:py-1.5 file:text-[13px] file:font-medium hover:file:bg-subtle" />
          <Button variant="primary" disabled={!file} loading={previewMut.isPending} icon={<FileUp className="size-4" />}
            onClick={() => file && previewMut.mutate({ file })}>Validate &amp; preview</Button>
        </div>
      </Card>

      {preview && (
        <>
          <Card className="mb-4">
            <CardHeader title="2 · Column mapping" description={`Detected columns in ${preview.filename}. Adjust and re-validate if a field was not matched.`}
              actions={<Button size="sm" icon={<RefreshCw className="size-3.5" />} loading={previewMut.isPending}
                onClick={() => file && previewMut.mutate({ file, mapping })}>Re-validate with mapping</Button>} />
            <div className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
              {(preview.available_fields ?? Object.keys(FIELD_LABEL)).map((f) => (
                <label key={f} className="flex items-center justify-between gap-2 text-[13px]">
                  <span className="text-ink-2">{FIELD_LABEL[f] ?? f}</span>
                  <Select value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value || null })} className="w-44">
                    <option value="">— not mapped —</option>
                    {preview.headers?.map((h) => <option key={h} value={h}>{h}</option>)}
                  </Select>
                </label>
              ))}
            </div>
          </Card>

          <Card className="mb-4">
            <CardHeader title="3 · Validation preview"
              description={`${preview.total_rows} rows`}
              actions={<>
                {preview.issue_count > 0 && <a className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-surface px-2.5 text-[13px] font-medium hover:bg-subtle" href={`/api/roster-imports/${preview.id}/errors.csv`}><Download className="size-3.5" />Issues CSV</a>}
                <Button size="sm" onClick={() => discard.mutate(preview.id)} loading={discard.isPending}>Discard</Button>
                <Button size="sm" variant="primary" disabled={preview.valid_rows === 0} loading={commit.isPending} onClick={() => commit.mutate(preview.id)}>
                  Commit {preview.valid_rows} valid row{preview.valid_rows === 1 ? "" : "s"}
                </Button>
              </>} />
            <div className="flex flex-wrap gap-2 border-b border-line px-4 py-2">
              {Object.entries(actionCounts).map(([a, n]) => <StatusBadge key={a} status={a} tone={a === "REJECT" ? "bad" : a === "CREATE" ? "ok" : a === "UPDATE" ? "info" : "neutral"} label={`${a.toLowerCase()} · ${n}`} />)}
            </div>
            {preview.error_rows > 0 && <div className="p-3"><Banner tone="bad" title={`${preview.error_rows} row(s) will be rejected`}>Rejected rows are not imported. Fix them in the source file and re-import; existing records are matched by roll number, so re-importing is safe.</Banner></div>}
            <DataTable dense pageSize={25} data={rows} columns={[
              { accessorKey: "line", header: "Line", cell: ({ row }) => <span className="tabular text-muted">{row.original.line}</span> },
              { accessorKey: "action", header: "Action", cell: ({ row }) => <StatusBadge status={row.original.action} tone={row.original.action === "REJECT" ? "bad" : row.original.action === "CREATE" ? "ok" : row.original.action === "UPDATE" ? "info" : "neutral"} /> },
              { id: "roll", header: "Roll", cell: ({ row }) => <span className="font-mono text-[12px]">{row.original.data.roll_number ?? "—"}</span> },
              { id: "name", header: "Name", cell: ({ row }) => row.original.data.full_name ?? "—" },
              { id: "branch", header: "Branch", cell: ({ row }) => row.original.data.branch ?? <span className="text-faint">{row.original.data.branch_raw ?? "—"}</span> },
              { id: "cgpa", header: "CGPA", cell: ({ row }) => row.original.data.cgpa ?? "—" },
              { id: "bl", header: "Backlogs", cell: ({ row }) => row.original.data.active_backlogs ?? "—" },
              { id: "consent", header: "Consent", cell: ({ row }) => row.original.data.consent === true ? <Tag tone="ok">Yes</Tag> : row.original.data.consent === false ? <Tag tone="bad">No</Tag> : <Tag tone="warn">Not stated</Tag> },
              { id: "issues", header: "Issues", cell: ({ row }) => <ul className="space-y-0.5 text-[12px]">{row.original.issues.map((i, n) => <li key={n} className={i.severity === "ERROR" ? "text-bad" : "text-warn"}>{i.message}</li>)}</ul> },
            ]} />
          </Card>
        </>
      )}

      <Card>
        <CardHeader title="Import history" />
        {!history.data ? <LoadingRows rows={3} /> : history.data.length === 0 ? <EmptyState title="No imports yet" /> : (
          <DataTable data={history.data} columns={[
            { accessorKey: "id", header: "#" },
            { accessorKey: "filename", header: "File" },
            { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} tone={row.original.status === "COMMITTED" ? "ok" : "neutral"} /> },
            { id: "rows", header: "Rows", cell: ({ row }) => `${row.original.valid_rows} valid / ${row.original.error_rows} rejected` },
            { id: "result", header: "Result", cell: ({ row }) => row.original.status === "COMMITTED" ? `${row.original.created_count} created · ${row.original.updated_count} updated · ${row.original.unchanged_count} unchanged` : "—" },
            { accessorKey: "created_at", header: "Previewed", cell: ({ row }) => fmtDateTime(row.original.created_at) },
            { id: "dl", header: "", cell: ({ row }) => row.original.issue_count ? <a className="text-[12px] text-accent hover:underline" href={`/api/roster-imports/${row.original.id}/errors.csv`}>Issues CSV</a> : null },
          ]} />
        )}
      </Card>
    </>
  );
}
