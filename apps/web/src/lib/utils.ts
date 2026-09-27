import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** Verdicts, mapped to the specification's three colours. */
export type StatusTone = "verified" | "pending" | "fraud" | "neutral";

/**
 * The single place that decides what colour a property is.
 *
 * Green = Verified, Yellow = Pending, Red = Fraud. An open fraud alert wins over
 * whatever the verification outcome says: a unit can be both verified and under
 * an ownership dispute, and the dispute is the thing the officer needs to see.
 */
export function statusTone(
  outcome: string | null | undefined,
  openAlerts = 0,
): StatusTone {
  if (openAlerts > 0) return "fraud";
  switch (outcome) {
    case "VERIFIED":
      return "verified";
    case "PENDING_VERIFICATION":
      return "pending";
    case "INVALID_CLAIM":
    case "UNAUTHORIZED_OCCUPANCY":
      return "fraud";
    default:
      return "neutral";
  }
}

export const TONE_CLASSES: Record<StatusTone, string> = {
  verified: "bg-verified text-verified-foreground border-transparent",
  pending: "bg-pending text-pending-foreground border-transparent",
  fraud: "bg-fraud text-fraud-foreground border-transparent",
  neutral: "bg-muted text-muted-foreground border-transparent",
};

/** Hex equivalents for Three.js materials, which cannot read CSS variables. */
export const TONE_HEX: Record<StatusTone, number> = {
  verified: 0x1d7a45,
  pending: 0xc47f0a,
  fraud: 0xb91c1c,
  neutral: 0x94a3b8,
};

export const OUTCOME_LABELS: Record<string, string> = {
  VERIFIED: "Verified",
  PENDING_VERIFICATION: "Pending verification",
  INVALID_CLAIM: "Invalid claim",
  UNAUTHORIZED_OCCUPANCY: "Unauthorized occupancy",
};

export const SEVERITY_TONE: Record<string, StatusTone> = {
  LOW: "pending",
  MEDIUM: "pending",
  HIGH: "fraud",
  CRITICAL: "fraud",
};

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Indian digit grouping — 12,34,567 rather than 1,234,567. */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-IN");
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

export function initials(name: string | null | undefined): string {
  if (!name) return "?";
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}
