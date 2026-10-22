"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Bot, Check, ChevronRight, Hand, Loader2, Sparkles, UserRound, X } from "lucide-react";
import Link from "next/link";
import { Card, CardHeader } from "@/components/ui/primitives";
import { LoadingRows } from "@/components/ui/states";
import { api } from "@/lib/api";
import { ROLE_LABEL } from "@/lib/auth";
import { cn, relTime } from "@/lib/format";
import type { AgentRun, CrewAgent, CrewResponse, PlanStep } from "@/lib/types";

type State = "done" | "working" | "waiting" | "stopped" | "next" | "skipped";

interface Node {
  kind: "agent" | "person";
  key: string;
  name: string;
  purpose?: string;
  state: State;
  results: string[];
  steps: string[];
  ai?: boolean;
}

const WAITING = new Set(["NEEDS_REVIEW", "AWAITING_APPROVAL", "PENDING_RECONCILIATION"]);

const STATE_LABEL: Record<State, string> = {
  done: "Done", working: "Working…", waiting: "Waiting", stopped: "Stopped", next: "Not yet", skipped: "Skipped",
};

/** Agents' notes in plain words (the raw note stays in the technical timeline). */
export function plain(note: string): string {
  return note
    .replace(/^Verdict READY_WITH_NOTES$/, "Checked: ready, with notes for the approver")
    .replace(/^Verdict READY$/, "Checked: ready to approve")
    .replace(/^Verdict DO_NOT_APPROVE$/, "Checked: do not approve yet")
    .replace(/^Approval #\d+ requested$/, "List sent to the approver")
    .replace(/^0 student\(s\) held for clash resolution$/, "No schedule clashes")
    .replace(/ held for clash resolution$/, " held for a schedule clash")
    .replace(/^(\d+)\/(\d+) eligible said yes, (\d+) no, (\d+) no answer$/, "$1 of $2 who qualify said YES · $3 said NO · $4 no answer");
}

function stepState(p: PlanStep): State {
  if (p.status === "done") return "done";
  if (p.status === "running") return "working";
  if (p.status === "failed" || p.status === "rejected") return "stopped";
  if (p.status === "skipped") return "skipped";
  if (p.status === "blocked" || p.status === "uncertain") return "waiting";
  return "next";
}

function worst(states: State[]): State {
  for (const s of ["stopped", "waiting", "working"] as State[]) if (states.includes(s)) return s;
  if (states.every((s) => s === "skipped")) return "skipped";
  if (states.every((s) => s === "done" || s === "skipped")) return "done";
  return states.some((s) => s === "done") ? "working" : "next";
}

/** Turn a run's plan into the chain of agents that handled it, with a person wherever the run waits. */
export function flowNodes(run: AgentRun, agents: CrewAgent[]): Node[] {
  const agentOf = (node: string) => agents.find((a) => a.nodes.includes(node));
  const nodes: Node[] = [];
  for (const p of run.plan ?? []) {
    const a = agentOf(p.node);
    const key = a?.key ?? p.node;
    const last = nodes[nodes.length - 1];
    const target = last && last.key === key ? last : null;
    const st = stepState(p);
    if (target) {
      target.steps.push(p.title);
      if (p.note) target.results.push(plain(p.note));
      target.state = worst([target.state, st]);
    } else {
      nodes.push({ kind: "agent", key, name: a?.name ?? p.title, purpose: a?.purpose, state: st, results: p.note ? [plain(p.note)] : [],
        steps: [p.title], ai: a?.uses_ai });
    }
  }
  // Where the run waits for a person, put that person in the chain: right after the agent that stopped,
  // otherwise before the first agent that has not started yet.
  if (WAITING.has(run.status) || run.status === "FAILED") {
    const approval = run.status === "AWAITING_APPROVAL";
    const person: Node = {
      kind: "person", key: "person", state: run.status === "FAILED" ? "stopped" : "waiting", steps: [],
      name: approval || run.intent === "FOLLOW_UP" ? ROLE_LABEL.approver
        : run.intent === "PROCESS_COMPANY_EMAIL" ? ROLE_LABEL.coordinator : ROLE_LABEL.officer,
      results: [approval ? "Checks the list and approves or rejects it. Agents never approve."
        : run.status === "FAILED" ? "The task stopped safely; see the message above." : "Answers the agents' question above, then they continue."],
    };
    const stoppedAt = approval ? -1 : nodes.findIndex((n) => n.state === "waiting" || n.state === "stopped");
    const firstNext = nodes.findIndex((n) => n.state === "next");
    const at = stoppedAt >= 0 ? stoppedAt + 1 : firstNext >= 0 ? firstNext : nodes.length;
    nodes.splice(at, 0, person);
  }
  return nodes;
}

function Badge({ state }: { state: State }) {
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
      state === "done" && "bg-ok-soft text-ok", state === "working" && "bg-info-soft text-info",
      state === "waiting" && "bg-warn-soft text-warn", state === "stopped" && "bg-bad-soft text-bad",
      (state === "next" || state === "skipped") && "bg-subtle text-muted")}>
      {state === "done" ? <Check className="size-3" /> : state === "working" ? <Loader2 className="size-3 animate-spin" />
        : state === "waiting" ? <Hand className="size-3" /> : state === "stopped" ? <X className="size-3" /> : null}
      {STATE_LABEL[state]}
    </span>
  );
}

function NodeCard({ n, step, compact }: { n: Node; step: number; compact?: boolean }) {
  const person = n.kind === "person";
  return (
    <div className={cn("relative flex h-full w-52 flex-col gap-1.5 rounded-lg border p-3",
      person ? "border-dashed border-warn bg-warn-soft/40" : n.state === "next" || n.state === "skipped" ? "border-line bg-subtle/60 opacity-70"
        : n.state === "working" ? "border-info bg-info-soft/30" : n.state === "stopped" ? "border-bad bg-bad-soft/30" : "border-line bg-surface")}>
      <div className="flex items-center gap-2">
        <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full",
          person ? "bg-warn text-white" : n.state === "done" ? "bg-ok text-white" : n.state === "next" ? "bg-line text-muted" : "bg-accent text-white")}>
          {person ? <UserRound className="size-4" /> : <Bot className="size-4" />}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-[13px] font-semibold text-ink" title={n.purpose}><span className="mr-1 text-faint">{step}.</span>{n.name}</span>
          <span className="flex items-center gap-1"><Badge state={n.state} />{n.ai && <Sparkles className="size-3 text-accent" aria-label="Can use AI" />}</span>
        </span>
      </div>
      {!compact && (n.results.length > 0
        ? <ul className="space-y-0.5 text-[12px] leading-snug text-ink-2">{n.results.slice(0, 3).map((r, i) => <li key={i}>{r}</li>)}</ul>
        : <p className="text-[12px] leading-snug text-muted">{n.state === "next" ? "Will " + (n.steps[0] ?? "").charAt(0).toLowerCase() + (n.steps[0] ?? "").slice(1) : n.steps.join("; ")}</p>)}
    </div>
  );
}

/** The chain of agents for one run: who did what, and who handed over to whom. */
export function AgentFlow({ run, agents, compact }: { run: AgentRun; agents: CrewAgent[]; compact?: boolean }) {
  const nodes = flowNodes(run, agents);
  return (
    <div>
      <ol className="flex flex-wrap items-stretch gap-x-1.5 gap-y-3">
        {nodes.map((n, i) => (
          <li key={`${n.key}-${i}`} className="flex items-center gap-1.5">
            <NodeCard n={n} step={i + 1} compact={compact} />
            {i < nodes.length - 1 && (
              <span className="flex shrink-0 flex-col items-center text-faint" aria-label="hands over to">
                <ArrowRight className="size-4" />
              </span>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

const TITLE: Record<string, string> = {
  PROCESS_COMPANY_EMAIL: "Company email → drive → consent forms", PREPARE_ROSTER: "Student list for the approver",
  CHECK_CONFLICTS: "Schedule clash check", WHAT_IF: "What-if check", INVITE_STUDENTS: "Consent forms to students",
  REQUEST_MISSING_DATA: "Missing-details forms", FOLLOW_UP: "Personal forms before approval",
};

export function runTitle(run: AgentRun) {
  return TITLE[run.intent ?? ""] ?? "Agent task";
}

/** A card with the agent chain for a run, loaded by id; used wherever agents worked on something. */
export function AgentFlowCard({ runId, title, compact }: { runId: number; title?: string; compact?: boolean }) {
  const run = useQuery({ queryKey: ["run", String(runId)], queryFn: () => api.get<AgentRun>(`/api/agent/runs/${runId}`), refetchInterval: 4000 });
  const crew = useQuery({ queryKey: ["crew"], queryFn: () => api.get<CrewResponse>("/api/agents"), staleTime: 300_000 });
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Bot className="size-4 text-faint" />{title ?? (run.data ? runTitle(run.data) : "Agents")}</span>}
        description={run.data ? `Started ${relTime(run.data.created_at)}` : undefined}
        actions={<Link href={`/agent/${runId}`} className="inline-flex items-center gap-0.5 text-[12.5px] text-accent hover:underline">Details<ChevronRight className="size-3.5" /></Link>} />
      <div className="p-4">{!run.data || !crew.data ? <LoadingRows rows={2} /> : <AgentFlow run={run.data} agents={crew.data.agents} compact={compact} />}</div>
    </Card>
  );
}

/** Every agent chain that worked on a drive, newest first. */
export function DriveAgentFlows({ runs }: { runs: AgentRun[] }) {
  const crew = useQuery({ queryKey: ["crew"], queryFn: () => api.get<CrewResponse>("/api/agents"), staleTime: 300_000 });
  const list = [...runs].sort((a, b) => b.id - a.id).slice(0, 6);
  if (list.length === 0) return null;
  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Bot className="size-4 text-faint" />Agents on this drive</span>}
        description="Each row is one task: the agents that handled it, in order, and where a person stepped in." />
      {!crew.data ? <LoadingRows rows={3} /> : (
        <ul className="divide-y divide-line">
          {list.map((r) => (
            <li key={r.id} className="space-y-2 px-4 py-3">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[13px] font-medium text-ink">{runTitle(r)} <span className="font-normal text-muted">· {relTime(r.created_at)}</span></p>
                <Link href={`/agent/${r.id}`} className="inline-flex items-center gap-0.5 text-[12.5px] text-accent hover:underline">Details<ChevronRight className="size-3.5" /></Link>
              </div>
              <AgentFlow run={r} agents={crew.data.agents} compact />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
