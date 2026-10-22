"use client";

// Public guided walkthrough for hackathon evaluators. Each "Yes, continue" runs the next step of a full placement
// drive on the real backend code, in a private sandbox (no login, no real email, the institute's data untouched).
import { useMutation } from "@tanstack/react-query";
import { ArrowRight, Check, CheckCircle2, Circle, Loader2, Mail, PlayCircle, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { AgentFlow } from "@/components/agents/agent-flow";
import { Button, ButtonLink, Card } from "@/components/ui/primitives";
import { Banner } from "@/components/ui/states";
import { api } from "@/lib/api";
import { toastError } from "@/lib/errors";
import { cn } from "@/lib/format";
import type { AgentRun, CrewAgent } from "@/lib/types";

type Block =
  | { type: "heading"; text: string }
  | { type: "note"; text: string }
  | { type: "email"; from: string; to: string; subject: string; body: string }
  | { type: "table"; columns: string[]; rows: string[][] }
  | { type: "kv"; items: [string, string][] }
  | { type: "flow"; run: AgentRun };

interface Check { name: string; expected: unknown; actual: unknown; passed: boolean }
interface Step {
  index: number; key: string; role: string; title: string; explain: string; blocks: Block[]; checks: Check[];
  passed: boolean; finished: boolean; ms: number; error?: string;
}
interface Start { outline: string[]; total: number; agents: CrewAgent[] }

function show(v: unknown) {
  return typeof v === "string" ? v : JSON.stringify(v);
}

function BlockView({ b, agents }: { b: Block; agents: CrewAgent[] }) {
  if (b.type === "heading") return <p className="pt-2 text-[13px] font-semibold text-ink">{b.text}</p>;
  if (b.type === "note") return <Banner tone="warn" title={b.text} />;
  if (b.type === "flow") return <AgentFlow run={b.run} agents={agents} />;
  if (b.type === "kv") return (
    <dl className="grid gap-x-4 gap-y-1.5 rounded-md border border-line p-3 text-[13px] sm:grid-cols-[12rem_1fr]">
      {b.items.map(([k, v]) => <div key={k} className="contents"><dt className="font-medium text-ink">{k}</dt><dd className="text-ink-2">{v}</dd></div>)}
    </dl>
  );
  if (b.type === "table") return (
    <div className="overflow-x-auto rounded-md border border-line">
      <table className="data-table w-full text-[12.5px]">
        <thead><tr>{b.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={cn(j === 0 && "font-mono text-[12px]",
          c === "On the list" || c === "PASS" ? "font-medium text-ok" : c === "FAIL" ? "font-medium text-bad" : c.startsWith("Held") || c === "Missing details" ? "text-warn" : "")}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
  return (
    <div className="rounded-md border border-line bg-surface">
      <div className="space-y-0.5 border-b border-line bg-subtle px-3 py-2 text-[12px] text-muted">
        <p><Mail className="mr-1 inline size-3.5" /><strong className="text-ink">{b.subject}</strong></p>
        <p>From {b.from} · To {b.to}</p>
      </div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-3 py-2 font-sans text-[12.5px] leading-relaxed text-ink-2">{b.body}</pre>
    </div>
  );
}

function StepCard({ s, agents, total }: { s: Step; agents: CrewAgent[]; total: number }) {
  return (
    <Card id={`step-${s.index}`} className="scroll-mt-4">
      <div className="space-y-3 p-5">
        <div className="flex flex-wrap items-center gap-2 text-[12px]">
          <span className="rounded-full bg-ink px-2 py-0.5 font-semibold text-white">Step {s.index + 1} of {total}</span>
          {s.role && <span className="rounded-full bg-accent-soft px-2 py-0.5 font-medium text-accent">{s.role}</span>}
          <span className="text-faint">ran in {s.ms} ms</span>
        </div>
        <h2 className="text-[18px] font-semibold text-ink">{s.title}</h2>
        <p className="text-[13.5px] leading-relaxed text-ink-2">{s.explain}</p>
        {s.error && <Banner tone="bad" title="This step failed">{s.error}</Banner>}
        <div className="space-y-3">{s.blocks.map((b, i) => <BlockView key={i} b={b} agents={agents} />)}</div>
        <div className="rounded-md border border-line bg-subtle/60 p-3">
          <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint">Checks (read back from the database)</p>
          <ul className="space-y-1">
            {s.checks.map((c) => (
              <li key={c.name} className="flex items-start gap-2 text-[13px]">
                {c.passed ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> : <X className="mt-0.5 size-4 shrink-0 text-bad" />}
                <span><span className="text-ink">{c.name}</span>
                  {!c.passed && <span className="block text-[12px] text-bad">expected {show(c.expected)}, got {show(c.actual)}</span>}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

export default function TourPage() {
  const [tour, setTour] = useState<Start | null>(null);
  const [steps, setSteps] = useState<Step[]>([]);
  const bottom = useRef<HTMLDivElement>(null);
  const start = useMutation({
    mutationFn: () => api.get<Start>("/api/tour"),
    onSuccess: (t) => { setTour(t); setSteps([]); next.mutate(0); },
    onError: toastError,
  });
  const next = useMutation({
    mutationFn: (index: number) => api.post<Step>(`/api/tour/steps/${index}`),
    onSuccess: (s) => setSteps((prev) => [...prev, s]),
    onError: toastError,
  });
  useEffect(() => {
    if (steps.length) document.getElementById(`step-${steps.length - 1}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [steps.length]);

  const finished = steps.length > 0 && steps[steps.length - 1].finished;
  const totalChecks = steps.reduce((n, s) => n + s.checks.length, 0);
  const passedChecks = steps.reduce((n, s) => n + s.checks.filter((c) => c.passed).length, 0);

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-lg bg-ink text-white"><ShieldCheck className="size-5" /></div>
            <div><p className="text-[15px] font-semibold text-ink">PlaceCrew</p><p className="text-[12px] text-muted">Guided walkthrough for evaluators</p></div>
          </div>
          <ButtonLink size="sm" href="/login">Open the full app</ButtonLink>
        </div>
      </header>

      {!tour ? (
        <main className="mx-auto max-w-3xl space-y-5 px-4 py-10">
          <h1 className="text-[26px] font-semibold leading-tight text-ink">See a whole placement drive, run by agents, in 10 steps</h1>
          <p className="text-[14px] leading-relaxed text-ink-2">
            You only press <strong>Yes, continue</strong>. Each press runs the next step on the real code: two companies email the
            placement office, the agents create the drives and send consent forms, students answer, the placement officer runs the drives,
            the approver asks students for missing details, approves, and the companies receive their lists. Every step shows what the
            agents did, the emails they wrote, and checks read back from the database. The last step re-runs all test scenarios.
          </p>
          <Card className="p-4">
            <ul className="space-y-2 text-[13px] text-ink-2">
              <li className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-ok" />No login needed. Everything runs in a private sandbox created just for you.</li>
              <li className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-ok" />No real email is sent: you see each email on screen instead. (Installed with a Gmail account, the same steps send real email.)</li>
              <li className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-ok" />Agents do the routine work; people decide. Only the Approver can approve, and the walkthrough proves it.</li>
              <li className="flex gap-2"><Check className="mt-0.5 size-4 shrink-0 text-ok" />Takes about 5 minutes. You can stop at any step.</li>
            </ul>
          </Card>
          <Button variant="primary" className="h-11 px-6 text-[15px]" loading={start.isPending || next.isPending} icon={<PlayCircle className="size-5" />}
            onClick={() => start.mutate()}>Start the walkthrough</Button>
          <p className="text-[12.5px] text-muted">Prefer to click around yourself? <Link className="text-accent underline" href="/login">Open the app</Link> — the login page lists each role.</p>
        </main>
      ) : (
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[15rem_1fr]">
          <aside className="lg:sticky lg:top-4 lg:self-start">
            <ol className="space-y-1">
              {tour.outline.map((label, i) => {
                const s = steps[i];
                const current = !s && i === steps.length;
                return (
                  <li key={label}>
                    <a href={s ? `#step-${i}` : undefined} className={cn("flex items-center gap-2 rounded-md px-2 py-1.5 text-[13px]",
                      current ? "bg-accent-soft font-medium text-accent" : s ? "text-ink hover:bg-subtle" : "text-faint")}>
                      {s ? (s.passed ? <CheckCircle2 className="size-4 shrink-0 text-ok" /> : <X className="size-4 shrink-0 text-bad" />)
                        : current && next.isPending ? <Loader2 className="size-4 shrink-0 animate-spin" /> : <Circle className="size-4 shrink-0" />}
                      {i + 1}. {label}
                    </a>
                  </li>
                );
              })}
            </ol>
            {steps.length > 0 && <p className="mt-3 px-2 text-[12px] text-muted">{passedChecks} of {totalChecks} checks passed so far</p>}
          </aside>

          <main className="space-y-4 pb-28">
            {steps.map((s) => <StepCard key={s.index} s={s} agents={tour.agents} total={tour.total} />)}
            {finished && (
              <Card className="border-ok p-5">
                <h2 className="text-[18px] font-semibold text-ink">{passedChecks === totalChecks ? "Every check passed" : `${passedChecks} of ${totalChecks} checks passed`}</h2>
                <p className="mt-1 text-[13.5px] text-ink-2">
                  You saw a complete drive: company emails → drives and consent forms → students&apos; answers → the officer&apos;s lists → the
                  approver&apos;s forms → late qualifiers → approval → verified lists emailed to both companies, plus every test scenario.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button onClick={() => start.mutate()} loading={start.isPending}>Run the walkthrough again</Button>
                  <ButtonLink variant="primary" href="/login">Open the full app</ButtonLink>
                </div>
              </Card>
            )}
            <div ref={bottom} />
          </main>
        </div>
      )}

      {tour && !finished && (
        <div className="fixed inset-x-0 bottom-0 border-t border-line bg-surface/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <p className="text-[13px] text-ink-2">
              {next.isPending ? "The agents are working on this step…" : <>Next: <strong>{tour.outline[steps.length]}</strong></>}
            </p>
            <Button variant="primary" className="h-11 px-6 text-[15px]" loading={next.isPending} icon={<ArrowRight className="size-5" />}
              onClick={() => next.mutate(steps.length)}>Yes, continue</Button>
          </div>
        </div>
      )}
    </div>
  );
}
