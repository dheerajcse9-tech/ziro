"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, RotateCw } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Dialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api, qs } from "@/lib/api";
import { toastError } from "@/lib/errors";
import { fmtDateTime, relTime, titleCase } from "@/lib/format";
import type { EmailItem, Paged } from "@/lib/types";

const CATEGORIES = ["DRIVE_INVITATION", "DATA_REQUEST", "REMINDER", "PROFILE_SUBMITTED", "NOT_SHORTLISTED", "EMPLOYER_ROSTER", "SELECTED", "NOT_SELECTED"];
const CATEGORY_LABEL: Record<string, string> = {
  DRIVE_INVITATION: "Consent form", DATA_REQUEST: "Personal form", REMINDER: "Reminder", PROFILE_SUBMITTED: "Shortlisted",
  NOT_SHORTLISTED: "Not shortlisted", EMPLOYER_ROSTER: "List to company", SELECTED: "Selected", NOT_SELECTED: "Not selected",
};

/** Renders plain-text email bodies with clickable links (links open the student response page). */
function Body({ text }: { text: string }) {
  const parts = text.split(/(https?:\/\/\S+)/g);
  return (
    <pre className="whitespace-pre-wrap rounded-md border border-line bg-subtle p-4 font-sans text-[13px] leading-relaxed text-ink">
      {parts.map((p, i) => /^https?:\/\//.test(p)
        ? <a key={i} href={p} target="_blank" rel="noreferrer" className="break-all text-accent underline">{p}</a>
        : <span key={i}>{p}</span>)}
    </pre>
  );
}

export default function CommunicationsPage() {
  const [category, setCategory] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["emails", category, status, page],
    queryFn: () => api.get<Paged<EmailItem> & { transport: string; sender: string | null }>(`/api/emails${qs({ category, status, page, page_size: 25 })}`),
    placeholderData: keepPreviousData, refetchInterval: 10_000,
  });
  const detail = useQuery({ queryKey: ["email", openId], queryFn: () => api.get<EmailItem>(`/api/emails/${openId}`), enabled: openId !== null });
  const qc = useQueryClient();
  const retry = useMutation({
    mutationFn: (id?: number) => api.post<{ retried: number; sent: number; failed: number; items: EmailItem[] }>(`/api/emails/retry-failed${qs({ email_id: id })}`),
    onSuccess: (r) => {
      if (r.retried === 0) toast.info("No failed emails to retry");
      else if (r.failed === 0) toast.success(`${r.sent} email(s) sent`);
      else toast.error(`${r.failed} of ${r.retried} email(s) still failed`, { description: r.items.find((m) => m.error)?.error ?? undefined });
      qc.invalidateQueries({ queryKey: ["emails"] }); qc.invalidateQueries({ queryKey: ["email"] });
    },
    onError: toastError,
  });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / q.data.page_size)) : 1;

  return (
    <>
      <PageHeader title="Emails sent" description="Every email sent to students and companies. The same email is never sent twice." />
      {q.data && (
        <div className="mb-4">
          {q.data.transport === "LOCAL_MAILBOX"
            ? <Banner tone="info" title="Gmail is not connected — emails stay in this app">Connect the placement Gmail in Settings to send real email. You can open any email below and click its link to act as the student.</Banner>
            : <Banner tone="ok" title={q.data.transport === "GMAIL_API" ? `Real emails are sent from ${q.data.sender}` : "Real emails are sent through the configured SMTP server"}>
                Students with demo addresses (…@students.example.edu) are not emailed: their messages stay here as “In app”.</Banner>}
        </div>
      )}
      <Card>
        <CardHeader title="Outbox" actions={<>
          <Button size="sm" loading={retry.isPending && retry.variables === undefined} onClick={() => retry.mutate(undefined)} icon={<RotateCw className="size-3.5" />}>Retry failed emails</Button>
          <Select aria-label="Category" value={category} onChange={(e) => { setCategory(e.target.value); setPage(1); }}>
            <option value="">All types</option>{CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c] ?? titleCase(c)}</option>)}
          </Select>
          <Select aria-label="Status" value={status} onChange={(e) => { setStatus(e.target.value); setPage(1); }}>
            <option value="">Any status</option><option value="SENT">Really sent</option><option value="DELIVERED_LOCAL">In app only (demo address)</option>
            <option value="FAILED">Failed</option><option value="SKIPPED">Skipped (no address)</option>
          </Select>
        </>} />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} /></div> : !q.data ? <LoadingRows /> : (
          <>
            <DataTable data={q.data.items} empty={<EmptyState icon={<Mail className="size-5" />} title="No emails yet" description="Invite students to a drive or approve a roster to see messages here." />}
              columns={[
                { accessorKey: "category", header: "Type", cell: ({ row }) => <Tag tone={row.original.category === "SELECTED" || row.original.category === "PROFILE_SUBMITTED" ? "ok" : row.original.category === "NOT_SELECTED" || row.original.category === "NOT_SHORTLISTED" ? "neutral" : row.original.category === "EMPLOYER_ROSTER" ? "accent" : row.original.category === "REMINDER" ? "warn" : "info"}>{CATEGORY_LABEL[row.original.category] ?? titleCase(row.original.category)}</Tag> },
                { accessorKey: "to_name", header: "To", cell: ({ row }) => <span><span className="font-medium">{row.original.to_name ?? "—"}</span><br /><span className="font-mono text-[11.5px] text-muted">{row.original.to_address ?? "no address"}</span></span> },
                { accessorKey: "subject", header: "Subject", cell: ({ row }) => <button className="text-left hover:underline" onClick={() => setOpenId(row.original.id)}>{row.original.subject}</button> },
                { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} tone={row.original.status === "FAILED" ? "bad" : row.original.status === "SKIPPED" ? "warn" : "ok"} label={row.original.status === "DELIVERED_LOCAL" ? "In app" : row.original.status === "SENT" && row.original.transport === "GMAIL_API" ? "Sent (Gmail)" : titleCase(row.original.status)} /> },
                { accessorKey: "created_at", header: "When", cell: ({ row }) => <span className="whitespace-nowrap text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
                { id: "open", header: "", cell: ({ row }) => <Button size="sm" onClick={() => setOpenId(row.original.id)}>Open</Button> },
              ]} />
            <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
              <span>{q.data.total} emails · page {page} of {pages}</span>
              <div className="flex gap-1"><Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button></div>
            </div>
          </>
        )}
      </Card>
      <Dialog open={openId !== null} onOpenChange={(o) => !o && setOpenId(null)} wide title={detail.data?.subject ?? "Email"}
        description={detail.data ? `To ${detail.data.to_name ?? ""} <${detail.data.to_address ?? "no address"}> · ${fmtDateTime(detail.data.created_at)} · via ${{ SMTP: "SMTP", GMAIL_API: "Gmail" }[detail.data.transport] ?? "local mailbox"}` : undefined}>
        {!detail.data ? <LoadingRows rows={4} /> : (
          <div className="space-y-3">
            {detail.data.error && <Banner tone="bad" title="Not delivered">{detail.data.error}</Banner>}
            {detail.data.status === "FAILED" && <Button size="sm" loading={retry.isPending} onClick={() => retry.mutate(detail.data!.id)} icon={<RotateCw className="size-3.5" />}>Retry this email</Button>}
            <Body text={detail.data.body_text ?? ""} />
          </div>
        )}
      </Dialog>
    </>
  );
}
