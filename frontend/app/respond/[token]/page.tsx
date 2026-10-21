"use client";

// Public page opened from a student's personal email link. No login: the
// unguessable token is the credential. Shows no personal data beyond first name.
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, ShieldCheck, XCircle } from "lucide-react";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button, Card, Field, Input, Select } from "@/components/ui/primitives";
import { Banner, ErrorState, LoadingRows } from "@/components/ui/states";
import { api, ApiError } from "@/lib/api";
import type { PublicRequest } from "@/lib/types";

export default function RespondPage() {
  const { token } = useParams<{ token: string }>();
  const q = useQuery({ queryKey: ["public-request", token], queryFn: () => api.get<PublicRequest>(`/api/public/requests/${token}`), retry: false });
  const [values, setValues] = useState<Record<string, string>>({});
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const answer = useMutation({
    mutationFn: (accept: boolean) => api.post<PublicRequest>(`/api/public/requests/${token}/invitation`, { accept }),
    onSuccess: () => q.refetch(),
  });
  const submit = useMutation({
    mutationFn: () => api.post<PublicRequest>(`/api/public/requests/${token}/data`, { values }),
    onSuccess: () => { setFieldErrors({}); q.refetch(); },
    onError: (e) => setFieldErrors(e instanceof ApiError ? ((e.details as any)?.fields ?? {}) : {}),
  });

  const r = q.data;
  return (
    <div className="flex min-h-screen items-start justify-center px-4 py-10">
      <div className="w-full max-w-lg">
        <div className="mb-5 flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-ink text-white"><ShieldCheck className="size-5" /></div>
          <div><p className="text-lg font-semibold text-ink">Placement Cell</p><p className="text-[13px] text-muted">Secure student response form</p></div>
        </div>
        {q.isError ? <ErrorState error={q.error} /> : !r ? <Card className="p-4"><LoadingRows rows={4} /></Card> : (
          <Card className="space-y-4 p-5">
            <p className="text-[15px]">Hi <strong>{r.first_name}</strong> <span className="text-muted">({r.roll_number})</span>,</p>
            {r.drive && (
              <div className="rounded-md bg-subtle p-3 text-[13px]">
                <p className="font-semibold text-ink">{r.drive.title}</p>
                <p className="text-muted">{r.drive.employer} · {r.drive.slot}{r.drive.location ? ` · ${r.drive.location}` : ""}</p>
                <p className="mt-1 text-muted">Criteria: {r.drive.criteria}</p>
              </div>
            )}

            {r.status === "EXPIRED" && <Banner tone="bad" title="This link has expired">Please contact the Placement Cell.</Banner>}

            {r.kind === "DRIVE_INVITATION" && r.status !== "EXPIRED" && (
              <>
                {r.status === "ACCEPTED" && <Banner tone="ok" title="You are registered">Your profile (roll number, name, branch, CGPA) will be shared with {r.drive?.employer} for this drive. You can still change your answer below until the link expires.</Banner>}
                {r.status === "DECLINED" && <Banner tone="info" title="You declined this drive">Your details will not be shared with {r.drive?.employer}. You can change your answer below until the link expires.</Banner>}
                {r.status === "SENT" && <p className="text-[13px] text-ink-2">You meet the eligibility criteria for this drive. Do you want to register and allow the Placement Cell to share your profile (roll number, name, branch and CGPA) with {r.drive?.employer} for this drive only?</p>}
                {answer.isError && <ErrorState error={answer.error} />}
                <div className="flex gap-2">
                  <Button variant="success" className="flex-1" icon={<CheckCircle2 className="size-4" />} disabled={r.status === "ACCEPTED"}
                    loading={answer.isPending && answer.variables === true} onClick={() => answer.mutate(true)}>Accept &amp; register</Button>
                  <Button variant="secondary" className="flex-1" icon={<XCircle className="size-4" />} disabled={r.status === "DECLINED"}
                    loading={answer.isPending && answer.variables === false} onClick={() => answer.mutate(false)}>Decline</Button>
                </div>
              </>
            )}

            {r.kind === "DATA_REQUEST" && r.status === "SENT" && (
              <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit.mutate(); }}>
                <p className="text-[13px] text-ink-2">Your placement record is missing the details below. The Placement Cell will verify them before updating your record.</p>
                {r.requested_fields.map((f) => (
                  <Field key={f.field} label={f.label} htmlFor={f.field} error={fieldErrors[f.field]}>
                    {f.field === "branch" ? (
                      <Select id="branch" className="w-full" value={values.branch ?? ""} onChange={(e) => setValues({ ...values, branch: e.target.value })}>
                        <option value="">Select your branch…</option>
                        {r.branches.map((b) => <option key={b.code} value={b.code}>{b.label}</option>)}
                      </Select>
                    ) : (
                      <Input id={f.field} inputMode="decimal" value={values[f.field] ?? ""} onChange={(e) => setValues({ ...values, [f.field]: e.target.value })}
                        placeholder={f.field === "cgpa" ? "e.g. 7.85" : f.field === "active_backlogs" ? "e.g. 0" : "e.g. 2026"} />
                    )}
                  </Field>
                ))}
                {submit.isError && !Object.keys(fieldErrors).length && <ErrorState error={submit.error} />}
                <Button type="submit" variant="primary" className="w-full" loading={submit.isPending}>Submit details</Button>
              </form>
            )}
            {r.kind === "DATA_REQUEST" && ["SUBMITTED", "APPLIED", "REJECTED"].includes(r.status) && (
              <Banner tone="ok" title="Thank you — your details were received">
                {r.status === "APPLIED" ? "The Placement Cell has verified and updated your record." : r.status === "REJECTED" ? "The Placement Cell could not accept these details and will contact you." : "The Placement Cell will verify them and update your record."}
              </Banner>
            )}
            <p className="text-[11.5px] text-faint">This personal link expires on {new Date(r.expires_at).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" })}. Do not forward it.</p>
          </Card>
        )}
      </div>
    </div>
  );
}
