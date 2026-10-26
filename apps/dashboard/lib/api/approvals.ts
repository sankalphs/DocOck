import { request } from "./client";
import type { components } from "./schema";

export type ApprovalView = components["schemas"]["ApprovalView"];
export type ApprovalList = components["schemas"]["ApprovalList"];
export type ActionView = components["schemas"]["ActionView"];
export type PlanCheck = components["schemas"]["PlanCheck"];
export type DryRunResult = components["schemas"]["DryRunResult"];
export type ActionAuditEntry = components["schemas"]["ActionAuditEntry"];

export type ApprovalFilter = "all" | "pending" | "decided";

export function fetchApprovals(
  status: ApprovalFilter = "all",
  limit = 50,
  signal?: AbortSignal,
): Promise<ApprovalList> {
  return request<ApprovalList>(`/api/approvals?status=${status}&limit=${limit}`, { signal });
}

export function fetchApprovalAudit(
  incident: string,
  signal?: AbortSignal,
): Promise<ActionAuditEntry[]> {
  return request<ActionAuditEntry[]>(
    `/api/approvals/audit?incident=${encodeURIComponent(incident)}&limit=200`,
    { signal },
  );
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

/** Approve: the server mints the single-use token itself; none is sent or returned. */
export function approve(id: number, note?: string): Promise<ApprovalView> {
  return post<ApprovalView>(`/api/approvals/${id}/approve`, {
    actor: "dashboard",
    note: note || null,
  });
}

export function reject(id: number, note?: string): Promise<ApprovalView> {
  return post<ApprovalView>(`/api/approvals/${id}/reject`, {
    actor: "dashboard",
    note: note || null,
  });
}

export function dryRun(id: number): Promise<DryRunResult> {
  return request<DryRunResult>(`/api/approvals/${id}/dry-run?actor=dashboard`, {
    method: "POST",
  });
}

type BadgeVariant = "default" | "secondary" | "outline" | "success" | "warning" | "destructive";

export function riskVariant(risk: string | null | undefined): BadgeVariant {
  if (risk === "medium") return "warning";
  if (risk === "low") return "success";
  return "outline";
}

export function approvalVariant(status: string): BadgeVariant {
  switch (status) {
    case "PENDING":
      return "warning";
    case "APPROVED":
      return "success";
    case "REJECTED":
    case "EXPIRED":
      return "destructive";
    default:
      return "outline";
  }
}

export function actionVariant(status: string): BadgeVariant {
  switch (status) {
    case "EXECUTED":
      return "success";
    case "EXECUTING":
    case "APPROVED":
      return "default";
    case "FAILED":
    case "ROLLED_BACK":
      return "destructive";
    case "PLANNED":
      return "warning";
    default:
      return "outline";
  }
}

/** `9:58` for a pending approval's remaining time. */
export function ttlLabel(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** A decision is possible only while the approval is pending and unexpired. */
export function canDecide(approval: Pick<ApprovalView, "status">, remainingS: number): boolean {
  return approval.status === "PENDING" && remainingS > 0;
}

/** The precondition list the planner stored with the action. */
export function plannedCheck(action: ActionView): PlanCheck | null {
  const raw = (action.args as Record<string, unknown>)["planned_check"];
  return raw && typeof raw === "object" ? (raw as PlanCheck) : null;
}

export function diagnosisOf(action: ActionView): {
  root_cause?: string;
  fault_class?: string;
  confidence?: number;
  summary?: string;
} {
  const raw = (action.args as Record<string, unknown>)["diagnosis"];
  return raw && typeof raw === "object" ? (raw as Record<string, never>) : {};
}

export function citedCodes(action: ActionView): string[] {
  const raw = (action.args as Record<string, unknown>)["evidence_codes"];
  return Array.isArray(raw) ? raw.map(String) : [];
}
