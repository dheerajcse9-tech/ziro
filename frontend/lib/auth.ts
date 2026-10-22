"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Role, User } from "./types";

export function useMe() {
  return useQuery({ queryKey: ["me"], queryFn: () => api.get<User>("/api/auth/me"), staleTime: 60_000, retry: false });
}

const PERMISSIONS: Record<string, Role[]> = {
  runAgent: ["officer", "admin"],
  editData: ["officer", "admin"],
  approve: ["approver"], // only the Approver signs off; admins set the system up
  admin: ["admin"],
  download: ["officer", "admin", "approver"],
  // Drive upload officer (coordinator): company inbox, drives, consent forms
  manageDrives: ["coordinator", "officer", "admin"],
  connectMailbox: ["coordinator", "admin"],
  // Approver-side follow-ups (missing details / consent forms)
  followUp: ["approver", "officer", "admin"],
};

export function can(user: User | undefined, action: keyof typeof PERMISSIONS | string): boolean {
  return !!user && (PERMISSIONS[action] ?? []).includes(user.role);
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  officer: "Placement officer",
  approver: "Approver (Head of Placements)",
  viewer: "Viewer",
  coordinator: "Drive upload officer",
};

// One sentence per role, used on the login page and Home so everyone sees the same workflow.
export const ROLE_INFO: Record<Role, { step: string; job: string }> = {
  coordinator: { step: "Step 1",
    job: "Watches the placement mailbox. When a company emails a drive, checks the details and deadline, creates the drive and sends the consent form to all students." },
  officer: { step: "Step 2",
    job: "After students answer the consent form, runs the drive: keeps students who said YES and meet the company's criteria, fixes student records and clashes, and sends the list to the Approver." },
  approver: { step: "Step 3",
    job: "Checks each list, asks students with missing details to fill a form, then approves or rejects. On approval the list is sent and emailed to the company." },
  admin: { step: "Setup",
    job: "Sets the system up: connects Gmail, adds the AI key, trusted company emails and students. Can help with steps 1–2, but cannot approve lists." },
  viewer: { step: "View", job: "Can look at drives and students but cannot change anything." },
};

export const ROLE_ORDER: Role[] = ["coordinator", "officer", "approver", "admin", "viewer"];
