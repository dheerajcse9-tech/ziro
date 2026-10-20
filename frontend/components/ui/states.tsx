import { AlertTriangle, Inbox, RotateCw } from "lucide-react";
import * as React from "react";
import { ApiError } from "@/lib/api";
import { Button, Skeleton } from "./primitives";

export function EmptyState({ title, description, action, icon }: { title: string; description?: React.ReactNode; action?: React.ReactNode; icon?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-12 text-center">
      <div className="mb-3 flex size-10 items-center justify-center rounded-full bg-subtle text-faint ring-1 ring-line">{icon ?? <Inbox className="size-5" />}</div>
      <p className="text-sm font-semibold text-ink">{title}</p>
      {description && <p className="mt-1 max-w-md text-[13px] text-muted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Something went wrong";
  const status = error instanceof ApiError ? error.status : undefined;
  return (
    <div role="alert" className="flex items-start gap-3 rounded-md border border-bad/25 bg-bad-soft px-4 py-3 text-sm text-bad">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div className="flex-1">
        <p className="font-semibold">{status ? `Error ${status}` : "Error"}</p>
        <p className="text-[13px]">{message}</p>
      </div>
      {onRetry && <Button size="sm" variant="secondary" onClick={onRetry} icon={<RotateCw className="size-3.5" />}>Retry</Button>}
    </div>
  );
}

export function LoadingRows({ rows = 6 }: { rows?: number }) {
  return (
    <div className="space-y-2 p-4" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => <Skeleton key={i} className="h-7 w-full" />)}
    </div>
  );
}

export function Banner({ tone = "warn", title, children, action }: { tone?: "warn" | "bad" | "info" | "ok"; title: string; children?: React.ReactNode; action?: React.ReactNode }) {
  const styles = {
    warn: "border-warn/25 bg-warn-soft text-warn",
    bad: "border-bad/25 bg-bad-soft text-bad",
    info: "border-info/20 bg-info-soft text-info",
    ok: "border-ok/20 bg-ok-soft text-ok",
  }[tone];
  return (
    <div className={`flex items-start gap-3 rounded-md border px-4 py-3 ${styles}`}>
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1 text-[13px]">
        <p className="font-semibold">{title}</p>
        {children && <div className="mt-0.5 text-ink-2">{children}</div>}
      </div>
      {action}
    </div>
  );
}
