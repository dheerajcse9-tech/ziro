import { cn, titleCase } from "@/lib/format";

type Tone = "ok" | "warn" | "bad" | "info" | "hold" | "neutral" | "accent";

const tones: Record<Tone, string> = {
  ok: "bg-ok-soft text-ok ring-ok/20",
  warn: "bg-warn-soft text-warn ring-warn/25",
  bad: "bg-bad-soft text-bad ring-bad/20",
  info: "bg-info-soft text-info ring-info/20",
  hold: "bg-hold-soft text-hold ring-hold/20",
  neutral: "bg-subtle text-muted ring-line-strong",
  accent: "bg-accent-soft text-accent ring-accent/20",
};

const STATUS_TONE: Record<string, Tone> = {
  // decisions / roster
  ELIGIBLE: "ok", INCLUDED: "ok", INELIGIBLE: "bad", EXCLUDED: "neutral", UNKNOWN: "warn", HELD: "hold",
  // runs
  VERIFIED: "ok", COMPLETED: "ok", AWAITING_APPROVAL: "accent", READY_FOR_APPROVAL: "accent", APPROVED: "info",
  NEEDS_REVIEW: "warn", PENDING_RECONCILIATION: "warn", FAILED: "bad", FAILED_VERIFICATION: "bad", REJECTED: "neutral",
  EXECUTING: "info", VALIDATING_INPUTS: "info", RETRIEVING_EVIDENCE: "info", INTERPRETING_CRITERIA: "info",
  EVALUATING_ROSTER: "info", CHECKING_CONFLICTS: "info", DRAFT: "neutral",
  // approvals / proposals
  PENDING: "accent", PENDING_APPROVAL: "accent", INVALIDATED: "warn", CONSUMED: "ok", EXECUTED: "ok",
  // uploads / verification
  SUBMITTED: "ok", UNCERTAIN: "warn", IN_PROGRESS: "info", BLOCKED: "bad", PENDING_VERIFICATION: "warn",
  // drives
  OPEN: "info", ROSTER_SUBMITTED: "ok", CLOSED: "neutral", CANCELLED: "neutral",
  // exceptions
  RESOLVED: "ok", DISMISSED: "neutral", BLOCKING: "bad", WARNING: "warn", INFO: "info",
  // tests
  PASS: "ok", FAIL: "bad",
  // consent
  GRANTED: "ok", REVOKED: "bad",
  // rule outcomes
  NOT_APPLICABLE: "neutral",
};

export function StatusBadge({ status, label, className, tone }: { status: string; label?: string; className?: string; tone?: Tone }) {
  const t = tone ?? STATUS_TONE[status] ?? "neutral";
  return (
    <span className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1 ring-inset", tones[t], className)}>
      <span className={cn("size-1.5 rounded-full bg-current opacity-70")} aria-hidden />
      {label ?? titleCase(status)}
    </span>
  );
}

export function Tag({ children, tone = "neutral", className }: { children: React.ReactNode; tone?: Tone; className?: string }) {
  return <span className={cn("inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset", tones[tone], className)}>{children}</span>;
}

export function PlannerBadge({ mode }: { mode: string }) {
  if (mode === "LLM") return <Tag tone="accent">LLM planner</Tag>;
  if (mode === "PENDING") return <Tag>Planning…</Tag>;
  if (mode.startsWith("LLM_FAILED")) return <Tag tone="warn">LLM failed → deterministic fallback</Tag>;
  return <Tag tone="neutral">Deterministic fallback planner</Tag>;
}

export function MockBadge() {
  return <Tag tone="warn">LOCAL MOCK destination</Tag>;
}
