"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Clock, Cpu, Play, Sparkles, Zap } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader } from "@/components/ui/primitives";
import { Banner, ErrorState, LoadingRows } from "@/components/ui/states";
import { Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, ROLE_LABEL, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { relTime, titleCase } from "@/lib/format";
import type { CrewAgent, CrewResponse } from "@/lib/types";

const TRIGGER: Record<CrewAgent["trigger"], { label: string; icon: React.ReactNode }> = {
  workflow: { label: "Workflow step", icon: <Bot className="size-3" /> },
  autopilot: { label: "Runs on autopilot", icon: <Clock className="size-3" /> },
  event: { label: "Runs when a student answers", icon: <Zap className="size-3" /> },
};

function AgentCard({ a }: { a: CrewAgent }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border border-line bg-surface p-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[13px] font-semibold text-ink">{a.name}</p>
        {a.uses_ai && <Tag tone="accent"><Sparkles className="mr-1 size-3" />AI</Tag>}
      </div>
      <p className="text-[12.5px] leading-snug text-ink-2">{a.purpose}</p>
      <div className="flex flex-wrap gap-1">
        <Tag>{TRIGGER[a.trigger].icon}<span className="ml-1">{TRIGGER[a.trigger].label}</span></Tag>
        <Tag tone="info">Works for: {ROLE_LABEL[a.owner]}</Tag>
      </div>
      {a.recent.length > 0 && (
        <ul className="mt-1 space-y-0.5 border-t border-line pt-2 text-[11.5px] text-muted">
          {a.recent.slice(0, 3).map((r, i) => (
            <li key={i} className="flex justify-between gap-2">
              <span className="truncate">{r.run_id ? <Link className="text-accent hover:underline" href={`/agent/${r.run_id}`}>{r.what || "View task"}</Link> : (r.what || titleCase(r.action ?? ""))}</span>
              <span className="shrink-0 text-faint">{relTime(r.at)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function AgentsPage() {
  const qc = useQueryClient();
  const me = useMe();
  const q = useQuery({ queryKey: ["crew"], queryFn: () => api.get<CrewResponse>("/api/agents"), refetchInterval: 15_000 });
  const tick = useMutation({
    mutationFn: () => api.post<any>("/api/autopilot/run"),
    onSuccess: (r) => {
      if (r.skipped) toast.info(r.skipped);
      else toast.success("Background agents ran", { description: r.mailbox?.message ?? r.error });
      qc.invalidateQueries();
    },
    onError: toastError,
  });
  const d = q.data;
  return (
    <>
      <PageHeader title="Agents"
        description="The helpers that do the routine work for each person. They never approve anything: the Approver approves every list before it reaches a company."
        actions={can(me.data, "manageDrives") || can(me.data, "approve") ? <Button variant="primary" icon={<Play className="size-4" />} loading={tick.isPending} onClick={() => tick.mutate()}>Run background agents now</Button> : undefined} />
      {q.isError ? <ErrorState error={q.error} /> : !d ? <LoadingRows /> : (
        <>
          <div className="mb-4 grid gap-3 md:grid-cols-3">
            <Banner tone={d.ai.provider === "none" ? "info" : "ok"} title={d.ai.provider === "none" ? "AI: off (rules only)" : `AI: ${d.ai.provider === "gemini" ? "Gemini" : "Claude"}`}>
              {d.ai.provider === "none" ? "Emails are read with built-in rules. An admin can add a Gemini key to use AI." : "Used to read company emails and requests; every decision is still checked by rules."}
            </Banner>
            <Banner tone={d.mailbox.source ? "ok" : "info"} title={d.mailbox.source ? `Mailbox: ${d.mailbox.address}` : "Mailbox: not connected"}>
              {d.mailbox.source ? `Reads emails whose subject contains “${d.mailbox.filter || "anything"}”` : "An admin connects Gmail in Settings."}
            </Banner>
            <Banner tone={d.autopilot.enabled ? (d.autopilot.last_error ? "warn" : "ok") : "info"} title={d.autopilot.enabled ? `Checks automatically every ${Math.max(1, Math.round(d.autopilot.interval_seconds / 60))} min` : "Automatic checks: off"}>
              {d.autopilot.last_error ?? (d.autopilot.last_tick_at ? `Last run ${relTime(d.autopilot.last_tick_at)}` : "Not run yet")}
            </Banner>
          </div>
          <div className="space-y-5">
            {[1, 2, 3].map((stage) => (
              <Card key={stage}>
                <CardHeader title={<span className="flex items-center gap-2"><Cpu className="size-4 text-faint" />Stage {stage}: {d.stages[String(stage)]}</span>}
                  description={{ 1: "Company email → drive → consent form → every student", 2: "Criteria + consent → roster proposal for the approver",
                    3: "Review → personal forms for missing details/consent → updates → upload → emails" }[stage]} />
                <div className="grid gap-3 p-4 sm:grid-cols-2 xl:grid-cols-3">
                  {d.agents.filter((a) => a.stage === stage).map((a) => <AgentCard key={a.key} a={a} />)}
                </div>
              </Card>
            ))}
          </div>
        </>
      )}
    </>
  );
}
