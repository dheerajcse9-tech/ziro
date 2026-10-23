"use client";

// Ask PlaceCrew: the staff's conversational agent. It looks things up with read-only tools and can only PROPOSE
// actions; the person confirms each one here, and the normal role checks apply.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUp, Bot, Check, ChevronDown, Search, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/primitives";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { cn } from "@/lib/format";
import type { AgentRun, Drive, Role } from "@/lib/types";

interface Step { tool: string; label: string; args: Record<string, unknown>; ok: boolean }
interface Proposal {
  action: "prepare_list" | "send_personal_forms" | "check_mailbox"; label: string; reason: string;
  drive_id?: number; drive?: string; closes_at_local?: string; students?: number; objective?: string;
}
interface Answer { answer: string; steps: Step[]; proposals: Proposal[]; mode: "AI" | "RULES"; model?: string; ai_error?: string; ai_error_detail?: string | null }
interface Message { role: "user" | "assistant"; text: string; reply?: Answer }

const PERMISSION: Record<Proposal["action"], string> = { prepare_list: "runAgent", send_personal_forms: "followUp", check_mailbox: "manageDrives" };

function suggestions(role: Role | undefined, drive?: string): string[] {
  const d = drive ?? "the next drive";
  if (role === "approver") return ["What is waiting for my approval?", `Who could still qualify for ${d}?`, `Send personal forms for ${d}`];
  if (role === "coordinator") return ["Check the mailbox for new company emails", "Which drives are open?", `How is ${d} going?`];
  return [`How is ${d} going?`, `What if ${d} lowered the CGPA to 6.5?`, `Who still needs attention for ${d}?`];
}

function ProposalCard({ p }: { p: Proposal }) {
  const me = useMe();
  const qc = useQueryClient();
  const [result, setResult] = useState<React.ReactNode>(null);
  const allowed = can(me.data, PERMISSION[p.action]);
  const go = useMutation({
    mutationFn: async () => {
      if (p.action === "prepare_list") {
        const run = await api.post<AgentRun>("/api/agent/runs", { objective: p.objective });
        return <>Started — <Link className="underline" href={`/agent/${run.id}`}>see the agents work</Link>.</>;
      }
      if (p.action === "send_personal_forms") {
        const run = await api.post<AgentRun>(`/api/drives/${p.drive_id}/followups`, { closes_at: p.closes_at_local });
        return <>{run.summary ?? "Forms sent."} <Link className="underline" href={`/agent/${run.id}`}>Details</Link></>;
      }
      const r = await api.post<{ mailbox: { message: string } }>("/api/inbox/check");
      return <>{r.mailbox.message}</>;
    },
    onSuccess: (node) => { setResult(node); qc.invalidateQueries(); toast.success("Done"); },
    onError: toastError,
  });
  return (
    <div className="rounded-lg border border-accent/40 bg-accent-soft/40 p-3">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-accent">Proposed action — needs your OK</p>
      <p className="mt-1 text-[13px] font-medium text-ink">{p.label}{p.drive ? ` · ${p.drive}` : ""}</p>
      {p.reason && <p className="text-[12.5px] text-ink-2">{p.reason}</p>}
      {p.action === "send_personal_forms" && <p className="text-[12px] text-muted">{p.students ?? 0} student(s) · forms close {p.closes_at_local?.replace("T", " ")}</p>}
      {result ? <p className="mt-2 flex items-start gap-1.5 text-[12.5px] text-ok"><Check className="mt-0.5 size-3.5 shrink-0" /><span>{result}</span></p>
        : allowed ? <Button size="sm" variant="primary" className="mt-2" loading={go.isPending} onClick={() => go.mutate()}>Confirm</Button>
          : <p className="mt-2 text-[12px] text-muted">Your role can’t do this; the right person will see the same suggestion.</p>}
    </div>
  );
}

function AssistantMessage({ a }: { a: Answer }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-2">
      <p className="whitespace-pre-wrap text-[13.5px] leading-relaxed text-ink">{a.answer}</p>
      {a.proposals.map((p, i) => <ProposalCard key={i} p={p} />)}
      <div className="flex flex-wrap items-center gap-2 text-[11.5px] text-muted">
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-medium", a.mode === "AI" ? "bg-accent-soft text-accent" : "bg-subtle text-muted")}>
          {a.mode === "AI" ? <><Sparkles className="size-3" />Gemini</> : "Built-in rules"}
        </span>
        {a.mode === "RULES" && a.ai_error && <span title={a.ai_error_detail ?? undefined}>AI not used: {a.ai_error}{a.ai_error_detail ? " (hover for details)" : ""}</span>}
        {a.steps.length > 0 && (
          <button type="button" onClick={() => setOpen(!open)} className="inline-flex items-center gap-0.5 hover:text-ink">
            {a.steps.length} lookup{a.steps.length === 1 ? "" : "s"}<ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />
          </button>
        )}
      </div>
      {open && (
        <ol className="space-y-1 rounded-md bg-subtle p-2 text-[12px] text-ink-2">
          {a.steps.map((s, i) => (
            <li key={i} className="flex items-start gap-1.5"><Search className="mt-0.5 size-3 shrink-0 text-faint" />
              <span>{s.label}{Object.keys(s.args).length > 0 && <span className="text-muted"> · {Object.entries(s.args).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ")}</span>}</span>
            </li>))}
        </ol>
      )}
    </div>
  );
}

export function AskPanel() {
  const me = useMe();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const end = useRef<HTMLDivElement>(null);
  const drives = useQuery({ queryKey: ["drives", ""], queryFn: () => api.get<Drive[]>("/api/drives"), enabled: open });
  const ask = useMutation({
    mutationFn: (question: string) => api.post<Answer>("/api/assistant/ask", {
      question, history: messages.slice(-8).map((m) => ({ role: m.role, text: m.role === "user" ? m.text : m.reply?.answer ?? "" })),
    }),
    onSuccess: (reply, question) => setMessages((m) => [...m, { role: "user", text: question }, { role: "assistant", text: reply.answer, reply }]),
    onError: toastError,
  });
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.length, ask.isPending]);
  if (!me.data || me.data.role === "viewer") return null;
  const firstDrive = drives.data?.find((d) => d.status === "OPEN")?.employer.name;
  const send = (q: string) => { if (q.trim().length >= 2 && !ask.isPending) { ask.mutate(q.trim()); setText(""); } };

  return (
    <>
      {!open && (
        <button type="button" onClick={() => setOpen(true)}
          className="fixed bottom-5 right-5 z-40 inline-flex items-center gap-2 rounded-full bg-ink px-4 py-3 text-[14px] font-semibold text-white shadow-lg ring-1 ring-white/10 hover:bg-ink/90">
          <Sparkles className="size-4" />Ask PlaceCrew
        </button>
      )}
      {open && (
        <section aria-label="Ask PlaceCrew" className="fixed inset-y-0 right-0 z-50 flex w-full max-w-[440px] flex-col border-l border-line bg-surface shadow-2xl">
          <header className="flex items-center justify-between gap-2 border-b border-line px-4 py-3">
            <div className="flex items-center gap-2">
              <span className="flex size-8 items-center justify-center rounded-full bg-ink text-white"><Bot className="size-4" /></span>
              <div className="leading-tight"><p className="text-[14px] font-semibold text-ink">Ask PlaceCrew</p>
                <p className="text-[11.5px] text-muted">Looks things up on live data · proposes, never acts alone</p></div>
            </div>
            <button type="button" onClick={() => setOpen(false)} className="rounded p-1.5 text-muted hover:bg-subtle" aria-label="Close"><X className="size-4" /></button>
          </header>
          <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {messages.length === 0 && (
              <div className="space-y-3">
                <p className="text-[13px] text-ink-2">Ask about any drive or student, in your own words. For example:</p>
                <div className="flex flex-col gap-2">
                  {suggestions(me.data.role, firstDrive).map((s) => (
                    <button key={s} type="button" onClick={() => send(s)}
                      className="rounded-lg border border-line bg-subtle px-3 py-2 text-left text-[13px] text-ink-2 hover:border-accent hover:bg-surface">{s}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => m.role === "user"
              ? <div key={i} className="ml-8 rounded-2xl rounded-br-sm bg-ink px-3 py-2 text-[13.5px] text-white">{m.text}</div>
              : <div key={i} className="mr-4">{m.reply && <AssistantMessage a={m.reply} />}</div>)}
            {ask.isPending && <p className="flex items-center gap-2 text-[13px] text-muted"><Sparkles className="size-3.5 animate-pulse text-accent" />Looking it up…</p>}
            <div ref={end} />
          </div>
          <form className="flex items-end gap-2 border-t border-line p-3" onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <textarea aria-label="Ask a question" rows={2} value={text} onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(text); } }}
              placeholder="e.g. Why isn’t Farah on the Nimbus list?"
              className="min-h-[44px] flex-1 resize-none rounded-lg border border-line bg-subtle px-3 py-2 text-[13.5px] placeholder:text-faint focus:border-accent focus:bg-surface focus:outline-none" />
            <Button type="submit" variant="primary" aria-label="Send" loading={ask.isPending} disabled={text.trim().length < 2} icon={<ArrowUp className="size-4" />} />
          </form>
        </section>
      )}
    </>
  );
}
