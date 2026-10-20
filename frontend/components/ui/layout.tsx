import { ChevronRight } from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { cn } from "@/lib/format";

export function PageHeader({ title, description, actions, crumbs, meta }: {
  title: React.ReactNode; description?: React.ReactNode; actions?: React.ReactNode;
  crumbs?: { label: string; href?: string }[]; meta?: React.ReactNode;
}) {
  return (
    <div className="mb-5">
      {crumbs && crumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-1.5 flex flex-wrap items-center gap-1 text-xs text-muted">
          {crumbs.map((c, i) => (
            <span key={i} className="flex items-center gap-1">
              {c.href ? <Link href={c.href} className="hover:text-ink hover:underline">{c.label}</Link> : <span>{c.label}</span>}
              {i < crumbs.length - 1 && <ChevronRight className="size-3" />}
            </span>
          ))}
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
          {description && <p className="mt-1 max-w-3xl text-[13px] text-muted">{description}</p>}
          {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}

export function KpiCard({ label, value, hint, href, tone = "neutral", icon }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; href?: string; tone?: "neutral" | "warn" | "bad" | "ok" | "accent"; icon?: React.ReactNode;
}) {
  const accent = { neutral: "text-ink", warn: "text-warn", bad: "text-bad", ok: "text-ok", accent: "text-accent" }[tone];
  const body = (
    <div className="flex h-full flex-col justify-between gap-2 rounded-[var(--radius-card)] border border-line bg-surface px-4 py-3 shadow-[var(--shadow-card)] transition-colors group-hover:border-line-strong group-hover:bg-subtle">
      <div className="flex items-center justify-between gap-2 text-[12px] font-medium text-muted">
        <span>{label}</span>
        <span className="text-faint">{icon}</span>
      </div>
      <div className={cn("text-2xl font-semibold tabular", accent)}>{value}</div>
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
  return href ? <Link href={href} className="group block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent rounded-[var(--radius-card)]">{body}</Link> : <div className="group">{body}</div>;
}

export function DefinitionList({ items, cols = 2 }: { items: { label: string; value: React.ReactNode }[]; cols?: 1 | 2 | 3 }) {
  return (
    <dl className={cn("grid gap-x-6 gap-y-3", cols === 1 ? "grid-cols-1" : cols === 2 ? "grid-cols-1 sm:grid-cols-2" : "grid-cols-1 sm:grid-cols-3")}>
      {items.map((it) => (
        <div key={it.label} className="min-w-0">
          <dt className="text-[11px] font-medium uppercase tracking-wide text-faint">{it.label}</dt>
          <dd className="mt-0.5 break-words text-[13px] text-ink">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
