import { request } from "./client";
import type { components } from "./schema";

export type Attention = components["schemas"]["Attention"];

export async function fetchAttention(signal?: AbortSignal): Promise<Attention> {
  return request<Attention>("/api/attention", { signal });
}

/** Events after which the counts can have changed. */
export function affectsAttention(eventType: string): boolean {
  return /^(incident|approval|fault)\./.test(eventType);
}

/** The count shown next to a sidebar entry, or null for no badge. */
export function navBadge(
  href: string,
  attention: Attention | null,
): { count: number; tone: "warning" | "destructive" | "secondary" } | null {
  if (!attention) return null;
  switch (href) {
    case "/approvals":
      // A pending decision is the one thing only a human can unblock.
      return attention.pending_approvals > 0
        ? { count: attention.pending_approvals, tone: "destructive" }
        : null;
    case "/incidents":
      return attention.open_incidents > 0
        ? {
            count: attention.open_incidents,
            tone: attention.worst_severity === "sev1" ? "destructive" : "warning",
          }
        : null;
    case "/faults":
      return attention.active_faults > 0
        ? { count: attention.active_faults, tone: "secondary" }
        : null;
    default:
      return null;
  }
}

/**
 * The browser-tab title: pending approvals first (they expire), then open
 * incidents, so a backgrounded console still says when it needs someone.
 */
export function attentionTitle(base: string, attention: Attention | null): string {
  if (!attention) return base;
  if (attention.pending_approvals > 0) {
    const n = attention.pending_approvals;
    return `(${n}) ${n === 1 ? "approval" : "approvals"} pending · ${base}`;
  }
  if (attention.open_incidents > 0) {
    const n = attention.open_incidents;
    return `(${n}) open ${n === 1 ? "incident" : "incidents"} · ${base}`;
  }
  return base;
}

const PREFIX = /^\(\d+\) (?:open )?(?:approvals?|incidents?)(?: pending)? · /;

/** Undo `attentionTitle`, so the prefix is never applied twice. */
export function stripAttention(title: string): string {
  return title.replace(PREFIX, "");
}
