"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CalendarClock, ListChecks, Send } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import { DataTable } from "@/components/ui/data-table";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader, Field, Select, Textarea } from "@/components/ui/primitives";
import { Banner, EmptyState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, ROLE_LABEL, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtDateTime, relTime } from "@/lib/format";
import type { AgentRun, Drive } from "@/lib/types";

// What a task is, in plain words.
const TASK_LABEL: Record<string, string> = {
  PREPARE_ROSTER: "Prepare student list", CHECK_CONFLICTS: "Check schedule clashes", WHAT_IF: "What-if check",
  INVITE_STUDENTS: "Send consent forms", REQUEST_MISSING_DATA: "Ask for missing details",
  PROCESS_COMPANY_EMAIL: "Read company email", FOLLOW_UP: "Follow-up forms",
};
const STATUS_LABEL: Record<string, string> = {
  AWAITING_APPROVAL: "Waiting for approver", NEEDS_REVIEW: "Needs your answer", VERIFIED: "Sent and checked",
  COMPLETED: "Done", FAILED: "Stopped", REJECTED: "Rejected", FAILED_VERIFICATION: "Check failed",
  PENDING_RECONCILIATION: "Being checked", RUNNING: "Working…", PLANNING: "Working…",
};

const ACTIONS = [
  { key: "prepare", label: "Prepare the student list for approval", icon: <ListChecks className="size-4" />,
    objective: (d: Drive) => `Prepare the eligible roster for drive #${d.id} (${d.title}), identify unresolved records, and show me the proposal for approval.` },
  { key: "clashes", label: "Only check schedule clashes", icon: <CalendarClock className="size-4" />,
    objective: (d: Drive) => `Check clashes for drive #${d.id} (${d.title}).` },
];

function AgentInner() {
  const me = useMe();
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [driveId, setDriveId] = useState(params.get("drive") ?? "");
  const [objective, setObjective] = useState("");
  const startedAt = useRef<number | null>(null);
  const drives = useQuery({ queryKey: ["drives"], queryFn: () => api.get<Drive[]>("/api/drives") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api.get<AgentRun[]>("/api/agent/runs"), refetchInterval: 5_000 });

  const start = useMutation({
    mutationFn: (text: string) => api.post<AgentRun>("/api/agent/runs", { objective: text }),
    onSuccess: (run) => { qc.invalidateQueries(); router.push(`/agent/${run.id}`); },
    onError: toastError,
    onSettled: () => { startedAt.current = null; },
  });

  // While a long (AI) run executes, jump to its live view as soon as it is saved.
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
  const open = (drives.data ?? []).filter((d) => ["OPEN", "DRAFT", "ROSTER_SUBMITTED"].includes(d.status));
  const drive = open.find((d) => String(d.id) === driveId);
  const visibleRuns = (runs.data ?? []).filter((r) => r.intent !== "PROCESS_COMPANY_EMAIL");

  return (
    <>
      <PageHeader title="Run a drive"
        description="Pick a drive. The agents keep only students who said YES on the consent form and meet the company's criteria, check for clashes, and send the list to the Approver." />
      {me.data && !allowed ? (
        <Banner tone="info" title="Running a drive is Step 2 — the Placement Officer's job">
          You are signed in as <strong>{ROLE_LABEL[me.data.role]}</strong>. To run a drive, sign out and sign in as the Placement Officer
          (<code className="font-mono">officer@placementos.demo</code>). You can follow the progress of every drive below.
        </Banner>
      ) : (
      <Card>
        <CardHeader title="Start" />
        <div className="space-y-4 p-4">
          <Field label="Drive" htmlFor="drive">
            <Select id="drive" value={driveId} onChange={(e) => setDriveId(e.target.value)} disabled={!allowed || start.isPending}>
              <option value="">Choose a drive…</option>
              {open.map((d) => <option key={d.id} value={d.id}>{d.title} — {fmtDateTime(d.starts_at, d.timezone)}</option>)}
            </Select>
          </Field>
          {drives.data && open.length === 0 && <p className="text-[12.5px] text-muted">No open drives yet. Drives appear when a company email is processed.</p>}
          <div className="flex flex-wrap gap-2">
            {ACTIONS.map((a, i) => (
              <Button key={a.key} variant={i === 0 ? "primary" : "secondary"} icon={a.icon} disabled={!allowed || !drive}
                loading={start.isPending && start.variables === (drive && a.objective(drive))}
                onClick={() => { if (drive) { startedAt.current = Date.now(); start.mutate(a.objective(drive)); } }}>{a.label}</Button>
            ))}
          </div>
          <form className="space-y-2 border-t border-line pt-4" onSubmit={(e) => { e.preventDefault(); if (objective.trim().length >= 3) { startedAt.current = Date.now(); start.mutate(objective.trim()); } }}>
            <p className="text-[12.5px] text-muted">Or ask in your own words, e.g. “What if we lower the CGPA to 6.5 for the Orbitra drive?”</p>
            <Textarea aria-label="Objective" rows={2} value={objective} onChange={(e) => setObjective(e.target.value)} disabled={!allowed || start.isPending} />
            <Button type="submit" size="sm" icon={<Send className="size-3.5" />} loading={start.isPending && start.variables === objective.trim()}
              disabled={!allowed || objective.trim().length < 3}>Run agent</Button>
          </form>
        </div>
      </Card>
      )}

      <Card className="mt-4">
        <CardHeader title="Recent tasks" />
        {!runs.data ? <LoadingRows /> : (
          <DataTable data={visibleRuns} rowHref={(r) => `/agent/${r.id}`} empty={<EmptyState icon={<Bot className="size-5" />} title="Nothing run yet" description="Choose a drive above to start." />}
            columns={[
              { accessorKey: "drive_title", header: "Drive", cell: ({ row }) => row.original.drive_title ?? <span className="text-faint">—</span> },
              { accessorKey: "intent", header: "Task", cell: ({ row }) => TASK_LABEL[row.original.intent ?? ""] ?? "Task" },
              { accessorKey: "status", header: "Status", cell: ({ row }) => <StatusBadge status={row.original.status} label={STATUS_LABEL[row.original.status]} /> },
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
