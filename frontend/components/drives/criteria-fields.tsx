"use client";

import { z } from "zod";
import { Field, Input, Select, Textarea } from "@/components/ui/primitives";
import { cn } from "@/lib/format";
import type { UseFormReturn } from "react-hook-form";

export const BRANCHES: { code: string; label: string }[] = [
  { code: "CSE", label: "Computer Science & Engineering" }, { code: "IT", label: "Information Technology" },
  { code: "AIDS", label: "AI & Data Science" }, { code: "ECE", label: "Electronics & Communication" },
  { code: "EEE", label: "Electrical & Electronics" }, { code: "ME", label: "Mechanical" },
  { code: "CE", label: "Civil" }, { code: "CHE", label: "Chemical" },
];

export const criteriaSchema = z.object({
  min_cgpa: z.string().refine((v) => v === "" || (!isNaN(Number(v)) && Number(v) >= 0 && Number(v) <= 10), "0–10, or blank for no minimum"),
  backlog_policy: z.enum(["NO_ACTIVE_BACKLOGS", "MAX_ALLOWED", "NOT_CONSIDERED"]),
  max_active_backlogs: z.string(),
  allowed_branches: z.array(z.string()),
  source_text: z.string().optional(),
  change_note: z.string().optional(),
}).refine((v) => v.backlog_policy !== "MAX_ALLOWED" || /^\d+$/.test(v.max_active_backlogs), {
  path: ["max_active_backlogs"], message: "Enter the maximum allowed backlogs",
});

export type CriteriaValues = z.infer<typeof criteriaSchema>;

export function toCriteriaPayload(v: CriteriaValues) {
  return {
    min_cgpa: v.min_cgpa === "" ? null : Number(v.min_cgpa),
    backlog_policy: v.backlog_policy,
    max_active_backlogs: v.backlog_policy === "MAX_ALLOWED" ? Number(v.max_active_backlogs) : null,
    allowed_branches: v.allowed_branches,
    source_text: v.source_text || null,
    change_note: v.change_note || null,
    required_consent_scope: "SHARE_WITH_EMPLOYERS",
  };
}

// The form type is generic so the same block can sit inside larger forms.
export function CriteriaFields({ form, prefix = "", showNote }: { form: UseFormReturn<any>; prefix?: string; showNote?: boolean }) {
  const name = (k: string) => (prefix ? `${prefix}.${k}` : k);
  const errors = (prefix ? form.formState.errors[prefix] : form.formState.errors) as Record<string, { message?: string }> | undefined;
  const policy = form.watch(name("backlog_policy"));
  const branches: string[] = form.watch(name("allowed_branches")) ?? [];
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label="Minimum CGPA (inclusive, ≥)" htmlFor="min_cgpa" error={errors?.min_cgpa?.message} hint="Blank = no CGPA requirement">
        <Input id="min_cgpa" inputMode="decimal" {...form.register(name("min_cgpa"))} />
      </Field>
      <Field label="Backlog policy" htmlFor="backlog_policy">
        <Select id="backlog_policy" className="w-full" {...form.register(name("backlog_policy"))}>
          <option value="NO_ACTIVE_BACKLOGS">No active backlogs</option>
          <option value="MAX_ALLOWED">Up to N active backlogs</option>
          <option value="NOT_CONSIDERED">Not considered by employer</option>
        </Select>
      </Field>
      {policy === "MAX_ALLOWED" && (
        <Field label="Maximum active backlogs" htmlFor="max_active_backlogs" error={errors?.max_active_backlogs?.message}>
          <Input id="max_active_backlogs" inputMode="numeric" {...form.register(name("max_active_backlogs"))} />
        </Field>
      )}
      <fieldset className="sm:col-span-2">
        <legend className="mb-1.5 text-[13px] font-medium text-ink-2">Eligible branches <span className="font-normal text-muted">(none selected = all branches)</span></legend>
        <div className="flex flex-wrap gap-1.5">
          {BRANCHES.map((b) => {
            const on = branches.includes(b.code);
            return (
              <button type="button" key={b.code} title={b.label} aria-pressed={on}
                onClick={() => form.setValue(name("allowed_branches"), on ? branches.filter((x) => x !== b.code) : [...branches, b.code], { shouldDirty: true })}
                className={cn("rounded-md px-2.5 py-1 text-[12px] font-medium ring-1 ring-inset", on ? "bg-accent text-white ring-accent" : "bg-surface text-ink-2 ring-line-strong hover:bg-subtle")}>
                {b.code}
              </button>
            );
          })}
        </div>
      </fieldset>
      <Field label="Employer's published criteria text (source)" htmlFor="source_text" className="sm:col-span-2" hint="Kept as evidence. The agent cross-checks it against the employer form; it never overrides these rules.">
        <Textarea id="source_text" rows={3} {...form.register(name("source_text"))} />
      </Field>
      {showNote && (
        <Field label="Change note" htmlFor="change_note" className="sm:col-span-2">
          <Input id="change_note" placeholder="Why are the criteria changing?" {...form.register(name("change_note"))} />
        </Field>
      )}
    </div>
  );
}
