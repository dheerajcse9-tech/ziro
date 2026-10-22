"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { BRANCHES } from "@/components/drives/criteria-fields";
import { Dialog } from "@/components/ui/overlay";
import { Button, Field, Input, Select } from "@/components/ui/primitives";
import { Banner } from "@/components/ui/states";
import { api } from "@/lib/api";
import { toastError } from "@/lib/errors";
import type { InboundEmail } from "@/lib/types";

const EMPTY = { company: "", contact: "", role: "", ctc: "", venue: "", date: "", start: "10:00", end: "13:00",
  cgpa: "7.0", backlogs: "none", maxBacklogs: "1", branches: [] as string[], batch: String(new Date().getFullYear()),
  deadlineDate: "", deadlineTime: "17:00", listBy: "" };

function longDate(iso: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" });
}

/** The drive written out like a company email, so it goes through exactly the same agents and checks. */
function compose(v: typeof EMPTY) {
  const backlog = v.backlogs === "none" ? ", no active backlogs" : v.backlogs === "max" ? `, maximum ${v.maxBacklogs} active backlog${v.maxBacklogs === "1" ? "" : "s"}` : "";
  const lines = [
    "Drive entered in PlaceCrew by the placement office.", "",
    `Company: ${v.company.trim()}`, `Role: ${v.role.trim()}`, ...(v.ctc ? [`CTC: ${v.ctc}`] : []),
    `Drive date: ${longDate(v.date)}`, `Time: ${v.start} to ${v.end}`, ...(v.venue ? [`Venue: ${v.venue}`] : []),
    `Eligibility: ${v.cgpa ? `Minimum CGPA of ${v.cgpa}` : "No minimum CGPA"}${backlog}.`,
    `Branches eligible: ${v.branches.length ? v.branches.join(", ") : "All branches"}`,
    ...(v.batch ? [`Batch: ${v.batch}`] : []),
    `Registration deadline: ${longDate(v.deadlineDate)}, ${v.deadlineTime}`,
    ...(v.listBy ? ["", `Please share the list of interested eligible students by ${longDate(v.listBy)}.`] : []),
  ];
  return { subject: `Campus Drive – ${v.company.trim()} – ${v.role.trim()}`, body: lines.join("\n") };
}

export function NewDriveDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const [v, setV] = useState(EMPTY);
  const [problems, setProblems] = useState<string[]>([]);
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setV({ ...v, [k]: e.target.value });
  const ready = v.company.trim() && v.role.trim() && v.date && v.start && v.end > v.start && v.deadlineDate;

  const create = useMutation({
    mutationFn: () => api.post<InboundEmail & { run_status: string }>("/api/inbox/staff-entry",
      { ...compose(v), company: v.company.trim(), contact_email: v.contact.trim() || null }),
    onSuccess: (r) => {
      qc.invalidateQueries();
      if (r.drive_id) {
        toast.success("Drive created", { description: "The consent form was sent to every student." });
        onOpenChange(false); setV(EMPTY); setProblems([]);
        router.push(`/drives/${r.drive_id}`);
      } else {
        setProblems(r.issues.filter((i) => i.level !== "INFO").map((i) => i.message));
      }
    },
    onError: toastError,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange} wide title="New drive"
      description="For a drive a company told you about outside email (phone, letter, meeting). The same agents check it, create the drive and send the consent form to every student."
      footer={<><Button onClick={() => onOpenChange(false)}>Cancel</Button>
        <Button variant="primary" disabled={!ready} loading={create.isPending} onClick={() => create.mutate()}>Create drive and send consent forms</Button></>}>
      <div className="space-y-4">
        {problems.length > 0 && <Banner tone="warn" title="Please fix these, then try again">
          <ul className="list-disc pl-4">{problems.map((p) => <li key={p}>{p}</li>)}</ul></Banner>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Company" htmlFor="nd_company"><Input id="nd_company" value={v.company} onChange={set("company")} /></Field>
          <Field label="Company email (gets the approved list)" htmlFor="nd_contact"><Input id="nd_contact" type="email" value={v.contact} onChange={set("contact")} placeholder="hr@company.com" /></Field>
          <Field label="Role" htmlFor="nd_role"><Input id="nd_role" value={v.role} onChange={set("role")} /></Field>
          <Field label="Package (optional)" htmlFor="nd_ctc"><Input id="nd_ctc" value={v.ctc} onChange={set("ctc")} placeholder="8 LPA" /></Field>
          <Field label="Drive date" htmlFor="nd_date"><Input id="nd_date" type="date" value={v.date} onChange={set("date")} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Starts" htmlFor="nd_start"><Input id="nd_start" type="time" value={v.start} onChange={set("start")} /></Field>
            <Field label="Ends" htmlFor="nd_end" error={v.end <= v.start ? "Must be after the start" : undefined}><Input id="nd_end" type="time" value={v.end} onChange={set("end")} /></Field>
          </div>
          <Field label="Venue (optional)" htmlFor="nd_venue"><Input id="nd_venue" value={v.venue} onChange={set("venue")} /></Field>
          <Field label="Batch" htmlFor="nd_batch"><Input id="nd_batch" inputMode="numeric" value={v.batch} onChange={set("batch")} /></Field>
          <Field label="Students must answer by (date)" htmlFor="nd_dl"><Input id="nd_dl" type="date" value={v.deadlineDate} onChange={set("deadlineDate")} /></Field>
          <Field label="…at (time)" htmlFor="nd_dlt"><Input id="nd_dlt" type="time" value={v.deadlineTime} onChange={set("deadlineTime")} /></Field>
          <Field label="Company wants the list by (optional)" htmlFor="nd_list"><Input id="nd_list" type="date" value={v.listBy} onChange={set("listBy")} /></Field>
        </div>
        <div className="rounded-md border border-line p-3">
          <p className="mb-3 text-[13px] font-semibold text-ink">Who can apply</p>
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Minimum CGPA" htmlFor="nd_cgpa"><Input id="nd_cgpa" inputMode="decimal" value={v.cgpa} onChange={set("cgpa")} placeholder="leave empty for none" /></Field>
            <Field label="Backlogs" htmlFor="nd_bl">
              <Select id="nd_bl" className="w-full" value={v.backlogs} onChange={set("backlogs")}>
                <option value="none">No active backlogs</option><option value="max">Up to a number</option><option value="any">Not considered</option>
              </Select>
            </Field>
            {v.backlogs === "max" && <Field label="Maximum backlogs" htmlFor="nd_maxbl"><Input id="nd_maxbl" inputMode="numeric" value={v.maxBacklogs} onChange={set("maxBacklogs")} /></Field>}
          </div>
          <p className="mb-1.5 mt-3 text-[12.5px] font-medium text-ink">Branches <span className="font-normal text-muted">(none ticked = all branches)</span></p>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {BRANCHES.map((b) => (
              <label key={b.code} className="flex items-center gap-1.5 text-[12.5px]">
                <input type="checkbox" checked={v.branches.includes(b.code)}
                  onChange={(e) => setV({ ...v, branches: e.target.checked ? [...v.branches, b.code] : v.branches.filter((x) => x !== b.code) })} />
                {b.label}
              </label>
            ))}
          </div>
        </div>
      </div>
    </Dialog>
  );
}
