"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertOctagon, ArrowRight, Bot, CheckCircle2, Circle, CircleDashed, CircleDot, Download, Flag, Hand, Loader2,
  MessageSquare, ShieldAlert, Wrench, XCircle,
} from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { JsonView, RollList } from "@/components/ui/evidence";
import { DefinitionList, PageHeader } from "@/components/ui/layout";
import { Button, ButtonLink, Card, CardHeader } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn, fmtDateTime } from "@/lib/format";
import type { AgentEvent, AgentRun, CrewResponse } from "@/lib/types";

const TERMINAL = new Set(["VERIFIED", "REJECTED", "FAILED", "FAILED_VERIFICATION", "COMPLETED"]);
const WAITING = new Set(["NEEDS_REVIEW", "AWAITING_APPROVAL", "PENDING_RECONCILIATION"]);

function StepIcon({ status }: { status: string }) {
  if (status === "done") return <CheckCircle2 className="size-4 text-ok" />;
  if (status === "running") return <Loader2 className="size-4 animate-spin text-info" />;
  if (status === "failed" || status === "rejected") return <XCircle className="size-4 text-bad" />;
  if (status === "blocked" || status === "uncertain" || status === "pending") return <Hand className="size-4 text-warn" />;
  if (status === "skipped") return <CircleDashed className="size-4 text-faint" />;
  return <Circle className="size-4 text-line-strong" />;
}

const EVENT_STYLE: Record<string, { icon: React.ReactNode; tone: string }> = {
  GOAL: { icon: <Flag className="size-3.5" />, tone: "text-accent" },
  PLAN: { icon: <Bot className="size-3.5" />, tone: "text-accent" },
  TOOL_CALL: { icon: <Wrench className="size-3.5" />, tone: "text-ink-2" },
  TOOL_RESULT: { icon: <CheckCircle2 className="size-3.5" />, tone: "text-ok" },
  TOOL_ERROR: { icon: <XCircle className="size-3.5" />, tone: "text-bad" },
  TOOL_DENIED: { icon: <ShieldAlert className="size-3.5" />, tone: "text-bad" },
  ERROR: { icon: <AlertOctagon className="size-3.5" />, tone: "text-bad" },
  INTERRUPT: { icon: <Hand className="size-3.5" />, tone: "text-warn" },
  GUARDRAIL: { icon: <ShieldAlert className="size-3.5" />, tone: "text-warn" },
  APPROVAL: { icon: <CheckCircle2 className="size-3.5" />, tone: "text-accent" },
  HUMAN_INPUT: { icon: <MessageSquare className="size-3.5" />, tone: "text-accent" },
  LLM_MESSAGE: { icon: <MessageSquare className="size-3.5" />, tone: "text-accent" },
  LLM_ERROR: { icon: <AlertOctagon className="size-3.5" />, tone: "text-warn" },
  COMPLETE: { icon: <Flag className="size-3.5" />, tone: "text-ok" },
};

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const me = useMe();
  const qc = useQueryClient();
  const [tech, setTech] = useState(false);
  const run = useQuery({
    queryKey: ["run", id], queryFn: () => api.get<AgentRun>(`/api/agent/runs/${id}`),
    refetchInterval: (q) => (q.state.data && (TERMINAL.has(q.state.data.status) || WAITING.has(q.state.data.status)) ? false : 1000),
  });
  const events = useQuery({
    queryKey: ["run-events", id], queryFn: () => api.get<AgentEvent[]>(`/api/agent/runs/${id}/events`),
    refetchInterval: () => (run.data && (TERMINAL.has(run.data.status) || WAITING.has(run.data.status)) ? false : 1000),
  });
  const crew = useQuery({ queryKey: ["crew"], queryFn: () => api.get<CrewResponse>("/api/agents"), staleTime: 300_000 });
  const agentOf = (node?: string | null) => crew.data?.agents.find((a) => node && a.nodes.includes(node))?.name;
  const answer = useMutation({
    mutationFn: (value: string | number) => api.post<AgentRun>(`/api/agent/runs/${id}/answer`, { value }),
    onSuccess: () => qc.invalidateQueries(),
    onError: toastError,
  });

  if (run.isError) return <ErrorState error={run.error} onRetry={() => run.refetch()} />;
  if (!run.data) return <LoadingRows />;
  const r = run.data;
  const s = r.state ?? {};
  const q = r.pending_question;
  const live = !TERMINAL.has(r.status) && !WAITING.has(r.status);
  // By default only the plain-language steps are shown; tool inputs/outputs are behind "Show technical details".
  const shown = (events.data ?? []).filter((e) => tech || !["NODE_START", "TRANSITION", "TOOL_CALL", "TOOL_RESULT"].includes(e.type));
  const src = s.requirements?.retrieved_source;
  const mayAnswer = can(me.data, "runAgent") || (r.intent === "PROCESS_COMPANY_EMAIL" && can(me.data, "manageDrives"))
    || (r.intent === "FOLLOW_UP" && can(me.data, "followUp"));

  return (
    <>
      <PageHeader title={<span className="flex items-center gap-2">{r.drive_title ?? "Agent task"}{live && <Loader2 className="size-4 animate-spin text-info" />}</span>}
        crumbs={[{ label: "Run a drive", href: "/agent" }, { label: "Details" }]}
        description={<span className="italic">“{r.objective}”</span>}
        meta={<><StatusBadge status={r.status} />
          {r.drive_id && <Link href={`/drives/${r.drive_id}`} className="text-[12px] text-accent hover:underline">Open drive</Link>}</>} />

      {q && WAITING.has(r.status) || (q && r.status === "FAILED") ? (
        <div className="mb-4">
          <Card className={cn("border-l-4", r.status === "AWAITING_APPROVAL" ? "border-l-accent" : r.status === "FAILED" ? "border-l-bad" : "border-l-warn")}>
            <div className="flex flex-wrap items-start justify-between gap-3 p-4">
              <div className="min-w-0 flex-1">
                <p className="text-[12px] font-semibold uppercase tracking-wide text-muted">{r.status === "AWAITING_APPROVAL" ? "Waiting for human approval" : r.status === "FAILED" ? "Run stopped safely" : "Agent needs a decision"}</p>
                <p className="mt-1 text-[14px] text-ink">{q!.question}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {q!.type === "approval" && q!.approval_id && <ButtonLink variant="primary" href={`/approvals/${q!.approval_id}`} icon={<ArrowRight className="size-4" />}>Open approval</ButtonLink>}
                {(q!.type === "reconcile" || q!.type === "verification_pending") && q!.upload_id && <ButtonLink variant="primary" href={`/verification/${q!.upload_id}`}>Open Verification Center</ButtonLink>}
                {q!.type === "criteria_mismatch" && r.drive_id && <ButtonLink href={`/drives/${r.drive_id}`}>Review drive criteria</ButtonLink>}
                {q!.type === "intake_review" && <ButtonLink href="/inbox">Open company email</ButtonLink>}
                {mayAnswer && q!.options?.map((o) => (
                  <Button key={String(o.value)} variant={o.value === "stop" ? "ghost" : "secondary"} loading={answer.isPending && answer.variables === o.value}
                    disabled={answer.isPending} onClick={() => answer.mutate(o.value)}>{o.label}</Button>))}
              </div>
            </div>
          </Card>
        </div>
      ) : null}
      {r.status === "FAILED_VERIFICATION" && <div className="mb-4"><Banner tone="bad" title="Independent verification failed" action={s.upload_id ? <ButtonLink size="sm" href={`/verification/${s.upload_id}`}>Inspect discrepancy</ButtonLink> : undefined}>{r.error}</Banner></div>}
      {r.status === "VERIFIED" && <div className="mb-4"><Banner tone="ok" title="Roster uploaded and independently verified" action={s.upload_id ? <ButtonLink size="sm" href={`/verification/${s.upload_id}`}>Receipt &amp; attendance</ButtonLink> : undefined}>{r.summary}</Banner></div>}

      <div className="grid gap-4 xl:grid-cols-3">
        <div className="space-y-4">
          <Card>
            <CardHeader title="Steps" />
            {r.plan.length === 0 ? <EmptyState title="Planning…" /> : (
              <ol className="space-y-0.5 p-2">
                {r.plan.map((p, i) => (
                  <li key={p.node} className={cn("flex gap-2.5 rounded-md px-2 py-1.5", r.current_step === p.node && live && "bg-info-soft")}>
                    <div className="flex flex-col items-center"><StepIcon status={p.status} />{i < r.plan.length - 1 && <div className="mt-1 w-px flex-1 bg-line" />}</div>
                    <div className="min-w-0 pb-1">
                      <p className={cn("text-[13px]", p.status === "pending" ? "text-muted" : "text-ink")}>{p.title}</p>
                      {agentOf(p.node) && <p className="text-[11px] font-medium text-accent">{agentOf(p.node)}</p>}
                      {p.note && <p className="text-[11.5px] text-muted">{p.note}</p>}
                    </div>
                  </li>))}
              </ol>
            )}
          </Card>
          <Card>
            <CardHeader title="Details" />
            <div className="p-4"><DefinitionList cols={1} items={[
              { label: "Started", value: fmtDateTime(r.created_at) },
              { label: "Last update", value: fmtDateTime(r.updated_at) },
              ...(s.approval_id ? [{ label: "Approval", value: <Link className="text-accent hover:underline" href={`/approvals/${s.approval_id}`}>#{s.approval_id}</Link> }] : []),
              ...(s.upload_id ? [{ label: "Upload", value: <Link className="text-accent hover:underline" href={`/verification/${s.upload_id}`}>#{s.upload_id}</Link> }] : []),
            ]} /></div>
          </Card>
          {s.eligibility && (
            <Card>
              <CardHeader title="Who qualifies" />
              <div className="space-y-3 p-4">
                <div className="grid grid-cols-3 gap-2 text-center">
                  {[["Included", s.eligibility.counts.included, "text-ok"], ["Held", s.eligibility.counts.held, "text-hold"], ["Excluded", s.eligibility.counts.excluded, "text-ink"]].map(([l, v, c]) => (
                    <div key={l as string} className="rounded-md bg-subtle py-2"><div className={cn("text-xl font-semibold tabular", c as string)}>{v as number}</div><div className="text-[11px] text-muted">{l as string}</div></div>))}
                </div>
                <ul className="space-y-1 text-[12.5px]">
                  {Object.entries(s.eligibility.exclusion_reasons as Record<string, number>).map(([k, v]) => <li key={k} className="flex justify-between"><span className="text-muted">{k}</span><span className="tabular">{v}</span></li>)}
                </ul>
              </div>
            </Card>
          )}
          {s.verification && (
            <Card>
              <CardHeader title="Verification" />
              <div className="space-y-2 p-4 text-[13px]">
                <StatusBadge status={s.verification.status} />
                <p className="tabular">Observed {s.verification.observed_count ?? "—"} / approved {s.verification.expected_count}</p>
                {s.verification.missing?.length > 0 && <div><p className="text-[12px] text-muted">Missing</p><RollList rolls={s.verification.missing} tone="bad" /></div>}
                {s.verification.unexpected?.length > 0 && <div><p className="text-[12px] text-muted">Unexpected</p><RollList rolls={s.verification.unexpected} tone="bad" /></div>}
                {s.verification.duplicates?.length > 0 && <div><p className="text-[12px] text-muted">Duplicated</p><RollList rolls={s.verification.duplicates} tone="bad" /></div>}
                {s.attendance_sheet_id && (
                  <div className="flex gap-2 pt-1">
                    <a className="inline-flex items-center gap-1 text-[12.5px] text-accent hover:underline" href={`/api/attendance/${s.attendance_sheet_id}/download?format=csv`}><Download className="size-3.5" />Attendance CSV</a>
                    <a className="inline-flex items-center gap-1 text-[12.5px] text-accent hover:underline" href={`/api/attendance/${s.attendance_sheet_id}/download?format=xlsx`}><Download className="size-3.5" />XLSX</a>
                  </div>)}
              </div>
            </Card>
          )}
        </div>

        <div className="space-y-4 xl:col-span-2">
          {src && (
            <Card>
              <CardHeader title="Company's criteria" description="What the company published, compared with the drive's criteria"
                actions={src.discrepancies_vs_configured?.length ? <Tag tone="bad">Mismatch</Tag> : <Tag tone="ok">Consistent</Tag>} />
              <div className="grid gap-4 p-4 md:grid-cols-2">
                <div>
                  <p className="mb-1 text-[12px] font-semibold text-muted">Published text <span className="font-normal">(untrusted — treated as data)</span></p>
                  <pre className="whitespace-pre-wrap rounded-md border border-line bg-subtle p-3 text-[12px] text-ink-2">{src.published_criteria_text_untrusted}</pre>
                  {src.parsed_published_criteria?.suspicious_phrases?.length > 0 && (
                    <p className="mt-2 flex items-start gap-1.5 text-[12px] text-warn"><ShieldAlert className="mt-0.5 size-3.5 shrink-0" />Instruction-like text detected and ignored: {src.parsed_published_criteria.suspicious_phrases.join("; ")}</p>)}
                </div>
                <div className="space-y-2 text-[12.5px]">
                  <p className="text-[12px] font-semibold text-muted">Read from it</p>
                  <DefinitionList cols={1} items={[
                    { label: "CGPA minimum", value: src.parsed_published_criteria?.min_cgpa ?? "not stated" },
                    { label: "Backlog policy", value: src.parsed_published_criteria?.backlog_policy ?? "not stated" },
                    { label: "Branches", value: (src.parsed_published_criteria?.branches ?? []).join(", ") || "not stated / all" },
                  ]} />
                  {src.discrepancies_vs_configured?.length > 0 && <Banner tone="bad" title="Differs from configured criteria">{src.discrepancies_vs_configured.map((d: any) => `${d.field}: published ${JSON.stringify(d.published)} vs configured ${JSON.stringify(d.configured)}`).join("; ")}</Banner>}
                </div>
              </div>
            </Card>
          )}
          <Card>
            <CardHeader title="What the agents did"
              actions={<label className="flex items-center gap-1.5 text-[12px] text-muted"><input type="checkbox" checked={tech} onChange={(e) => setTech(e.target.checked)} />Show technical details</label>} />
            {!events.data ? <LoadingRows /> : (
              <ol className="relative px-4 py-3">
                {shown.map((e) => {
                  const st = EVENT_STYLE[e.type] ?? { icon: <CircleDot className="size-3.5" />, tone: "text-faint" };
                  const isTool = e.type === "TOOL_CALL" || e.type === "TOOL_RESULT" || e.type === "TOOL_ERROR";
                  return (
                    <li key={e.id} className="relative flex gap-3 pb-3">
                      <div className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-subtle ring-1 ring-line", st.tone)}>{st.icon}</div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                          {tech && <span className="text-[11px] font-semibold uppercase tracking-wide text-faint">{e.type.replace(/_/g, " ")}</span>}
                          {tech && e.tool_name && <span className="font-mono text-[12px] text-ink">{e.tool_name}</span>}
                          {e.payload?.agent_name ? <Tag tone="accent">{e.payload.agent_name}</Tag> : tech && e.node && <Tag>{e.node}</Tag>}
                          {e.status && !["OK", "STARTED"].includes(e.status) && <StatusBadge status={e.status} />}
                          {tech && e.duration_ms !== null && <span className="text-[11px] text-faint tabular">{e.duration_ms} ms</span>}
                          <span className="ml-auto text-[11px] text-faint tabular">{fmtDateTime(e.created_at, undefined, { year: undefined, day: undefined, month: undefined, second: "2-digit" })}</span>
                        </div>
                        {e.message && <p className={cn("mt-0.5 text-[13px]", e.type === "ERROR" || e.type === "TOOL_ERROR" ? "text-bad" : "text-ink-2")}>{e.message}</p>}
                        {tech && isTool && e.payload && Object.keys(e.payload).length > 0 && (
                          <div className="mt-1.5"><JsonView value={e.type === "TOOL_CALL" ? e.payload.input : e.payload.output ?? e.payload} label={e.type === "TOOL_CALL" ? "Input" : e.type === "TOOL_RESULT" ? "Output" : "Details"} /></div>)}
                        {tech && !isTool && e.payload && Object.keys(e.payload).length > 0 && ["PLAN", "INTERRUPT", "DECISION", "GUARDRAIL", "HUMAN_INPUT", "ERROR"].includes(e.type) && (
                          <div className="mt-1.5"><JsonView value={e.payload} label="Details" /></div>)}
                      </div>
                    </li>);
                })}
                {live && <li className="flex items-center gap-2 pl-1 text-[13px] text-info"><Loader2 className="size-4 animate-spin" />Agent is working…</li>}
              </ol>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
