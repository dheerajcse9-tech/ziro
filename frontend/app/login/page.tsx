"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Card, Field, Input } from "@/components/ui/primitives";
import { ErrorState } from "@/components/ui/states";
import { api, ApiError } from "@/lib/api";
import { ROLE_INFO, ROLE_LABEL, ROLE_ORDER } from "@/lib/auth";
import type { Role } from "@/lib/types";

const schema = z.object({ email: z.string().email("Enter a valid email"), password: z.string().min(1, "Password is required") });
type FormValues = z.infer<typeof schema>;

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const demo = useQuery({ queryKey: ["demo-accounts"], queryFn: () => api.get<{ email: string; name: string; role: Role }[]>("/api/auth/demo-accounts") });
  const { register, handleSubmit, setValue, setError, formState: { errors, isSubmitting } } = useForm<FormValues>({ resolver: zodResolver(schema) });

  async function onSubmit(values: FormValues) {
    try {
      await api.post("/api/auth/login", values);
      qc.clear();
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      setError("root", { message: err instanceof ApiError ? err.message : "Sign-in failed" });
    }
  }

  const accounts = [...(demo.data ?? [])].sort((a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role));

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="grid w-full max-w-5xl gap-6 lg:grid-cols-[minmax(0,22rem)_1fr]">
        <div>
          <div className="mb-6 flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg bg-ink text-white"><ShieldCheck className="size-5" /></div>
            <div>
              <h1 className="text-lg font-semibold text-ink">PlacementOS</h1>
              <p className="text-[13px] text-muted">Placement drives, run by agents and approved by people</p>
            </div>
          </div>
          <Card className="p-5">
            <form onSubmit={handleSubmit(onSubmit)} className="space-y-4" noValidate>
              <Field label="Email" htmlFor="email" error={errors.email?.message}>
                <Input id="email" type="email" autoComplete="username" {...register("email")} />
              </Field>
              <Field label="Password" htmlFor="password" error={errors.password?.message}>
                <Input id="password" type="password" autoComplete="current-password" {...register("password")} />
              </Field>
              {errors.root?.message && <ErrorState error={new Error(errors.root.message)} />}
              <Button type="submit" variant="primary" className="w-full" loading={isSubmitting}>Sign in</Button>
            </form>
          </Card>
        </div>

        <div className="space-y-4">
          <Card className="p-4">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-faint">How a drive works</p>
            <ol className="mt-3 grid gap-2 text-[12.5px] sm:grid-cols-[1fr_auto_1fr_auto_1fr] sm:items-center">
              {[["1", "Company emails the placement mailbox", "Drive + consent form to all students"],
                ["2", "Students answer YES / NO", "Officer runs the drive → list"],
                ["3", "Approver checks and approves", "List emailed to the company"]].flatMap(([n, a, b], i) => [
                <li key={n} className="rounded-md border border-line bg-subtle p-2.5">
                  <span className="mr-1.5 inline-flex size-5 items-center justify-center rounded-full bg-ink text-[11px] font-semibold text-white">{n}</span>
                  <span className="font-medium text-ink">{a}</span><span className="block pl-6 text-muted">{b}</span>
                </li>,
                ...(i < 2 ? [<ArrowRight key={`a${n}`} className="mx-auto hidden size-4 text-faint sm:block" aria-hidden />] : []),
              ])}
            </ol>
            <p className="mt-3 text-[12px] text-muted">All emails to students and companies are sent from the placement mailbox. Agents do the routine work but never approve.</p>
          </Card>

          {accounts.length > 0 && (
            <Card className="p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="text-[12px] font-semibold uppercase tracking-wide text-faint">Who does what — click to sign in</p>
                <p className="text-[12px] text-muted">Password for all: <code className="font-mono">Demo@2026</code></p>
              </div>
              <ul className="mt-2 divide-y divide-line">
                {accounts.map((a) => (
                  <li key={a.email}>
                    <button type="button" className="flex w-full items-start gap-3 rounded-md px-2 py-2.5 text-left hover:bg-subtle"
                      onClick={() => { setValue("email", a.email); setValue("password", "Demo@2026"); }}>
                      <span className="mt-0.5 w-14 shrink-0 rounded bg-accent-soft px-1.5 py-0.5 text-center text-[11px] font-semibold text-accent">{ROLE_INFO[a.role].step}</span>
                      <span className="min-w-0">
                        <span className="block text-[13px] font-medium text-ink">{ROLE_LABEL[a.role]} <span className="font-mono text-[11.5px] font-normal text-muted">{a.email}</span></span>
                        <span className="block text-[12.5px] text-muted">{ROLE_INFO[a.role].job}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
