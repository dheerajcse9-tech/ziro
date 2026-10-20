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
  approve: ["approver", "admin"],
  admin: ["admin"],
  download: ["officer", "admin", "approver"],
};

export function can(user: User | undefined, action: keyof typeof PERMISSIONS | string): boolean {
  return !!user && (PERMISSIONS[action] ?? []).includes(user.role);
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: "Administrator",
  officer: "Placement officer",
  approver: "Approver (Head of Placements)",
  viewer: "Viewer",
};
