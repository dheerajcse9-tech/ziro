"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, FlaskConical, Play, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { JsonView } from "@/components/ui/evidence";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn, fmtDateTime } from "@/lib/format";
import type { TestRun } from "@/lib/types";

interface TestsResponse { environment: string; tests: { test_id: string; title: string; description: string; runs: number; latest: TestRun | null }[] }

function fmtVal(v: unknown) {
  return typeof v === "string" ? v : JSON.stringify(v);
}

export default function TestLabPage() {
  const me = useMe();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["tests"], queryFn: () => api.get<TestsResponse>("/api/tests") });
  const history = useQuery({ queryKey: ["test-history"], queryFn: () => api.get<TestRun[]>("/api/tests/runs?limit=40") });
  const run = useMutation({
    mutationFn: (ids: string[]) => api.post<TestRun[]>("/api/tests/run", { test_ids: ids }),
    onSuccess: (r) => {
      const failed = r.filter((x) => !x.passed);
      (failed.length ? toast.error : toast.success)(`${r.length - failed.length}/${r.length} scenarios passed`);
      qc.invalidateQueries({ queryKey: ["tests"] }); qc.invalidateQueries({ queryKey: ["test-history"] });
    },
    onError: toastError,
  });
  const allowed = can(me.data, "runAgent");

  return (
    <>
      <PageHeader title="Test Lab" description="TC1–TC4: the official hackathon scenarios. TC5: every company-email case. TC6: every test student's journey through the three steps. Each runs the real code in a throwaway sandbox (no real email is sent); results are recorded as they come out."
        actions={<>
          <a href="/api/tests/runs.csv" className="inline-flex h-9 items-center gap-2 rounded-md border border-line-strong bg-surface px-3.5 text-sm font-medium hover:bg-subtle"><Download className="size-4" />Evidence CSV</a>
          {allowed && <Button variant="primary" loading={run.isPending && (run.variables?.length ?? 0) > 1} disabled={!q.data} onClick={() => run.mutate(q.data!.tests.map((t) => t.test_id))} icon={<Play className="size-4" />}>Run all scenarios</Button>}
        </>} />
      {q.data && <div className="mb-4"><Banner tone="info" title="Execution environment">{q.data.environment}. Demo data is never touched.</Banner></div>}
      {q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : !q.data ? <LoadingRows /> : (
        <div className="space-y-3">
          {q.data.tests.map((t) => {
            const l = t.latest;
            const expanded = open === t.test_id;
            return (
              <Card key={t.test_id}>
                <div className="flex flex-wrap items-start justify-between gap-3 p-4">
                  <div className="flex min-w-0 gap-3">
                    <div className={cn("mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md font-mono text-[12px] font-semibold ring-1",
                      !l ? "bg-subtle text-muted ring-line" : l.passed ? "bg-ok-soft text-ok ring-ok/20" : "bg-bad-soft text-bad ring-bad/20")}>{t.test_id}</div>
                    <div className="min-w-0">
                      <p className="text-[14px] font-semibold">{t.title}</p>
                      <p className="text-[12.5px] text-muted">{t.description}</p>
                      {l && <p className="mt-1 text-[12px] text-muted">Last run {fmtDateTime(l.executed_at)} · {l.duration_ms} ms · {l.checks.filter((c) => c.passed).length}/{l.checks.length} checks · {t.runs} run(s) total</p>}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {l ? <StatusBadge status={l.passed ? "PASS" : "FAIL"} /> : <StatusBadge status="NONE" label="Not run" />}
                    {l && <Button size="sm" variant="ghost" onClick={() => setOpen(expanded ? null : t.test_id)}>{expanded ? "Hide" : "Details"}</Button>}
                    {allowed && <Button size="sm" loading={run.isPending && run.variables?.length === 1 && run.variables[0] === t.test_id} disabled={run.isPending}
                      onClick={() => run.mutate([t.test_id])} icon={<FlaskConical className="size-3.5" />}>Run</Button>}
                  </div>
                </div>
                {expanded && l && (
                  <div className="space-y-4 border-t border-line p-4">
                    {l.failure_details && <Banner tone="bad" title="Failure details"><pre className="whitespace-pre-wrap font-mono text-[11.5px]">{l.failure_details}</pre></Banner>}
                    <div>
                      <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint">Checks (expected vs actual)</p>
                      <table className="data-table w-full rounded-md border border-line">
                        <thead><tr><th className="w-6" /><th>Check</th><th>Expected</th><th>Actual</th></tr></thead>
                        <tbody>{l.checks.map((c, i) => (
                          <tr key={i}>
                            <td>{c.passed ? <CheckCircle2 className="size-4 text-ok" /> : <XCircle className="size-4 text-bad" />}</td>
                            <td className="text-[12.5px]">{c.name}</td>
                            <td className="max-w-xs break-words font-mono text-[11.5px] text-muted">{fmtVal(c.expected)}</td>
                            <td className={cn("max-w-xs break-words font-mono text-[11.5px]", c.passed ? "text-ink-2" : "text-bad")}>{fmtVal(c.actual)}</td>
                          </tr>))}</tbody>
                      </table>
                    </div>
                    <div className="grid gap-3 lg:grid-cols-2">
                      <JsonView value={l.input_fixture} label="Input fixture" defaultOpen />
                      <JsonView value={l.expected} label="Expected result" defaultOpen />
                      <JsonView value={l.actual} label="Actual result" defaultOpen />
                      <JsonView value={l.evidence} label="Evidence (agent run, proposal hash, upload, verification, attendance)" defaultOpen />
                    </div>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      )}
      <Card className="mt-4">
        <CardHeader title="Execution history" description="Every recorded run, newest first." />
        {!history.data ? <LoadingRows rows={3} /> : history.data.length === 0 ? <EmptyState title="No test runs recorded yet" /> : (
          <table className="data-table w-full"><thead><tr><th>Run</th><th>Test</th><th>Result</th><th>Checks</th><th>Duration</th><th>Executed</th></tr></thead>
            <tbody>{history.data.map((h) => (
              <tr key={h.id}><td className="font-mono text-[12px]">#{h.id}</td><td>{h.test_id} · {h.title}</td><td><StatusBadge status={h.passed ? "PASS" : "FAIL"} /></td>
                <td className="tabular">{h.checks.filter((c) => c.passed).length}/{h.checks.length}</td><td className="tabular">{h.duration_ms} ms</td><td className="whitespace-nowrap text-[12px]">{fmtDateTime(h.executed_at)}</td></tr>))}</tbody></table>
        )}
      </Card>
    </>
  );
}
