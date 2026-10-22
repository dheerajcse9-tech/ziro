"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Card, Field, Input } from "@/components/ui/primitives";
import { ErrorState } from "@/components/ui/states";
import { api, ApiError } from "@/lib/api";
import { ROLE_LABEL } from "@/lib/auth";
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

  return (
    <div className="flex min-h-screen items-center justify-center px-4 py-10">
      <div className="w-full max-w-md">
        <div className="mb-6 flex items-center gap-3">
          <div className="flex size-10 items-center justify-center rounded-lg bg-ink text-white"><ShieldCheck className="size-5" /></div>
          <div>
            <h1 className="text-lg font-semibold text-ink">PlacementOS</h1>
            <p className="text-[13px] text-muted">Agentic placement drive coordinator</p>
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
        {demo.data && demo.data.length > 0 && (
          <Card className="mt-4 p-4">
            <p className="text-[12px] font-semibold uppercase tracking-wide text-faint">Staff logins</p>
            <p className="mt-1 text-[12px] text-muted">Password for all staff logins: <code className="font-mono">Demo@2026</code></p>
            <ul className="mt-3 divide-y divide-line">
              {demo.data.map((a) => (
                <li key={a.email}>
                  <button type="button" className="flex w-full items-center justify-between gap-2 py-2 text-left hover:bg-subtle"
                    onClick={() => { setValue("email", a.email); setValue("password", "Demo@2026"); }}>
                    <span>
                      <span className="block text-[13px] font-medium text-ink">{a.name}</span>
                      <span className="block font-mono text-[11.5px] text-muted">{a.email}</span>
                    </span>
                    <span className="text-[11px] text-muted">{ROLE_LABEL[a.role]}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </div>
    </div>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
