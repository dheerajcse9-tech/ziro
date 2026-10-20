"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ExternalLink, FileCheck2, RefreshCw, RotateCcw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { toast } from "sonner";
import { HashValue, JsonView, RollList } from "@/components/ui/evidence";
import { DefinitionList, PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { MockBadge, StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import type { UploadDetail, Verification } from "@/lib/types";

export default function UploadDetailPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["upload", id], queryFn: () => api.get<UploadDetail>(`/api/uploads/${id}`) });
  const receipt = useQuery({ queryKey: ["receipt", id], queryFn: () => api.get<any>(`/api/uploads/${id}/receipt`), enabled: !!q.data && q.data.status === "SUBMITTED" });
  const verify = useMutation({
    mutationFn: () => api.post<Verification>(`/api/uploads/${id}/verify`),
    onSuccess: (v) => { (v.status === "VERIFIED" ? toast.success : toast.error)(`Verification: ${v.status.replace(/_/g, " ").toLowerCase()}`, { description: v.detail ?? undefined }); qc.invalidateQueries(); },
    onError: toastError,
  });
  const retry = useMutation({
    mutationFn: () => api.post(`/api/uploads/${id}/retry`),
    onSuccess: () => { toast.success("Reconciliation complete — see attempt log"); qc.invalidateQueries(); },
    onError: toastError,
  });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!q.data) return <LoadingRows />;
  const u = q.data;
  const v = u.latest_verification;
  const officer = can(me.data, "runAgent");

  return (
    <>
      <PageHeader title={`Upload #${u.id} · ${u.drive_title}`} crumbs={[{ label: "Verification Center", href: "/verification" }, { label: `Upload #${u.id}` }]}
        meta={<><StatusBadge status={u.status} />{v && <StatusBadge status={v.status} />}{u.destination.is_local_mock && <MockBadge />}</>}
        actions={officer ? <>
          {u.status === "SUBMITTED" && <Button onClick={() => verify.mutate()} loading={verify.isPending} icon={<RefreshCw className="size-4" />}>Re-read destination &amp; verify</Button>}
          {(u.status === "UNCERTAIN" || u.status === "FAILED") && <Button variant="primary" onClick={() => retry.mutate()} loading={retry.isPending} icon={<RotateCcw className="size-4" />}>Reconcile, then retry if absent</Button>}
        </> : undefined} />

      {u.status === "UNCERTAIN" && <div className="mb-4"><Banner title="Outcome uncertain">The destination did not confirm the submission. Reconciliation looks up operation key <span className="font-mono">{u.operation_key.slice(0, 12)}…</span> at the destination before any resubmission, so a retry can never create a duplicate.</Banner></div>}
      {v?.status === "FAILED_VERIFICATION" && <div className="mb-4"><Banner tone="bad" title="Verification failed — destination does not match the approved roster">{v.detail}</Banner></div>}
      {v?.status === "PENDING_VERIFICATION" && <div className="mb-4"><Banner title="Destination could not be read back">{v.detail}. The run is not marked verified until a read-back succeeds.</Banner></div>}
      {v?.status === "VERIFIED" && <div className="mb-4"><Banner tone="ok" title={`Verified: ${v.observed_count} of ${v.expected_count} approved IDs observed, no missing, unexpected or duplicate records`} /></div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4 xl:col-span-2">
          <Card>
            <CardHeader title="Independent read-back comparison" description={v ? `${v.method} · ${fmtDateTime(v.created_at)}` : "Not verified yet"} />
            {!v ? <EmptyState title="No verification yet" /> : (
              <div className="grid gap-4 p-4 md:grid-cols-2">
                <div><p className="mb-1 text-[12px] font-semibold text-muted">Approved ({u.submitted_roll_numbers.length})</p><RollList rolls={u.submitted_roll_numbers} /></div>
                <div><p className="mb-1 text-[12px] font-semibold text-muted">Observed at destination ({v.observed_count ?? "unreadable"})</p>
                  <RollList rolls={v.observed_snapshot?.observed_roll_numbers ?? []} tone={v.status === "VERIFIED" ? "ok" : "neutral"} empty="Could not read" /></div>
                <div><p className="mb-1 text-[12px] font-semibold text-muted">Missing at destination</p><RollList rolls={v.missing_roll_numbers} tone="bad" /></div>
                <div><p className="mb-1 text-[12px] font-semibold text-muted">Unexpected at destination</p><RollList rolls={v.unexpected_roll_numbers} tone="bad" /></div>
                <div><p className="mb-1 text-[12px] font-semibold text-muted">Duplicated at destination</p><RollList rolls={v.duplicate_roll_numbers} tone="bad" /></div>
                <div><p className="mb-1 text-[12px] font-semibold text-muted">Receipt hash</p><HashValue value={v.receipt_hash} /></div>
              </div>
            )}
          </Card>
          <Card>
            <CardHeader title="Attempt log" description="Recorded before and after every external call." />
            <table className="data-table w-full"><thead><tr><th>Time</th><th>Step</th><th>Details</th></tr></thead>
              <tbody>{u.attempt_log.map((l, i) => {
                const { at, step, ...rest } = l;
                return <tr key={i}><td className="whitespace-nowrap font-mono text-[11.5px]">{fmtDateTime(at, undefined, { year: undefined, second: "2-digit" })}</td>
                  <td><Tag tone={step === "receipt" || step === "reconciled" ? "ok" : step === "uncertain" || step === "rejected" ? "warn" : "neutral"}>{step}</Tag></td>
                  <td className="font-mono text-[11.5px] text-muted">{Object.entries(rest).map(([k, val]) => `${k}=${typeof val === "object" ? JSON.stringify(val) : String(val)}`).join("  ")}</td></tr>;
              })}</tbody></table>
          </Card>
          {u.verifications.length > 1 && (
            <Card>
              <CardHeader title="Verification history" />
              <table className="data-table w-full"><thead><tr><th>#</th><th>When</th><th>Status</th><th>Observed / expected</th><th>Detail</th></tr></thead>
                <tbody>{u.verifications.map((x) => <tr key={x.id}><td>{x.id}</td><td className="whitespace-nowrap">{fmtDateTime(x.created_at)}</td><td><StatusBadge status={x.status} /></td><td className="tabular">{x.observed_count ?? "—"} / {x.expected_count}</td><td className="text-[12px] text-muted">{x.detail}</td></tr>)}</tbody></table>
            </Card>
          )}
        </div>
        <div className="space-y-4">
          <Card>
            <CardHeader title="Operation" />
            <div className="p-4"><DefinitionList cols={1} items={[
              { label: "Operation key (idempotency)", value: <HashValue value={u.operation_key} /> },
              { label: "Destination", value: <span>{u.destination.name}<br /><a className="inline-flex items-center gap-1 text-[12px] text-accent hover:underline" target="_blank" rel="noreferrer" href={`/mock-portal/forms/${u.destination.form_ref}/roster`}>View destination roster <ExternalLink className="size-3" /></a></span> },
              { label: "Destination receipt", value: u.destination_receipt?.receipt_no ? <span className="font-mono">{u.destination_receipt.receipt_no}{u.destination_receipt.reconciled ? " (reconciled)" : ""}</span> : "—" },
              { label: "Attempts", value: u.attempts },
              { label: "Approval", value: <Link className="text-accent hover:underline" href={`/approvals/${u.approval_id}`}>#{u.approval_id}</Link> },
              { label: "Executed by", value: <span className="font-mono text-[12px]">{u.executed_by}</span> },
              { label: "Completed", value: fmtDateTime(u.completed_at) },
              ...(u.error ? [{ label: "Error", value: <span className="text-bad">{u.error}</span> }] : []),
            ]} /></div>
          </Card>
          <Card>
            <CardHeader title="Attendance sheets" description="Generated only from a VERIFIED upload; consent re-checked at generation." />
            {u.attendance_sheets.length === 0 ? <EmptyState icon={<FileCheck2 className="size-5" />} title="No sheet yet" description={v?.status === "VERIFIED" ? "Use Re-read & verify to generate." : "Available after successful verification."} /> : (
              <ul className="divide-y divide-line">{u.attendance_sheets.map((s) => (
                <li key={s.id} className="space-y-1 px-4 py-3 text-[12.5px]">
                  <p className="font-medium">{s.file_name}</p>
                  <p className="text-muted">{s.row_count} rows · {fmtDateTime(s.created_at)}{s.excluded_at_generation.length ? ` · ${s.excluded_at_generation.length} excluded (consent)` : ""}</p>
                  <HashValue value={s.sha256} />
                  {s.purged ? <Tag>Content purged (retention)</Tag> : can(me.data, "download") && (
                    <div className="flex gap-3 pt-1">
                      <a className="inline-flex items-center gap-1 text-accent hover:underline" href={`/api/attendance/${s.id}/download?format=csv`}><Download className="size-3.5" />CSV</a>
                      <a className="inline-flex items-center gap-1 text-accent hover:underline" href={`/api/attendance/${s.id}/download?format=xlsx`}><Download className="size-3.5" />XLSX</a>
                    </div>)}
                </li>))}</ul>
            )}
          </Card>
          {receipt.data && (
            <Card>
              <CardHeader title="Upload receipt" description="Eligible/excluded counts, reasons, approval and verification." actions={
                <a className="text-[12px] text-accent hover:underline" download={`receipt_upload_${u.id}.json`}
                  href={`data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(receipt.data, null, 2))}`}>Download JSON</a>} />
              <div className="space-y-2 p-4 text-[12.5px]">
                <p>{receipt.data.counts.eligible_uploaded} uploaded · {receipt.data.counts.excluded} excluded · {receipt.data.counts.held} held</p>
                <ul className="space-y-0.5 text-muted">{Object.entries(receipt.data.exclusion_reasons as Record<string, number>).slice(0, 8).map(([r, n]) => <li key={r}>{n} × {r}</li>)}</ul>
                <JsonView value={receipt.data} label="Full receipt" />
              </div>
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
