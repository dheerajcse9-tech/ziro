"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Send, Sparkles, Wrench } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader, Select, Textarea } from "@/components/ui/primitives";
import { Banner, EmptyState, LoadingRows } from "@/components/ui/states";
import { PlannerBadge, StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { relTime } from "@/lib/format";
import type { AgentRun, Health } from "@/lib/types";

const EXAMPLES = [
  "Prepare the eligible roster for the upcoming drive, identify unresolved records, and show me the proposal for approval.",
  "Prepare the roster for Helios Analytics and flag any schedule clashes.",
  "What if we lower the CGPA threshold to 6.5 for the Northwind drive?",
  "Invite eligible students for the Bluepeak drive.",
  "Email students with missing data for the Northwind drive.",
  "Check clashes for the Bluepeak drive.",
];

interface ToolInfo { name: string; description: string; permission: string; llm_callable: boolean }

function AgentInner() {
  const me = useMe();
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [objective, setObjective] = useState(params.get("objective") ?? "");
  const [status, setStatus] = useState("");
  const startedAt = useRef<number | null>(null);
  const runs = useQuery({ queryKey: ["runs", status], queryFn: () => api.get<AgentRun[]>(`/api/agent/runs${status ? `?status=${status}` : ""}`), refetchInterval: 5_000 });
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/api/health") });
  const tools = useQuery({ queryKey: ["tools"], queryFn: () => api.get<ToolInfo[]>("/api/agent/tools") });

  const start = useMutation({
    mutationFn: (text: string) => api.post<AgentRun>("/api/agent/runs", { objective: text }),
    onSuccess: (run) => { qc.invalidateQueries(); router.push(`/agent/${run.id}`); },
    onError: toastError,
    onSettled: () => { startedAt.current = null; },
  });

  // While a long (LLM) run executes, jump to its live view as soon as it is persisted.
  const watcher = useQuery({
    queryKey: ["runs-watch"], enabled: start.isPending, refetchInterval: 700,
    queryFn: () => api.get<AgentRun[]>("/api/agent/runs?limit=3"),
  });
  useEffect(() => {
    if (!start.isPending || !startedAt.current || !watcher.data) return;
    const fresh = watcher.data.find((r) => new Date(r.created_at).getTime() >= startedAt.current! - 2000 && r.created_by_id === me.data?.id);
    if (fresh) router.push(`/agent/${fresh.id}`);
  }, [watcher.data, start.isPending, router, me.data]);

  const allowed = can(me.data, "runAgent");
  return (
    <>
      <PageHeader title="Run a drive"
        description="Delegate a drive workflow in plain language. The agent plans, calls typed tools and stops for human approval before any external action. Everything shown is read from persisted events." />
      {health.data && health.data.planner !== "LLM" && (
        <div className="mb-4"><Banner tone="info" title="Running the deterministic fallback planner">No LLM API key is configured on this deployment, so goal interpretation uses the labelled rule-based planner. Tools, approval gate, upload and verification are identical in both modes.</Banner></div>
      )}
      <div className="grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title="New task" description="The agent cannot approve, change criteria or bypass consent — those stay with people." />
          <form className="space-y-3 p-4" onSubmit={(e) => { e.preventDefault(); if (objective.trim().length >= 3) { startedAt.current = Date.now(); start.mutate(objective.trim()); } }}>
            <Textarea aria-label="Objective" rows={3} value={objective} onChange={(e) => setObjective(e.target.value)} disabled={!allowed || start.isPending}
              placeholder="e.g. Prepare the eligible roster for the upcoming drive and show me the proposal for approval." />
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap gap-1.5">
                {EXAMPLES.map((ex) => (
                  <button key={ex} type="button" onClick={() => setObjective(ex)} disabled={!allowed}
                    className="inline-flex max-w-xs items-center gap-1 truncate rounded-full border border-line bg-subtle px-2.5 py-1 text-[12px] text-ink-2 hover:border-line-strong hover:bg-surface">
                    <Sparkles className="size-3 shrink-0 text-accent" /><span className="truncate">{ex}</span>
                  </button>))}
              </div>
              <Button type="submit" variant="primary" loading={start.isPending} disabled={!allowed || objective.trim().length < 3} icon={<Send className="size-4" />}>Run agent</Button>
            </div>
            {!allowed && <p className="text-[12px] text-muted">Your role can view runs but not start them.</p>}
          </form>
        </Card>
        <Card>
          <CardHeader title="Tool permissions" description="Typed tools with strict input schemas." />
          {!tools.data ? <LoadingRows rows={4} /> : (
            <ul className="divide-y divide-line">
              {tools.data.map((t) => (
                <li key={t.name} className="flex items-start gap-2 px-4 py-2">
                  <Wrench className="mt-0.5 size-3.5 shrink-0 text-faint" />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5"><span className="font-mono text-[12px] text-ink">{t.name}</span>
                      <Tag tone={t.permission === "EXECUTE" ? "bad" : t.permission === "PROPOSE" ? "warn" : t.permission === "NOTIFY" ? "info" : "neutral"}>{t.permission}</Tag>
                      {t.llm_callable && <Tag tone="accent">LLM-callable</Tag>}</div>
                    <p className="text-[11.5px] text-muted">{t.description}</p>
                  </div>
                </li>))}
            </ul>
          )}
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Runs" actions={<Select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {["AWAITING_APPROVAL", "NEEDS_REVIEW", "VERIFIED", "COMPLETED", "FAILED", "FAILED_VERIFICATION", "PENDING_RECONCILIATION", "REJECTED"].map((s) => <option key={s} value={s}>{s.replace(/_/g, " ").toLowerCase()}</option>)}
        </Select>} />
        {!runs.data ? <LoadingRows /> : (
          <DataTable data={runs.data} rowHref={(r) => `/agent/${r.id}`} empty={<EmptyState icon={<Bot className="size-5" />} title="No runs yet" description="Describe a task above to start the first run." />}
            columns={[
              { accessorKey: "id", header: "Run", cell: ({ row }) => <span className="font-mono text-[12px]">#{row.original.id}</span> },
              { accessorKey: "objective", header: "Objective", cell: ({ row }) => <span className="line-clamp-2 max-w-md text-[12.5px]">{row.original.objective}</span> },
              { accessorKey: "drive_title", header: "Drive", cell: ({ row }) => row.original.drive_title ?? <span className="text-faint">—</span> },
              { accessorKey: "intent", header: "Intent", cell: ({ row }) => row.original.intent ? <Tag>{row.original.intent.replace(/_/g, " ").toLowerCase()}</Tag> : "—" },
              { accessorKey: "planner_mode", header: "Planner", cell: ({ row }) => <PlannerBadge mode={row.original.planner_mode} /> },
              { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} /> },
              { accessorKey: "created_at", header: "Started", cell: ({ row }) => <span className="whitespace-nowrap text-[12px] text-muted">{relTime(row.original.created_at)}</span> },
            ]} />
        )}
      </Card>
    </>
  );
}

export default function AgentPage() {
  return <Suspense fallback={<LoadingRows />}><AgentInner /></Suspense>;
}
