"use client";

// Public guided walkthrough for hackathon evaluators. Each "Yes, continue" runs the next step of a full placement
// drive on the real backend code, in a private sandbox (no login, no real email, the institute's data untouched).
// The page also explains the workflow, the roles, every feature and every test case, and where each one lives in the app.
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowRight, Check, CheckCircle2, Circle, FlaskConical, Lightbulb, Loader2, Mail, MapPin, Pause, Play, PlayCircle,
  ShieldCheck, Sparkles, UserRound, X,
} from "lucide-react";
import { useEffect, useState } from "react";
import { AgentFlow } from "@/components/agents/agent-flow";
import { Button, ButtonLink, Card } from "@/components/ui/primitives";
import { Banner, LoadingRows } from "@/components/ui/states";
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
  why: string; in_app: string; features: string[]; proves: string[];
}
interface Feature { id: string; group: string; name: string; what: string; where: string; steps: number[] }
interface TestCase { id: string; name: string; what: string; steps: number[] }
interface Guide {
  outline: string[]; total: number; agents: CrewAgent[]; features: Feature[]; test_cases: TestCase[];
  roles: { role: string; login: string; job: string }[]; built_with: [string, string][];
}

const STAGES = [
  { n: 1, who: "Drive upload officer", title: "Company email → drive → consent forms",
    agents: "Mailbox · Email Reader (Gemini) · Criteria Check · Drive Builder · Consent Form · Student Outreach · Reminder" },
  { n: 2, who: "Placement officer", title: "Run the drive → student list",
    agents: "Goal Planner (Gemini) · Evidence · Eligibility · Consent · Conflict · Roster" },
  { n: 3, who: "Approver", title: "Missing details → approve → company gets the list",
    agents: "Approval Review · Form Builder · Follow-up Mailer · Response Reader · Re-check · Upload & Verification · Employer Update" },
];

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
        <tbody>{b.rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={cn(j === 0 && b.columns[0] === "Roll" && "font-mono text-[12px]",
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

function Chip({ children, tone = "plain" }: { children: React.ReactNode; tone?: "plain" | "accent" | "ok" | "dark" }) {
  return <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11.5px] font-medium",
    tone === "accent" && "bg-accent-soft text-accent", tone === "ok" && "bg-ok-soft text-ok", tone === "dark" && "bg-ink text-white",
    tone === "plain" && "bg-subtle text-ink-2")}>{children}</span>;
}

function StepCard({ s, guide }: { s: Step; guide: Guide }) {
  const names = Object.fromEntries(guide.features.map((f) => [f.id, f.name]));
  const passed = s.checks.filter((c) => c.passed).length;
  return (
    <Card id={`step-${s.index}`} className="scroll-mt-4">
      <div className="space-y-4 p-5">
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone="dark">Step {s.index + 1} of {guide.total}</Chip>
          {s.role && <Chip tone="accent"><UserRound className="size-3" />{s.role}</Chip>}
          {s.proves.map((tc) => <Chip key={tc} tone="ok"><FlaskConical className="size-3" />Proves {tc}</Chip>)}
          <span className="text-[11.5px] text-faint">ran on the real code in {s.ms} ms</span>
        </div>
        <h2 className="text-[19px] font-semibold leading-snug text-ink">{s.title}</h2>

        <div className="flex gap-2 rounded-md border border-warn/25 bg-warn-soft px-3 py-2 text-[13px] text-ink-2">
          <Lightbulb className="mt-0.5 size-4 shrink-0 text-warn" />
          <span><strong className="text-ink">Why it matters: </strong>{s.why}</span>
        </div>

        <div>
          <p className="mb-1 text-[12px] font-semibold uppercase tracking-wide text-faint">What happens</p>
          <p className="text-[13.5px] leading-relaxed text-ink-2">{s.explain}</p>
        </div>
        {s.error && <Banner tone="bad" title="This step failed">{s.error}</Banner>}

        <div className="space-y-3">{s.blocks.map((b, i) => <BlockView key={i} b={b} agents={guide.agents} />)}</div>

        <div className="rounded-md border border-line bg-subtle/60 p-3">
          <p className="mb-1.5 text-[12px] font-semibold uppercase tracking-wide text-faint">
            Proof: {passed} of {s.checks.length} checks passed (read back from the database)
          </p>
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

        <div className="grid gap-2 border-t border-line pt-3 text-[12.5px] sm:grid-cols-2">
          <p className="flex gap-1.5 text-ink-2"><MapPin className="mt-0.5 size-3.5 shrink-0 text-accent" />
            <span><strong className="text-ink">In the app: </strong>{s.in_app}</span></p>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-semibold text-ink">Features shown:</span>
            {s.features.map((f) => <Chip key={f}>{names[f] ?? f}</Chip>)}
          </div>
        </div>
      </div>
    </Card>
  );
}

function Section({ title, sub, children }: { title: string; sub?: string; children: React.ReactNode }) {
  return (
    <section className="space-y-3">
      <div><h2 className="text-[18px] font-semibold text-ink">{title}</h2>{sub && <p className="text-[13px] text-muted">{sub}</p>}</div>
      {children}
    </section>
  );
}

function Workflow() {
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {STAGES.map((st) => (
        <Card key={st.n} className="p-4">
          <p className="text-[11.5px] font-semibold uppercase tracking-wide text-accent">Stage {st.n} · {st.who}</p>
          <p className="mt-1 text-[14px] font-semibold text-ink">{st.title}</p>
          <p className="mt-2 text-[12px] leading-relaxed text-muted">Agents: {st.agents}</p>
        </Card>
      ))}
    </div>
  );
}

function FeatureMap({ guide, seen }: { guide: Guide; seen?: Set<string> }) {
  const groups = [...new Set(guide.features.map((f) => f.group))];
  return (
    <div className="grid gap-3 md:grid-cols-2">
      {groups.map((g) => (
        <Card key={g} className="p-4">
          <p className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-faint">{g}</p>
          <ul className="space-y-2.5">
            {guide.features.filter((f) => f.group === g).map((f) => (
              <li key={f.id} className="flex gap-2 text-[13px]">
                {seen ? (seen.has(f.id) ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> : <Circle className="mt-0.5 size-4 shrink-0 text-faint" />)
                  : <Sparkles className="mt-0.5 size-4 shrink-0 text-accent" />}
                <span>
                  <span className="font-medium text-ink">{f.name}</span>
                  <span className="block text-ink-2">{f.what}</span>
                  <span className="block text-[12px] text-muted">
                    {f.steps.length ? `Walkthrough: step ${f.steps.join(", ")} · ` : "Walkthrough: not shown (open the app) · "}In the app: {f.where}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

function TestCases({ guide, results }: { guide: Guide; results?: Record<string, boolean> }) {
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="data-table w-full text-[13px]">
        <thead><tr><th>Test</th><th>What it proves</th><th>Shown in steps</th>{results && <th>Result</th>}</tr></thead>
        <tbody>{guide.test_cases.map((tc) => (
          <tr key={tc.id}>
            <td className="font-semibold">{tc.id} {tc.name}</td><td>{tc.what}</td><td>{tc.steps.join(", ")}</td>
            {results && <td className={results[tc.id] ? "font-medium text-ok" : "font-medium text-bad"}>{results[tc.id] ? "PASS" : "FAIL"}</td>}
          </tr>))}</tbody>
      </table>
    </div>
  );
}

function Roles({ guide }: { guide: Guide }) {
  return (
    <div className="overflow-x-auto rounded-md border border-line bg-surface">
      <table className="data-table w-full text-[13px]">
        <thead><tr><th>Role</th><th>Login (password Demo@2026)</th><th>Their job</th></tr></thead>
        <tbody>{guide.roles.map((r) => <tr key={r.role}><td className="font-medium">{r.role}</td><td className="font-mono text-[12px]">{r.login}</td><td>{r.job}</td></tr>)}</tbody>
      </table>
    </div>
  );
}

function BuiltWith({ guide }: { guide: Guide }) {
  return (
    <dl className="grid gap-x-4 gap-y-1.5 rounded-md border border-line bg-surface p-4 text-[13px] sm:grid-cols-[8rem_1fr]">
      {guide.built_with.map(([k, v]) => <div key={k} className="contents"><dt className="font-medium text-ink">{k}</dt><dd className="text-ink-2">{v}</dd></div>)}
    </dl>
  );
}

export default function TourPage() {
  const guide = useQuery({ queryKey: ["tour"], queryFn: () => api.get<Guide>("/api/tour") });
  const [started, setStarted] = useState(false);
  const [steps, setSteps] = useState<Step[]>([]);
  const [auto, setAuto] = useState(false);
  const next = useMutation({
    mutationFn: (index: number) => api.post<Step>(`/api/tour/steps/${index}`),
    onSuccess: (s) => setSteps((prev) => [...prev.filter((p) => p.index !== s.index), s]),
    onError: (e) => { setAuto(false); toastError(e); },
  });
  const begin = (autoplay: boolean) => { setStarted(true); setSteps([]); setAuto(autoplay); next.mutate(0); };
  const finished = steps.length > 0 && steps[steps.length - 1].finished;

  useEffect(() => {
    if (steps.length) document.getElementById(`step-${steps.length - 1}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [steps.length]);
  useEffect(() => {  // "Play automatically": the next step starts a few seconds after the last one appears
    if (!auto || finished || next.isPending || !steps.length) return;
    const timer = setTimeout(() => next.mutate(steps.length), 4000);
    return () => clearTimeout(timer);
  }, [auto, finished, next, steps.length]);
  useEffect(() => { if (finished) { setAuto(false); document.getElementById("summary")?.scrollIntoView({ behavior: "smooth" }); } }, [finished]);

  const g = guide.data;
  const totalChecks = steps.reduce((n, s) => n + s.checks.length, 0);
  const passedChecks = steps.reduce((n, s) => n + s.checks.filter((c) => c.passed).length, 0);
  const seen = new Set(steps.flatMap((s) => s.features));
  const tcResults = Object.fromEntries((g?.test_cases ?? []).map((tc) => [tc.id,
    steps.filter((s) => s.proves.includes(tc.id)).every((s) => s.passed) && steps.some((s) => s.proves.includes(tc.id))]));

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-lg bg-ink text-white"><ShieldCheck className="size-5" /></div>
            <div><p className="text-[15px] font-semibold text-ink">PlaceCrew</p><p className="text-[12px] text-muted">Guided walkthrough for hackathon evaluators</p></div>
          </div>
          <ButtonLink size="sm" href="/login">Open the full app</ButtonLink>
        </div>
      </header>

      {!started ? (
        <main className="mx-auto max-w-5xl space-y-10 px-4 py-10">
          <section className="space-y-4">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-accent">Hackathon evaluators: start here</p>
            <h1 className="text-[30px] font-semibold leading-tight text-ink">A crew of AI agents runs a college placement drive. People stay in charge.</h1>
            <p className="max-w-3xl text-[15px] leading-relaxed text-ink-2">
              <strong className="text-ink">The problem:</strong> placement cells copy company emails by hand, chase students for consent,
              check CGPA in spreadsheets and email lists to companies. This leads to mistakes: wrong students shared, students shared without
              consent, clashing interviews, missed deadlines.<br />
              <strong className="text-ink">PlaceCrew:</strong> 21 agents do that work, from the company&apos;s email to the verified list the
              company receives. Every decision that matters is made by a person, and only the Approver can approve.
            </p>
            {!g ? <LoadingRows rows={2} /> : (
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <Button variant="primary" className="h-11 px-6 text-[15px]" loading={next.isPending} icon={<PlayCircle className="size-5" />}
                  onClick={() => begin(false)}>Start the walkthrough</Button>
                <Button className="h-11 px-5 text-[15px]" icon={<Play className="size-4" />} onClick={() => begin(true)}>Play it automatically</Button>
                <span className="text-[12.5px] text-muted">{g.total} steps · about 5 minutes · no login</span>
              </div>
            )}
          </section>

          <div className="grid gap-3 md:grid-cols-3">
            {[["You only press “Yes, continue”", "Each press runs the next step of a full drive. Or press “Play it automatically” and watch."],
              ["Real code, private sandbox", "The same agents, checks, approvals, uploads and emails as the app, in a sandbox made for you. No real email is sent; you see every email on screen."],
              ["Every step is proved", "Each step ends with checks read back from the database, and the last step re-runs every test case."]].map(([t, d]) => (
              <Card key={t} className="p-4"><p className="flex items-center gap-1.5 text-[14px] font-semibold text-ink"><Check className="size-4 text-ok" />{t}</p>
                <p className="mt-1 text-[13px] text-ink-2">{d}</p></Card>))}
          </div>

          <Section title="The workflow: three stages, three people" sub="Each stage is owned by one person; the agents do the routine work in between.">
            <Workflow />
          </Section>

          <Section title="How to read each step">
            <Card className="p-4">
              <ul className="grid gap-2 text-[13px] text-ink-2 sm:grid-cols-2">
                <li className="flex gap-2"><UserRound className="mt-0.5 size-4 shrink-0 text-accent" /><span><strong className="text-ink">Who acts:</strong> the person and the agents involved</span></li>
                <li className="flex gap-2"><Lightbulb className="mt-0.5 size-4 shrink-0 text-warn" /><span><strong className="text-ink">Why it matters:</strong> the real problem this step solves</span></li>
                <li className="flex gap-2"><Sparkles className="mt-0.5 size-4 shrink-0 text-accent" /><span><strong className="text-ink">What happens:</strong> the emails, lists and the agent chain (one card per agent, arrows for each hand-over)</span></li>
                <li className="flex gap-2"><CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /><span><strong className="text-ink">Proof:</strong> checks read back from the database</span></li>
                <li className="flex gap-2"><MapPin className="mt-0.5 size-4 shrink-0 text-accent" /><span><strong className="text-ink">In the app:</strong> the page and button where a person does this</span></li>
                <li className="flex gap-2"><FlaskConical className="mt-0.5 size-4 shrink-0 text-ok" /><span><strong className="text-ink">Proves TC…:</strong> the test case this step demonstrates</span></li>
              </ul>
            </Card>
          </Section>

          {g && <>
            <Section title={`The ${g.total} steps`}>
              <ol className="grid gap-1.5 text-[13.5px] text-ink-2 sm:grid-cols-2">
                {g.outline.map((o, i) => <li key={o} className="flex gap-2"><span className="w-6 shrink-0 font-semibold text-ink">{i + 1}.</span>{o}</li>)}
              </ol>
            </Section>
            <Section title="Test cases proved" sub="TC1–TC4 are the official hackathon scenarios; TC5 and TC6 cover every company email and every student case.">
              <TestCases guide={g} />
            </Section>
            <Section title={`Every feature (${g.features.length})`} sub="Where you will see each one in the walkthrough, and where it lives in the app.">
              <FeatureMap guide={g} />
            </Section>
            <Section title="Roles" sub="Want to click around yourself? These are the logins.">
              <Roles guide={g} />
            </Section>
            <Section title="How it is built"><BuiltWith guide={g} /></Section>
            <div className="flex flex-wrap gap-3">
              <Button variant="primary" className="h-11 px-6 text-[15px]" loading={next.isPending} icon={<PlayCircle className="size-5" />} onClick={() => begin(false)}>Start the walkthrough</Button>
              <Button className="h-11 px-5 text-[15px]" icon={<Play className="size-4" />} onClick={() => begin(true)}>Play it automatically</Button>
            </div>
          </>}
        </main>
      ) : !g ? <LoadingRows rows={4} /> : (
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-6 lg:grid-cols-[16rem_1fr]">
          <aside className="space-y-4 lg:sticky lg:top-4 lg:self-start">
            <div>
              <div className="mb-1 flex justify-between text-[12px] text-muted"><span>Progress</span><span>{steps.length} / {g.total}</span></div>
              <div className="h-1.5 overflow-hidden rounded-full bg-subtle"><div className="h-full bg-accent transition-all" style={{ width: `${(steps.length / g.total) * 100}%` }} /></div>
            </div>
            <ol className="space-y-0.5">
              {g.outline.map((label, i) => {
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
            {steps.length > 0 && (
              <div className="space-y-1 px-2 text-[12px] text-muted">
                <p>{passedChecks} of {totalChecks} checks passed</p>
                <p>{seen.size} of {g.features.length} features shown</p>
                <div className="flex flex-wrap gap-1 pt-1">{g.test_cases.map((tc) => {
                  const shown = steps.some((s) => s.proves.includes(tc.id));
                  return <Chip key={tc.id} tone={shown ? (tcResults[tc.id] ? "ok" : "plain") : "plain"}>{shown && tcResults[tc.id] && <Check className="size-3" />}{tc.id}</Chip>;
                })}</div>
              </div>
            )}
          </aside>

          <main className="space-y-4 pb-28">
            {steps.map((s) => <StepCard key={s.index} s={s} guide={g} />)}
            {finished && (
              <div id="summary" className="scroll-mt-4 space-y-6">
                <Card className="border-ok p-5">
                  <h2 className="text-[20px] font-semibold text-ink">{passedChecks === totalChecks ? "Every check passed" : `${passedChecks} of ${totalChecks} checks passed`}</h2>
                  <p className="mt-1 text-[13.5px] text-ink-2">
                    {totalChecks} checks across {g.total} steps, all on the real code. You saw a complete drive: company emails → drives and consent
                    forms → answers and reminders → the officer&apos;s lists → Ask PlaceCrew → the approver&apos;s forms → late qualifiers → approval →
                    verified lists emailed to both companies → the records, plus every test case run again.
                  </p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    <Button onClick={() => begin(false)}>Run the walkthrough again</Button>
                    <ButtonLink variant="primary" href="/login">Open the full app</ButtonLink>
                  </div>
                </Card>
                <Section title="Test cases"><TestCases guide={g} results={tcResults} /></Section>
                <Section title={`Features: ${seen.size} of ${g.features.length} shown in the walkthrough`} sub="The rest are in the app; each card says where.">
                  <FeatureMap guide={g} seen={seen} />
                </Section>
                <Section title="Try it yourself" sub="Open the full app and sign in as any role. Every staff page has the Ask PlaceCrew button (bottom right).">
                  <Roles guide={g} />
                </Section>
                <Section title="How it is built"><BuiltWith guide={g} /></Section>
              </div>
            )}
          </main>
        </div>
      )}

      {started && g && !finished && (
        <div className="fixed inset-x-0 bottom-0 border-t border-line bg-surface/95 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
            <p className="text-[13px] text-ink-2">
              {next.isPending ? "The agents are working on this step…" : <>Next: <strong>{g.outline[steps.length]}</strong>{auto && " (starts by itself)"}</>}
            </p>
            <div className="flex gap-2">
              <Button className="h-11" icon={auto ? <Pause className="size-4" /> : <Play className="size-4" />} onClick={() => setAuto(!auto)}>
                {auto ? "Pause" : "Play automatically"}
              </Button>
              <Button variant="primary" className="h-11 px-6 text-[15px]" loading={next.isPending} icon={<ArrowRight className="size-5" />}
                onClick={() => next.mutate(steps.length)}>Yes, continue</Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
