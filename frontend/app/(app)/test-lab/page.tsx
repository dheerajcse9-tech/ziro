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

interface Story { goal: string; setup: { columns: string[]; rows: string[][] }; steps: string[] }

/** Why a scenario passed: what it proves, its data, what it did, and the rule behind every check. */
function Explained({ l }: { l: TestRun }) {
  const story: Story | undefined = l.input_fixture?.story;
  const passed = l.checks.filter((c) => c.passed).length;
  return (
    <div className="space-y-5 border-t border-line p-4">
      <Banner tone={l.passed ? "ok" : "bad"} title={l.passed
        ? `PASS — all ${l.checks.length} checks matched`
        : `FAIL — ${l.checks.length - passed} of ${l.checks.length} checks did not match`}>
        {l.passed
          ? "The test ran the real code in a fresh sandbox, then read the results back from the database and the company's form. Every value it read equals what the rules say it must be (table below)."
          : "At least one value read back differs from what the rules require; see the red rows below."}
      </Banner>
      {l.failure_details && <Banner tone="bad" title="Failure details"><pre className="whitespace-pre-wrap font-mono text-[11.5px]">{l.failure_details}</pre></Banner>}
      {story && (
        <>
          <section>
            <p className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-faint">What this test proves</p>
            <p className="text-[13.5px] leading-relaxed text-ink">{story.goal}</p>
          </section>
          <section>
            <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint">Test data</p>
            <div className="overflow-x-auto rounded-md border border-line">
              <table className="data-table w-full text-[12.5px]">
                <thead><tr>{story.setup.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                <tbody>{story.setup.rows.map((r, i) => <tr key={i}>{r.map((v, j) => <td key={j} className={cn(j === 0 && "font-mono text-[12px]")}>{v}</td>)}</tr>)}</tbody>
              </table>
            </div>
          </section>
          <section>
            <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint">What the test did</p>
            <ol className="list-decimal space-y-1 pl-5 text-[13px] text-ink-2">{story.steps.map((s) => <li key={s}>{s}</li>)}</ol>
          </section>
        </>
      )}
      <section>
        <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint">Why it passed — every check</p>
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="data-table w-full">
            <thead><tr><th className="w-6" /><th>Check</th><th>Why this is the right answer</th><th>Expected</th><th>Actual (read back)</th></tr></thead>
            <tbody>{l.checks.map((c, i) => (
              <tr key={i} className={cn(!c.passed && "bg-bad-soft/40")}>
                <td>{c.passed ? <CheckCircle2 className="size-4 text-ok" /> : <XCircle className="size-4 text-bad" />}</td>
                <td className="text-[12.5px] font-medium">{c.name}</td>
                <td className="min-w-64 text-[12.5px] text-ink-2">{c.why ?? "—"}</td>
                <td className="max-w-xs break-words font-mono text-[11.5px] text-muted">{fmtVal(c.expected)}</td>
                <td className={cn("max-w-xs break-words font-mono text-[11.5px]", c.passed ? "text-ink-2" : "text-bad")}>{fmtVal(c.actual)}</td>
              </tr>))}</tbody>
          </table>
        </div>
      </section>
      <details className="rounded-md border border-line p-3">
        <summary className="cursor-pointer text-[12.5px] font-medium text-muted">Raw data (technical): input, expected, actual, evidence</summary>
        <div className="mt-3 grid gap-3 lg:grid-cols-2">
          <JsonView value={l.input_fixture} label="Input" />
          <JsonView value={l.expected} label="Expected result" />
          <JsonView value={l.actual} label="Actual result" />
          <JsonView value={l.evidence} label="Evidence (agent run, upload, verification, attendance)" />
        </div>
      </details>
    </div>
  );
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
                {expanded && l && <Explained l={l} />}
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
