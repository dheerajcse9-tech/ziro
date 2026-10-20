import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Format an instant in a specific IANA timezone (drive-local time). */
export function fmtDateTime(iso: string | null | undefined, tz?: string, opts: Intl.DateTimeFormatOptions = {}) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false,
    timeZone: tz, ...opts,
  }).format(new Date(iso));
}

export function fmtDate(iso: string | null | undefined, tz?: string) {
  return fmtDateTime(iso, tz, { hour: undefined, minute: undefined });
}

export function fmtTime(iso: string | null | undefined, tz?: string) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: tz }).format(new Date(iso));
}

export function fmtSlot(start: string, end: string, tz: string) {
  return `${fmtDate(start, tz)} · ${fmtTime(start, tz)}–${fmtTime(end, tz)} ${tzShort(tz)}`;
}

export function tzShort(tz: string) {
  if (tz === "Asia/Kolkata") return "IST";
  return tz;
}

export function relTime(iso: string | null | undefined) {
  if (!iso) return "—";
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  const abs = Math.abs(diff);
  const fmt = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  if (abs < 60) return fmt.format(-Math.round(diff), "second");
  if (abs < 3600) return fmt.format(-Math.round(diff / 60), "minute");
  if (abs < 86400) return fmt.format(-Math.round(diff / 3600), "hour");
  return fmt.format(-Math.round(diff / 86400), "day");
}

export function shortHash(h: string | null | undefined, n = 10) {
  return h ? `${h.slice(0, n)}…` : "—";
}

export function titleCase(s: string | null | undefined) {
  if (!s) return "";
  return s.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
