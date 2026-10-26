import type { IncidentStatus } from "@/lib/api/incidents";

export type BadgeVariant = "success" | "warning" | "destructive" | "secondary";

export function statusBadge(status: IncidentStatus): BadgeVariant {
  switch (status) {
    case "RESOLVED":
      return "success";
    case "ESCALATED":
    case "REJECTED":
      return "destructive";
    case "OPEN":
    case "AWAITING_APPROVAL":
    case "REMEDIATING":
    case "VERIFYING":
      return "warning";
    default:
      return "secondary";
  }
}

export function severityBadge(severity: string): BadgeVariant {
  switch (severity) {
    case "sev1":
      return "destructive";
    case "sev2":
      return "warning";
    default:
      return "secondary";
  }
}

export function eventBadge(kind: string): BadgeVariant {
  switch (kind) {
    case "opened":
      return "destructive";
    case "signal":
      return "warning";
    case "recovered":
      return "success";
    case "closed":
    case "status":
      return "secondary";
    default:
      return "secondary";
  }
}
