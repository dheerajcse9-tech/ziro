"use client";

import { AgentFlowCard } from "@/components/agents/agent-flow";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ClipboardPaste, Inbox, MailCheck, MailOpen, RefreshCw, ShieldAlert, Sparkles } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { DataTable } from "@/components/ui/data-table";
import { DefinitionList, PageHeader } from "@/components/ui/layout";
import { Dialog } from "@/components/ui/overlay";
import { Button, ButtonLink, Card, CardHeader, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api, qs } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn, fmtDateTime, relTime } from "@/lib/format";
import type { Health, InboundEmail, InboxResponse, IntakeIssue } from "@/lib/types";

const STATUS_LABEL: Record<InboundEmail["status"], string> = {
  NEW: "New", PROCESSING: "Agents working", NEEDS_REVIEW: "Needs you", DRIVE_CREATED: "Drive created",
  PUBLISHED: "Consent forms sent", IGNORED: "Ignored",
};
const STATUS_TONE: Record<InboundEmail["status"], "info" | "warn" | "ok" | "neutral" | "accent"> = {
  NEW: "info", PROCESSING: "accent", NEEDS_REVIEW: "warn", DRIVE_CREATED: "ok", PUBLISHED: "ok", IGNORED: "neutral",
};
const FILTERS = [
  { value: "", label: "All" }, { value: "NEEDS_REVIEW", label: "Needs you" }, { value: "NEW", label: "New" },
  { value: "PUBLISHED", label: "Consent forms sent" }, { value: "IGNORED", label: "Ignored" },
];

function IssueList({ issues }: { issues: IntakeIssue[] }) {
  if (!issues.length) return <p className="text-[13px] text-muted">No problems found.</p>;
  const tone = { ERROR: "bad", REVIEW: "warn", INFO: "info" } as const;
  const label = { ERROR: "Must fix", REVIEW: "Confirm", INFO: "Note" } as const;
  return (
    <ul className="space-y-1.5">
      {issues.map((i, n) => (
        <li key={n} className="flex items-start gap-2 text-[13px]">
          <Tag tone={tone[i.level]} className="mt-0.5 shrink-0">{label[i.level]}</Tag>
          <span className="text-ink-2">{i.message}</span>
        </li>
      ))}
    </ul>
  );
}

const EDIT_FIELDS: { key: string; label: string; type?: string; hint?: string }[] = [
  { key: "company", label: "Company" },
  { key: "role", label: "Role" },
  { key: "drive_date", label: "Drive date", type: "date" },
  { key: "start_time", label: "Start time", type: "time" },
  { key: "end_time", label: "End time", type: "time" },
  { key: "venue", label: "Venue" },
  { key: "registration_deadline", label: "Students must register by", type: "datetime-local" },
  { key: "roster_due", label: "Company wants the list by", type: "datetime-local" },
  { key: "min_cgpa", label: "Minimum CGPA", hint: "Blank = no minimum" },
  { key: "allowed_branches", label: "Branches", hint: "Comma separated codes, e.g. CSE, IT, ECE. Blank = all" },
  { key: "ctc", label: "CTC / stipend" },
];

function EmailDetail({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const me = useMe();
  const canAct = can(me.data, "manageDrives");
  const q = useQuery({ queryKey: ["inbox-email", id], queryFn: () => api.get<InboundEmail>(`/api/inbox/${id}`), refetchInterval: 5000 });
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<Record<string, string>>({});
  const [policy, setPolicy] = useState("");
  const refresh = () => { qc.invalidateQueries({ queryKey: ["inbox"] }); qc.invalidateQueries({ queryKey: ["inbox-email", id] }); };

  useEffect(() => {
    const d = q.data?.details;
    if (!d || editing) return;
    setForm({
      company: d.company ?? "", role: d.role ?? "", drive_date: d.drive_date ?? "", start_time: d.start_time ?? "",
      end_time: d.end_time ?? "", venue: d.venue ?? "", registration_deadline: d.registration_deadline ?? "",
      roster_due: d.roster_due ?? "", min_cgpa: d.min_cgpa?.toString() ?? "", allowed_branches: (d.allowed_branches ?? []).join(", "),
      ctc: d.ctc ?? "", max_active_backlogs: d.max_active_backlogs?.toString() ?? "",
    });
    setPolicy(d.backlog_policy ?? "");
  }, [q.data, editing]);

  const answer = useMutation({
    mutationFn: (value: string) => api.post(`/api/agent/runs/${q.data!.run!.id}/answer`, { value }),
    onSuccess: () => { toast.success("Sent to the agents"); refresh(); },
    onError: toastError,
  });
  const process = useMutation({
    mutationFn: () => api.post(`/api/inbox/${id}/process`),
    onSuccess: () => { toast.success("Agents are processing the email"); refresh(); },
    onError: toastError,
  });
  const ignore = useMutation({
    mutationFn: () => api.post(`/api/inbox/${id}/ignore`, { note: "Ignored in the Company Inbox" }),
    onSuccess: () => { toast.success("Email ignored"); refresh(); },
    onError: toastError,
  });
  const save = useMutation({
    mutationFn: () => {
      const values: Record<string, unknown> = {};
      for (const f of EDIT_FIELDS) {
        const v = (form[f.key] ?? "").trim();
        if (f.key === "min_cgpa") values.min_cgpa = v === "" ? null : Number(v);
        else if (f.key === "allowed_branches") values.allowed_branches = v ? v.split(",").map((x) => x.trim()).filter(Boolean) : [];
        else values[f.key] = v || null;
      }
      values.backlog_policy = policy || null;
      if (policy === "MAX_ALLOWED") values.max_active_backlogs = Number(form.max_active_backlogs || 0);
      return api.put<InboundEmail>(`/api/inbox/${id}/details`, { values });
    },
    onSuccess: () => { toast.success("Details corrected — choose 'check again' to let the agents continue"); setEditing(false); refresh(); },
    onError: toastError,
  });

  const e = q.data;
  const question = e?.run?.status === "NEEDS_REVIEW" ? e.run.pending_question : null;
  const editable = canAct && e && !["DRIVE_CREATED", "PUBLISHED", "IGNORED"].includes(e.status);
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()} wide title={e?.subject ?? "Company email"}
      description={e ? `From ${e.from_name ? `${e.from_name} ` : ""}<${e.from_address}> · ${relTime(e.received_at)} · ${e.source === "GMAIL" ? "Gmail" : e.source === "MANUAL" ? "pasted" : e.source === "STAFF" ? "entered with New drive" : e.source.toLowerCase()}` : undefined}>
      {q.isError ? <ErrorState error={q.error} /> : !e ? <LoadingRows rows={6} /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={e.status} tone={STATUS_TONE[e.status]} label={STATUS_LABEL[e.status]} />
            {e.extraction_mode && <Tag tone={e.extraction_mode === "AI" ? "accent" : e.extraction_mode === "RULES_AI_FAILED" ? "warn" : "neutral"}>
              {e.extraction_mode === "AI" ? "Read by AI + rules" : e.extraction_mode === "RULES_AI_FAILED" ? "AI failed → rules" : "Read by rules"}</Tag>}
            {e.overrides.length > 0 && <Tag tone="info">Corrected: {e.overrides.join(", ")}</Tag>}
            {e.drive_id && <ButtonLink size="sm" href={`/drives/${e.drive_id}`}>Open drive #{e.drive_id}</ButtonLink>}
          </div>

          {e.run && <AgentFlowCard runId={e.run.id} title="How the agents handled this email" />}
          {question && (
            <Banner tone="warn" title="The agents need you">
              <p className="mb-2">{question.question}</p>
              {canAct && <div className="flex flex-wrap gap-2">
                {question.options?.map((o) => (
                  <Button key={String(o.value)} size="sm" variant={o.value === "confirm" ? "primary" : "secondary"} loading={answer.isPending && answer.variables === o.value}
                    onClick={() => answer.mutate(String(o.value))}>{o.label}</Button>
                ))}
              </div>}
            </Banner>
          )}
          {e.run?.summary && e.status === "PUBLISHED" && <Banner tone="ok" title="Done">{e.run.summary}</Banner>}

          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="What the agents read" description="Each value with the line it came from"
                actions={editable && !editing ? <Button size="sm" onClick={() => setEditing(true)}>Correct details</Button> : undefined} />
              <div className="p-4">
                {editing ? (
                  <div className="grid grid-cols-2 gap-3">
                    {EDIT_FIELDS.map((f) => (
                      <Field key={f.key} label={f.label} htmlFor={`f_${f.key}`} hint={f.hint}
                        className={f.key === "allowed_branches" || f.key === "registration_deadline" || f.key === "roster_due" ? "col-span-2" : ""}>
                        <Input id={`f_${f.key}`} type={f.type ?? "text"} value={form[f.key] ?? ""} onChange={(ev) => setForm({ ...form, [f.key]: ev.target.value })} />
                      </Field>
                    ))}
                    <Field label="Backlog rule" htmlFor="f_policy">
                      <Select id="f_policy" className="w-full" value={policy} onChange={(ev) => setPolicy(ev.target.value)}>
                        <option value="">Not stated (not considered)</option><option value="NO_ACTIVE_BACKLOGS">No active backlogs</option>
                        <option value="MAX_ALLOWED">Up to N backlogs</option><option value="NOT_CONSIDERED">Not considered</option>
                      </Select>
                    </Field>
                    {policy === "MAX_ALLOWED" && <Field label="Max backlogs" htmlFor="f_maxb"><Input id="f_maxb" inputMode="numeric" value={form.max_active_backlogs ?? ""} onChange={(ev) => setForm({ ...form, max_active_backlogs: ev.target.value })} /></Field>}
                    <div className="col-span-2 flex justify-end gap-2">
                      <Button size="sm" onClick={() => setEditing(false)}>Cancel</Button>
                      <Button size="sm" variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save corrections</Button>
                    </div>
                  </div>
                ) : (
                  <>
                  <div className="mb-4"><TwoReaders e={e} /></div>
                  <DefinitionList cols={2} items={[
                    { label: "Company", value: e.details.company ?? <Missing /> },
                    { label: "Role", value: e.details.role ?? <Missing /> },
                    { label: "Drive date", value: e.details.drive_date ?? <Missing /> },
                    { label: "Time", value: e.details.start_time ? `${e.details.start_time}–${e.details.end_time ?? "?"}` : <Missing /> },
                    { label: "Venue", value: e.details.venue ?? "—" },
                    { label: "CTC / stipend", value: e.details.ctc ?? "—" },
                    { label: "Register by", value: e.details.registration_deadline?.replace("T", " ") ?? <Missing /> },
                    { label: "List due to company", value: e.details.roster_due?.replace("T", " ") ?? "—" },
                    { label: "Minimum CGPA", value: e.details.min_cgpa ?? "None stated" },
                    { label: "Backlogs", value: e.details.backlog_policy === "NO_ACTIVE_BACKLOGS" ? "None allowed" : e.details.backlog_policy === "MAX_ALLOWED" ? `Up to ${e.details.max_active_backlogs}` : "Not considered" },
                    { label: "Branches", value: e.details.allowed_branches?.length ? e.details.allowed_branches.join(", ") : "All" },
                    { label: "Batch", value: e.details.graduation_years?.length ? e.details.graduation_years.join(", ") : "—" },
                  ]} />
                  </>
                )}
                {!editing && Object.keys(e.details.evidence ?? {}).length > 0 && (
                  <details className="mt-4 text-[12px]">
                    <summary className="cursor-pointer text-muted">Source lines</summary>
                    <ul className="mt-2 space-y-1">{Object.entries(e.details.evidence).map(([k, v]) => <li key={k}><span className="text-faint">{k}:</span> <span className="font-mono">{v}</span></li>)}</ul>
                  </details>
                )}
              </div>
            </Card>
            <div className="space-y-4">
              <Card>
                <CardHeader title="Criteria Check Agent" description="Must fix · Confirm · Note" />
                <div className="p-4"><IssueList issues={e.issues} /></div>
              </Card>
              <Card>
                <CardHeader title="Original email" description="Untrusted content: treated as data, never as instructions" />
                <pre className="max-h-72 overflow-auto whitespace-pre-wrap p-4 font-sans text-[13px] leading-relaxed text-ink-2">{e.body_text}</pre>
              </Card>
            </div>
          </div>

          {canAct && (
            <div className="flex flex-wrap justify-end gap-2 border-t border-line pt-3">
              {e.status === "NEW" && <Button variant="primary" icon={<Bot className="size-4" />} loading={process.isPending} onClick={() => process.mutate()}>Process with agents</Button>}
              {e.status === "NEEDS_REVIEW" && !e.run && <Button variant="primary" loading={process.isPending} onClick={() => process.mutate()}>Process again</Button>}
              {editable && <Button variant="ghost" loading={ignore.isPending} onClick={() => ignore.mutate()}>Ignore email</Button>}
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}

function Missing() {
  return <span className="font-medium text-bad">Not found</span>;
}

function PasteDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const [from, setFrom] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const paste = useMutation({
    mutationFn: () => api.post<InboundEmail & { created: boolean }>("/api/inbox", { from_address: from, subject, body, process: true }),
    onSuccess: (r) => {
      toast.success(r.created ? "Email added — the agents processed it" : "That email is already in the inbox");
      qc.invalidateQueries({ queryKey: ["inbox"] });
      onOpenChange(false); setFrom(""); setSubject(""); setBody("");
    },
    onError: toastError,
  });
  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="Paste a company email"
      description="For emails that did not reach the connected mailbox. The same agents and checks run on it."
      footer={<><Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" loading={paste.isPending} disabled={!from.includes("@") || !subject || !body} onClick={() => paste.mutate()}>Add and process</Button></>}>
      <div className="space-y-3">
        <Field label="From (company email address)" htmlFor="p_from"><Input id="p_from" value={from} onChange={(e) => setFrom(e.target.value)} placeholder="hr@company.com" /></Field>
        <Field label="Subject" htmlFor="p_subj"><Input id="p_subj" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Campus Drive – Company – Role" /></Field>
        <Field label="Email text" htmlFor="p_body"><Textarea id="p_body" rows={12} value={body} onChange={(e) => setBody(e.target.value)} /></Field>
      </div>
    </Dialog>
  );
}

function fmtWindow(minutes?: number) {
  if (!minutes) return "30 days";
  return minutes < 60 ? `${minutes} minutes` : minutes < 1440 ? `${minutes / 60} hour${minutes === 60 ? "" : "s"}` : `${minutes / 1440} day${minutes === 1440 ? "" : "s"}`;
}

const READ_FIELDS: [string, string][] = [["company", "Company"], ["role", "Role"], ["drive_date", "Drive date"],
  ["start_time", "Starts"], ["end_time", "Ends"], ["registration_deadline", "Register by"], ["min_cgpa", "Minimum CGPA"],
  ["backlog_policy", "Backlogs"], ["allowed_branches", "Branches"], ["graduation_years", "Batch"]];

function shown(v: unknown) {
  if (v === null || v === undefined || (Array.isArray(v) && v.length === 0)) return "—";
  if (Array.isArray(v)) return v.join(", ");
  const t = String(v);
  if (/^[A-Z_]+$/.test(t)) return t.replace(/_/g, " ").toLowerCase().replace(/^\w/, (c) => c.toUpperCase());
  return /^\d{4}-\d{2}-\d{2}T/.test(t) ? t.replace("T", " ") : t;
}

/** Gemini's reading next to the rule-based reading, field by field: the AI is visible, and checked. */
function TwoReaders({ e }: { e: InboundEmail }) {
  const r = e.reading;
  if (e.extraction_mode === "RULES_AI_FAILED") return (
    <Banner tone="warn" title="Gemini not used for this email">{r?.ai_error ? `Reason: ${r.ai_error}. ` : ""}The built-in rules read it instead; every check still ran.</Banner>
  );
  if (e.extraction_mode !== "AI" || !r?.rules) return null;
  const rows = READ_FIELDS.map(([k, label]) => {
    const a = shown(r.ai[k]), b = shown(r.rules![k]);
    return { k, label, a, b, same: a === b || b === "—" };
  });
  const agree = rows.filter((x) => x.same).length;
  return (
    <div className="rounded-md border border-line">
      <div className="flex items-center justify-between gap-2 border-b border-line bg-accent-soft/40 px-3 py-2">
        <p className="flex items-center gap-1.5 text-[12.5px] font-semibold text-accent"><Sparkles className="size-3.5" />Read by Gemini{r.model ? ` (${r.model})` : ""}, checked by rules</p>
        <p className="text-[12px] text-muted">{agree}/{rows.length} fields agree</p>
      </div>
      <table className="data-table w-full text-[12.5px]">
        <thead><tr><th>Field</th><th>Gemini read</th><th>Rules read</th><th /></tr></thead>
        <tbody>{rows.map((x) => (
          <tr key={x.k} className={x.same ? "" : "bg-warn-soft/40"}>
            <td className="text-muted">{x.label}</td><td>{x.a}</td><td className="text-muted">{x.b}</td>
            <td>{x.same ? <span className="text-ok">✓ agree</span> : <span className="text-warn">differs — check</span>}</td>
          </tr>))}</tbody>
      </table>
    </div>
  );
}

export default function InboxPage() {
  const qc = useQueryClient();
  const me = useMe();
  const [status, setStatus] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const [paste, setPaste] = useState(false);
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/api/health"), staleTime: 60_000 });
  const q = useQuery({ queryKey: ["inbox", status], queryFn: () => api.get<InboxResponse>(`/api/inbox${qs({ status })}`),
    placeholderData: keepPreviousData, refetchInterval: 10_000 });
  const canAct = can(me.data, "manageDrives");
  const check = useMutation({
    mutationFn: () => api.post<{ mailbox: { configured: boolean; message: string; new: number }; processed: { email_id: number; status?: string; error?: string }[] }>("/api/inbox/check"),
    onSuccess: (r) => {
      if (!r.mailbox.configured) toast.info(r.mailbox.message);
      else toast.success(r.mailbox.message, { description: r.processed.length ? `${r.processed.length} email(s) processed by the agents` : undefined });
      qc.invalidateQueries({ queryKey: ["inbox"] });
    },
    onError: toastError,
  });
  const samples = useMutation({
    mutationFn: () => api.post<{ added: number }>("/api/inbox/demo"),
    onSuccess: (r) => { toast.success(`${r.added} sample email(s) added`); qc.invalidateQueries({ queryKey: ["inbox"] }); },
    onError: toastError,
  });
  const box = q.data?.mailbox;
  const readSince = useMutation({
    mutationFn: (since: "now" | "all") => api.put<{ read_since: string | null }>("/api/inbox-settings/read-since", { since }),
    onSuccess: (r) => { toast.success(r.read_since ? "Older emails will be ignored" : "Older emails will be read on the next check"); qc.invalidateQueries({ queryKey: ["inbox"] }); },
    onError: toastError,
  });
  const pilot = q.data?.autopilot;

  return (
    <>
      <PageHeader title="Company emails"
        description="Recruitment emails from companies. The stage 1 agents read each email, check every detail, create the drive with its deadline, prepare its consent form and send it to every student."
        actions={canAct ? <>
          <Button onClick={() => samples.mutate()} loading={samples.isPending} icon={<Sparkles className="size-4" />}>{health.data?.seed_profile === "demo" ? "Load sample emails" : "Load test emails (E1–E6)"}</Button>
          <Button onClick={() => setPaste(true)} icon={<ClipboardPaste className="size-4" />}>Paste an email</Button>
          <Button variant="primary" onClick={() => check.mutate()} loading={check.isPending} icon={<RefreshCw className="size-4" />}>Check mailbox now</Button>
        </> : undefined} />

      {box && (
        <div className="mb-4">
          {box.source ? (
            <Banner tone="ok" title={`Watching ${box.address} (${box.source === "GMAIL" ? "Gmail" : "IMAP"})`}>
              Reading emails whose subject contains “{box.filter || "anything"}”. Read-only: nothing is marked, moved or deleted.
              {pilot?.enabled ? ` The Mailbox Agent checks every ${Math.round(pilot.interval_seconds / 60) || 1} min${pilot.last_tick_at ? ` (last check ${relTime(pilot.last_tick_at)})` : ""}.` : " Autopilot is off — use Check mailbox now."}
              <span className="mt-1 block">
                Reading emails from the last <strong>{fmtWindow(box.window_minutes)}</strong> (change in Settings){box.read_since ? <>, received after the last reset ({fmtDateTime(box.read_since)})</> : null}.
                {can(me.data, "connectMailbox") && <button type="button" className="ml-2 text-accent underline" disabled={readSince.isPending}
                  onClick={() => readSince.mutate(box.read_since ? "all" : "now")}>{box.read_since ? "Ignore the reset time" : "Ignore emails already in the mailbox"}</button>}
              </span>
            </Banner>
          ) : (
            <Banner tone="info" title="No mailbox connected yet"
              action={can(me.data, "connectMailbox") ? <ButtonLink size="sm" href="/settings">Connect Gmail</ButtonLink> : undefined}>
              Connect the drive upload officer’s Gmail in Settings so company emails arrive here automatically. Until then, you can paste a company email here.
            </Banner>
          )}
        </div>
      )}

      <Card>
        <CardHeader title="Emails" actions={
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button key={f.value} onClick={() => setStatus(f.value)} aria-pressed={status === f.value}
                className={cn("rounded-full px-2.5 py-1 text-[12px] ring-1 ring-inset", status === f.value ? "bg-accent text-white ring-accent" : "ring-line-strong hover:bg-subtle")}>
                {f.label}{f.value && q.data?.counts[f.value] ? ` (${q.data.counts[f.value]})` : ""}
              </button>
            ))}
          </div>} />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} /></div> : !q.data ? <LoadingRows /> : (
          <DataTable data={q.data.items}
            empty={<EmptyState icon={<Inbox className="size-5" />} title="No company emails yet"
              description="Companies email the placement mailbox with “Campus Drive” in the subject. Connect Gmail, paste an email, or load the samples." />}
            columns={[
              { accessorKey: "from_address", header: "From", cell: ({ row }) => <span><span className="font-medium">{row.original.from_name ?? row.original.details.company ?? "—"}</span><br /><span className="font-mono text-[11.5px] text-muted">{row.original.from_address}</span></span> },
              { accessorKey: "subject", header: "Subject", cell: ({ row }) => <button className="text-left hover:underline" onClick={() => setOpenId(row.original.id)}>{row.original.subject}</button> },
              { accessorKey: "status", header: "Status", cell: ({ row }) => <span className="flex flex-col items-start gap-1">
                <StatusBadge status={row.original.status} tone={STATUS_TONE[row.original.status]} label={STATUS_LABEL[row.original.status]} />
                {row.original.issues.some((i) => i.code === "SUSPICIOUS") && <Tag tone="bad"><ShieldAlert className="mr-1 size-3" />Suspicious text</Tag>}
              </span> },
              { id: "drive", header: "Drive", cell: ({ row }) => row.original.drive_id ? <Link className="text-accent hover:underline" href={`/drives/${row.original.drive_id}`}>{row.original.drive_title ?? `#${row.original.drive_id}`}</Link> : <span className="text-faint">—</span> },
              { accessorKey: "received_at", header: "Received", cell: ({ row }) => <span className="whitespace-nowrap text-[12px] text-muted">{relTime(row.original.received_at)}</span> },
              { id: "open", header: "", cell: ({ row }) => <Button size="sm" onClick={() => setOpenId(row.original.id)} icon={row.original.status === "NEEDS_REVIEW" ? <MailOpen className="size-3.5" /> : <MailCheck className="size-3.5" />}>{row.original.status === "NEEDS_REVIEW" && canAct ? "Review" : "Open"}</Button> },
            ]} />
        )}
      </Card>
      {openId !== null && <EmailDetail id={openId} onClose={() => setOpenId(null)} />}
      <PasteDialog open={paste} onOpenChange={setPaste} />
    </>
  );
}
