"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Clock, Link2, LogOut, Mail, Save, ShieldCheck } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { DefinitionList } from "@/components/ui/layout";
import { Button, Card, CardHeader, Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { Banner, LoadingRows } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import type { GoogleStatus, InboxResponse } from "@/lib/types";

interface AgentPolicy { auto_apply_student_data: boolean; reminder_hours_before_deadline: number; reminder_hours_before_drive: number }

export function GmailCard() {
  const me = useMe();
  const qc = useQueryClient();
  const params = useSearchParams();
  const q = useQuery({ queryKey: ["google"], queryFn: () => api.get<GoogleStatus>("/api/integrations/google") });
  const canConnect = can(me.data, "connectMailbox");

  useEffect(() => {
    const result = params.get("google");
    if (result === "connected") toast.success(`Gmail connected: ${params.get("account") ?? ""}`);
    if (result === "error") toast.error("Gmail was not connected", { description: params.get("message") ?? undefined });
    if (result) window.history.replaceState(null, "", "/settings");
  }, [params]);

  const connect = useMutation({
    mutationFn: () => api.post<{ auth_url: string }>("/api/integrations/google/connect"),
    onSuccess: (r) => { window.location.href = r.auth_url; },
    onError: toastError,
  });
  const disconnect = useMutation({
    mutationFn: () => api.post<GoogleStatus>("/api/integrations/google/disconnect"),
    onSuccess: () => { toast.success("Gmail disconnected"); qc.invalidateQueries(); },
    onError: toastError,
  });
  const g = q.data;
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Mail className="size-4 text-faint" />Placement mailbox (Gmail)</span>}
        description="The drive upload officer's Gmail: company emails are read from it, and every email PlacementOS sends goes out from it."
        actions={g && canConnect ? (g.connected
          ? <Button size="sm" loading={disconnect.isPending} onClick={() => disconnect.mutate()} icon={<LogOut className="size-3.5" />}>Disconnect</Button>
          : <Button size="sm" variant="primary" disabled={!g.configured} loading={connect.isPending} onClick={() => connect.mutate()} icon={<Link2 className="size-3.5" />}>Connect Gmail</Button>) : undefined} />
      {!g ? <LoadingRows rows={3} /> : (
        <div className="space-y-3 p-4">
          {!g.configured && (
            <Banner tone="info" title="Google sign-in is not set up yet">
              Add <span className="font-mono">GOOGLE_CLIENT_ID</span> and <span className="font-mono">GOOGLE_CLIENT_SECRET</span> to <span className="font-mono">backend/.env</span> and restart the backend.
              In Google Cloud, the OAuth client&apos;s authorised redirect URI must be exactly <span className="break-all font-mono">{g.redirect_uri}</span>.
            </Banner>
          )}
          {g.status === "ERROR" && <Banner tone="bad" title="Gmail needs to be reconnected">{g.last_error}</Banner>}
          <DefinitionList items={[
            { label: "Status", value: g.connected ? <StatusBadge status="VERIFIED" label="Connected" /> : <StatusBadge status="DRAFT" label="Not connected" /> },
            { label: "Account", value: g.account_email ?? "—" },
            { label: "Permissions", value: "Read email (read-only) · Send email" },
            { label: "Connected", value: g.connected_at ? fmtDateTime(g.connected_at) : "—" },
            { label: "Redirect URI", value: <span className="break-all font-mono text-[12px]">{g.redirect_uri}</span> },
          ]} />
          {!canConnect && <p className="text-[12px] text-muted">Only the drive upload officer or an admin can connect the mailbox.</p>}
        </div>
      )}
    </Card>
  );
}

const WINDOWS: [number, string][] = [[30, "Last 30 minutes"], [60, "Last 1 hour"], [360, "Last 6 hours"], [1440, "Last 24 hours"],
  [10080, "Last 7 days"], [43200, "Last 30 days"]];

/** How far back the Mailbox Agent reads company emails. */
export function MailWindowCard() {
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["inbox", ""], queryFn: () => api.get<InboxResponse>("/api/inbox") });
  const editable = me.data?.role === "coordinator" || me.data?.role === "admin";
  const save = useMutation({
    mutationFn: (minutes: number) => api.put<{ window_minutes: number }>("/api/inbox-settings/window", { minutes }),
    onSuccess: (r) => { toast.success(`Reading company emails from the ${WINDOWS.find(([m]) => m === r.window_minutes)?.[1].toLowerCase()}`); qc.invalidateQueries({ queryKey: ["inbox"] }); },
    onError: toastError,
  });
  const box = q.data?.mailbox;
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><Clock className="size-4 text-faint" />Which company emails to read</span>}
        description="The Mailbox Agent only reads emails received in this time. Older emails are ignored." />
      <div className="space-y-2 p-4">
        <Field label="Read emails from" htmlFor="mail_window">
          <Select id="mail_window" className="w-full" disabled={!editable || save.isPending || !box}
            value={String(box?.window_minutes ?? 60)} onChange={(e) => save.mutate(Number(e.target.value))}>
            {WINDOWS.map(([m, label]) => <option key={m} value={m}>{label}</option>)}
          </Select>
        </Field>
        {box?.read_since && <p className="text-[12px] text-muted">Emails received before the last reset ({fmtDateTime(box.read_since)}) are always ignored.</p>}
      </div>
    </Card>
  );
}

export function TrustedSendersCard() {
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["inbox", ""], queryFn: () => api.get<InboxResponse>("/api/inbox") });
  const [text, setText] = useState("");
  useEffect(() => { if (q.data) setText(q.data.trusted_senders.join("\n")); }, [q.data]);
  const editable = me.data?.role === "coordinator" || me.data?.role === "admin";
  const save = useMutation({
    mutationFn: () => api.put<{ trusted_senders: string[] }>("/api/inbox-settings/trusted-senders", { senders: text.split(/[\s,;]+/).filter(Boolean) }),
    onSuccess: (r) => { toast.success(`${r.trusted_senders.length} trusted sender(s) saved`); qc.invalidateQueries({ queryKey: ["inbox"] }); },
    onError: toastError,
  });
  return (
    <Card>
      <CardHeader title={<span className="flex items-center gap-2"><ShieldCheck className="size-4 text-faint" />Trusted company senders</span>}
        description="Emails from these addresses skip the 'new company' confirmation. Every other check still runs." />
      <div className="space-y-3 p-4">
        <Field label="One email address per line" htmlFor="trusted" hint="Companies registered with a contact email are already trusted for that address.">
          <Textarea id="trusted" rows={4} disabled={!editable} value={text} onChange={(e) => setText(e.target.value)} placeholder="hr@company.com" />
        </Field>
        {editable && <Button variant="primary" size="sm" loading={save.isPending} onClick={() => save.mutate()} icon={<Save className="size-3.5" />}>Save</Button>}
      </div>
    </Card>
  );
}

export function AgentPolicyCard() {
  const me = useMe();
  const qc = useQueryClient();
  const isAdmin = can(me.data, "admin");
  const q = useQuery({ queryKey: ["settings"], queryFn: () => api.get<AgentPolicy & Record<string, unknown>>("/api/settings") });
  const [form, setForm] = useState<AgentPolicy | null>(null);
  useEffect(() => {
    if (q.data) setForm({ auto_apply_student_data: !!q.data.auto_apply_student_data, reminder_hours_before_deadline: Number(q.data.reminder_hours_before_deadline),
      reminder_hours_before_drive: Number(q.data.reminder_hours_before_drive) });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.patch("/api/settings", { ...form } as unknown as Record<string, unknown>),
    onSuccess: () => { toast.success("Agent rules saved"); qc.invalidateQueries({ queryKey: ["settings"] }); },
    onError: toastError,
  });
  return (
    <Card>
      <CardHeader title="Agent rules" description={isAdmin ? "How the stage 3 agents treat students' answers and reminders." : "Read-only for your role."} />
      {!form ? <LoadingRows rows={3} /> : (
        <div className="space-y-3 p-4">
          <label className="flex items-start gap-2 text-[13px]">
            <input type="checkbox" className="mt-0.5" disabled={!isAdmin} checked={form.auto_apply_student_data}
              onChange={(e) => setForm({ ...form, auto_apply_student_data: e.target.checked })} />
            <span><strong>Response Reader applies students&apos; answers automatically</strong><br />
              <span className="text-muted">Values are validated, marked “self-reported” and shown to the approver. When off, an officer verifies each answer in the Exception Inbox before it is applied.</span></span>
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Remind about open forms (hours before closing)" htmlFor="rh1"><Input id="rh1" type="number" min={1} max={168} disabled={!isAdmin} value={form.reminder_hours_before_deadline} onChange={(e) => setForm({ ...form, reminder_hours_before_deadline: Number(e.target.value) })} /></Field>
            <Field label="Remind shortlisted students (hours before the drive)" htmlFor="rh2"><Input id="rh2" type="number" min={1} max={168} disabled={!isAdmin} value={form.reminder_hours_before_drive} onChange={(e) => setForm({ ...form, reminder_hours_before_drive: Number(e.target.value) })} /></Field>
          </div>
          {isAdmin && <Button variant="primary" size="sm" loading={save.isPending} onClick={() => save.mutate()} icon={<CheckCircle2 className="size-3.5" />}>Save agent rules</Button>}
        </div>
      )}
    </Card>
  );
}
