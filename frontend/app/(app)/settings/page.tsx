"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, RotateCcw, Save, Trash2, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { DefinitionList, PageHeader } from "@/components/ui/layout";
import { ConfirmDialog } from "@/components/ui/overlay";
import { Button, Card, CardHeader, Field, Input, Select } from "@/components/ui/primitives";
import { Banner, ErrorState, LoadingRows } from "@/components/ui/states";
import { MockBadge, StatusBadge, Tag } from "@/components/ui/status";
import { api } from "@/lib/api";
import { can, ROLE_LABEL, useMe } from "@/lib/auth";
import { toastError } from "@/lib/errors";
import { fmtDateTime } from "@/lib/format";
import type { Destination, Health, User } from "@/lib/types";

interface Integrations {
  official_organiser_target: { available: boolean; note: string };
  destinations: (Destination & { status: string; detail: string | null })[];
  allowed_destination_hosts: string[];
  llm: { enabled: boolean; model: string | null; mode: string };
  mock_portal_base_url: string;
  demo_mode: boolean;
}
interface AppSettings { institution_name: string; timezone: string; schedule_buffer_minutes: number; attendance_retention_days: number; attendance_fields: string[] }

const FAULTS = [
  ["NONE", "No fault"], ["TIMEOUT_AFTER_COMMIT", "Timeout after commit (tests reconciliation)"], ["UNAVAILABLE", "503 before commit (tests safe retry)"],
  ["DROP_LAST", "Silently drop last candidate (partial upload)"], ["DUPLICATE_FIRST", "Duplicate first candidate"],
  ["INJECT_EXTRA", "Inject an unexpected record"], ["READBACK_UNAVAILABLE", "Roster page unavailable (read-back fails)"],
];

export default function SettingsPage() {
  const me = useMe();
  const qc = useQueryClient();
  const isAdmin = can(me.data, "admin");
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<Health>("/api/health") });
  const integ = useQuery({ queryKey: ["integrations"], queryFn: () => api.get<Integrations>("/api/integrations") });
  const settings = useQuery({ queryKey: ["settings"], queryFn: () => api.get<AppSettings>("/api/settings") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/api/users"), enabled: !!me.data && me.data.role !== "viewer" });
  const [form, setForm] = useState<AppSettings | null>(null);
  useEffect(() => { if (settings.data) setForm(settings.data); }, [settings.data]);
  const [fault, setFault] = useState({ form_ref: "", mode: "TIMEOUT_AFTER_COMMIT" });
  const [resetOpen, setResetOpen] = useState(false);

  const save = useMutation({
    mutationFn: () => api.patch<AppSettings>("/api/settings", { ...form } as unknown as Record<string, unknown>),
    onSuccess: () => { toast.success("Settings saved"); qc.invalidateQueries(); }, onError: toastError,
  });
  const toggleDest = useMutation({
    mutationFn: (d: Destination) => api.patch<Destination>(`/api/destinations/${d.id}`, { is_enabled: !d.is_enabled }),
    onSuccess: (d) => { toast.success(`${d.name} ${d.is_enabled ? "enabled" : "disabled"} (config v${d.config_version}); open approvals re-validated`); qc.invalidateQueries(); },
    onError: toastError,
  });
  const arm = useMutation({
    mutationFn: () => api.post<{ mode: string; form_ref: string }>("/api/integrations/mock-portal/faults", { ...fault, remaining: fault.mode === "NONE" ? 0 : 1 }),
    onSuccess: (r) => toast.success(`Fault ${r.mode} armed on ${r.form_ref} for the next operation`), onError: toastError,
  });
  const reset = useMutation({
    mutationFn: () => api.post<{ students: number; drives: number }>("/api/admin/demo-reset"),
    onSuccess: (r) => { setResetOpen(false); toast.success(`Demo reset: ${r.students} synthetic students, ${r.drives} drives`); qc.clear(); window.location.href = "/login"; },
    onError: toastError,
  });
  const purge = useMutation({
    mutationFn: () => api.post<{ purged_sheets: number }>("/api/admin/retention/purge"),
    onSuccess: (r) => toast.success(`${r.purged_sheets} attendance sheet(s) purged under the retention policy`), onError: toastError,
  });

  if (integ.isError) return <ErrorState error={integ.error} onRetry={() => integ.refetch()} />;

  return (
    <>
      <PageHeader title="Settings & Integrations" description="Environment, destinations, roles and institutional policy. Credentials are server-side only and never shown here." />
      {health.data?.demo_mode && <div className="mb-4"><Banner title="Demo environment">All people and employers are synthetic. Fault injection and demo reset are available to admins.</Banner></div>}
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Application health" />
          {!health.data ? <LoadingRows rows={3} /> : <div className="p-4"><DefinitionList items={[
            { label: "API", value: <StatusBadge status={health.data.status === "ok" ? "VERIFIED" : "FAILED"} label={health.data.status} /> },
            { label: "Database", value: `${health.data.database} (${health.data.database_dialect})` },
            { label: "Environment", value: health.data.environment },
            { label: "Planner", value: health.data.planner === "LLM" ? `LLM · ${health.data.llm_model}` : "Deterministic fallback" },
            { label: "Server time", value: fmtDateTime(health.data.time, undefined, { second: "2-digit" }) },
            { label: "Allowed destination hosts", value: integ.data?.allowed_destination_hosts.join(", ") ?? "—" },
          ]} /></div>}
        </Card>
        <Card>
          <CardHeader title="Official organiser target" />
          <div className="space-y-3 p-4">
            {integ.data && <Banner tone="warn" title="Organiser mock drive form not configured">{integ.data.official_organiser_target.note}</Banner>}
            <p className="text-[12.5px] text-muted">The destination adapter interface (<span className="font-mono">DriveFormAdapter</span>) is the only integration point. Adding the organiser&apos;s form means configuring an allowlisted destination of kind ORGANISER_FORM — no domain code changes.</p>
            {integ.data && <p className="text-[12.5px]">LLM: <strong>{integ.data.llm.mode}</strong></p>}
          </div>
        </Card>
      </div>

      <Card className="mt-4">
        <CardHeader title="Upload destinations" description="Reachability is checked live by fetching each form page through the adapter." />
        {!integ.data ? <LoadingRows rows={3} /> : (
          <table className="data-table w-full"><thead><tr><th>Destination</th><th>Form</th><th>Kind</th><th>Reachability</th><th>Config</th><th>Enabled</th></tr></thead>
            <tbody>{integ.data.destinations.map((d) => (
              <tr key={d.id}>
                <td>{d.name}</td>
                <td><a className="inline-flex items-center gap-1 font-mono text-[12px] text-accent hover:underline" target="_blank" rel="noreferrer" href={`/mock-portal/forms/${d.form_ref}`}>{d.form_ref}<ExternalLink className="size-3" /></a></td>
                <td>{d.is_local_mock ? <MockBadge /> : <Tag tone="info">Organiser</Tag>}</td>
                <td><StatusBadge status={d.status} tone={d.status === "reachable" ? "ok" : d.status === "unreachable" ? "bad" : "warn"} label={d.status} /><div className="text-[11.5px] text-muted">{d.detail}</div></td>
                <td>v{d.config_version}</td>
                <td>{can(me.data, "editData") ? <Button size="sm" variant={d.is_enabled ? "secondary" : "primary"} loading={toggleDest.isPending && toggleDest.variables?.id === d.id} onClick={() => toggleDest.mutate(d)}>{d.is_enabled ? "Disable" : "Enable"}</Button> : d.is_enabled ? "Yes" : "No"}</td>
              </tr>))}</tbody></table>
        )}
      </Card>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title="Institutional settings" description={isAdmin ? "Admin only. Schedule buffer changes re-validate open approvals." : "Read-only for your role."} />
          {!form ? <LoadingRows rows={4} /> : (
            <form className="grid gap-3 p-4 sm:grid-cols-2" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
              <Field label="Institution" htmlFor="inst" className="sm:col-span-2"><Input id="inst" disabled={!isAdmin} value={form.institution_name} onChange={(e) => setForm({ ...form, institution_name: e.target.value })} /></Field>
              <Field label="Default timezone" htmlFor="tz"><Select id="tz" className="w-full" disabled={!isAdmin} value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
                {["Asia/Kolkata", "UTC", "Asia/Singapore", "Asia/Dubai", "Europe/London"].map((t) => <option key={t}>{t}</option>)}</Select></Field>
              <Field label="Clash buffer between drives (minutes)" htmlFor="buf" hint="0 = only true overlaps clash"><Input id="buf" type="number" min={0} max={240} disabled={!isAdmin} value={form.schedule_buffer_minutes} onChange={(e) => setForm({ ...form, schedule_buffer_minutes: Number(e.target.value) })} /></Field>
              <Field label="Attendance sheet retention (days)" htmlFor="ret"><Input id="ret" type="number" min={1} disabled={!isAdmin} value={form.attendance_retention_days} onChange={(e) => setForm({ ...form, attendance_retention_days: Number(e.target.value) })} /></Field>
              <fieldset><legend className="mb-1.5 text-[13px] font-medium text-ink-2">Attendance sheet fields (data minimisation)</legend>
                {["roll_number", "full_name", "branch", "email"].map((f) => (
                  <label key={f} className="mr-3 inline-flex items-center gap-1.5 text-[13px]"><input type="checkbox" disabled={!isAdmin || f === "roll_number"} checked={form.attendance_fields.includes(f)}
                    onChange={(e) => setForm({ ...form, attendance_fields: e.target.checked ? [...form.attendance_fields, f] : form.attendance_fields.filter((x) => x !== f) })} />{f}</label>))}
              </fieldset>
              {isAdmin && <div className="flex gap-2 sm:col-span-2"><Button type="submit" variant="primary" loading={save.isPending} icon={<Save className="size-4" />}>Save settings</Button>
                <Button type="button" loading={purge.isPending} onClick={() => purge.mutate()} icon={<Trash2 className="size-4" />}>Apply retention purge now</Button></div>}
            </form>
          )}
        </Card>
        <Card>
          <CardHeader title="Users and roles" />
          {!users.data ? <p className="p-4 text-[13px] text-muted">{me.data?.role === "viewer" ? "Not visible to viewers." : "Loading…"}</p> : (
            <table className="data-table w-full"><thead><tr><th>Name</th><th>Email</th><th>Role</th></tr></thead>
              <tbody>{users.data.map((u) => <tr key={u.id}><td>{u.name}</td><td className="font-mono text-[12px]">{u.email}</td><td><Tag tone={u.role === "approver" ? "accent" : u.role === "admin" ? "bad" : "neutral"}>{ROLE_LABEL[u.role]}</Tag></td></tr>)}</tbody></table>
          )}
          <div className="border-t border-line p-4 text-[12.5px] text-muted">Officers run the agent and manage data · Approvers decide approval requests · Admins configure the system · Viewers read only. The agent has no user identity and cannot approve.</div>
        </Card>
      </div>

      {isAdmin && integ.data?.demo_mode && (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Card>
            <CardHeader title="Mock portal fault injection" description="Arms a one-shot fault on the LOCAL MOCK portal to demonstrate recovery and verification." />
            <div className="grid gap-3 p-4 sm:grid-cols-2">
              <Field label="Form" htmlFor="f_ref"><Select id="f_ref" className="w-full" value={fault.form_ref} onChange={(e) => setFault({ ...fault, form_ref: e.target.value })}>
                <option value="">Select…</option>{integ.data.destinations.map((d) => <option key={d.id} value={d.form_ref}>{d.form_ref}</option>)}</Select></Field>
              <Field label="Fault" htmlFor="f_mode"><Select id="f_mode" className="w-full" value={fault.mode} onChange={(e) => setFault({ ...fault, mode: e.target.value })}>
                {FAULTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</Select></Field>
              <div className="sm:col-span-2"><Button variant="primary" disabled={!fault.form_ref} loading={arm.isPending} onClick={() => arm.mutate()} icon={<Zap className="size-4" />}>Arm fault</Button></div>
            </div>
          </Card>
          <Card>
            <CardHeader title="Demo reset" />
            <div className="space-y-3 p-4">
              <p className="text-[13px] text-muted">Deletes all demo data (including the audit ledger and mock-portal submissions) and re-seeds the synthetic dataset. A DEMO_RESET event starts the new ledger. Disabled outside demo mode.</p>
              <Button variant="danger" onClick={() => setResetOpen(true)} icon={<RotateCcw className="size-4" />}>Reset demo data</Button>
            </div>
          </Card>
        </div>
      )}
      <ConfirmDialog open={resetOpen} onOpenChange={setResetOpen} tone="danger" loading={reset.isPending} title="Reset all demo data?" confirmLabel="Reset and re-seed" onConfirm={() => reset.mutate()}
        description="This cannot be undone. You will be signed out.">
        <p className="flex items-center gap-2 text-[13px] text-bad"><AlertTriangle className="size-4" />All runs, approvals, uploads and audit history will be removed.</p>
      </ConfirmDialog>
    </>
  );
}
