"use client";

import { Check, ChevronDown, ChevronRight, Copy } from "lucide-react";
import * as React from "react";
import { cn } from "@/lib/format";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [done, setDone] = React.useState(false);
  return (
    <button type="button" aria-label={label} title={label}
      onClick={async () => { await navigator.clipboard?.writeText(value); setDone(true); setTimeout(() => setDone(false), 1200); }}
      className="inline-flex items-center rounded p-1 text-faint hover:bg-subtle hover:text-ink">
      {done ? <Check className="size-3.5 text-ok" /> : <Copy className="size-3.5" />}
    </button>
  );
}

export function HashValue({ value, n = 12 }: { value: string | null | undefined; n?: number }) {
  if (!value) return <span className="text-faint">—</span>;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-[12px] text-ink-2" title={value}>
      {value.slice(0, n)}…<CopyButton value={value} label="Copy full hash" />
    </span>
  );
}

/** Collapsible JSON viewer for raw tool inputs/outputs and evidence. */
export function JsonView({ value, label = "Raw data", defaultOpen = false, maxHeight = 360 }: { value: unknown; label?: string; defaultOpen?: boolean; maxHeight?: number }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const text = React.useMemo(() => JSON.stringify(value, null, 2), [value]);
  return (
    <div className="rounded-md border border-line bg-subtle">
      <div className="flex items-center justify-between px-2 py-1">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          className="inline-flex items-center gap-1 text-[12px] font-medium text-muted hover:text-ink">
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}{label}
        </button>
        <CopyButton value={text} label="Copy JSON" />
      </div>
      {open && (
        <pre className={cn("overflow-auto border-t border-line px-3 py-2 font-mono text-[11.5px] leading-relaxed text-ink-2")} style={{ maxHeight }}>{text}</pre>
      )}
    </div>
  );
}

export function RollList({ rolls, tone = "neutral", empty = "None" }: { rolls: string[]; tone?: "neutral" | "bad" | "ok" | "warn"; empty?: string }) {
  if (!rolls.length) return <span className="text-[13px] text-faint">{empty}</span>;
  const cls = { neutral: "bg-subtle text-ink-2 ring-line", bad: "bg-bad-soft text-bad ring-bad/20", ok: "bg-ok-soft text-ok ring-ok/20", warn: "bg-warn-soft text-warn ring-warn/20" }[tone];
  return (
    <div className="flex flex-wrap gap-1">
      {rolls.map((r, i) => <span key={`${r}-${i}`} className={cn("rounded px-1.5 py-0.5 font-mono text-[11.5px] ring-1 ring-inset", cls)}>{r}</span>)}
    </div>
  );
}
