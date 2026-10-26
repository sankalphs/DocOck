import type { FaultStatus } from "@/lib/api/faults";

export type BadgeVariant = "default" | "secondary" | "outline" | "success" | "warning" | "destructive";

export function statusVariant(status: FaultStatus): BadgeVariant {
  switch (status) {
    case "ACTIVE":
      return "destructive";
    case "PROPAGATING":
    case "REVERTING":
      return "warning";
    case "REVERTED":
      return "success";
    case "FAILED":
      return "destructive";
    default:
      return "outline";
  }
}

export function statusLabel(status: FaultStatus): string {
  switch (status) {
    case "PROPAGATING":
      return "propagating";
    case "ACTIVE":
      return "active";
    case "REVERTING":
      return "reverting";
    default:
      return status.toLowerCase();
  }
}

export function openStatus(status: FaultStatus): boolean {
  return status === "PROPAGATING" || status === "ACTIVE" || status === "REVERTING";
}

export type Countdown = { label: string; fraction: number; expired: boolean };

/** Remaining TTL as mm:ss plus the fraction of the original TTL still left. */
export function countdown(
  injectedAt: string | null,
  expiresAt: string | null,
  now: number,
): Countdown {
  if (!expiresAt) return { label: "no expiry", fraction: 0, expired: false };
  const expires = new Date(expiresAt).getTime();
  const start = injectedAt ? new Date(injectedAt).getTime() : expires - 900_000;
  const remaining = Math.max(0, expires - now);
  const total = Math.max(1, expires - start);
  const seconds = Math.ceil(remaining / 1000);
  const minutes = Math.floor(seconds / 60);
  const label = remaining <= 0 ? "expired" : `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
  return { label, fraction: Math.min(1, remaining / total), expired: remaining <= 0 };
}

export function formatParams(params: Record<string, unknown>): string {
  const entries = Object.entries(params);
  if (entries.length === 0) return "no parameters";
  return entries.map(([key, value]) => `${key} ${String(value)}`).join(" · ");
}
