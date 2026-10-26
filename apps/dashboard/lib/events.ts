/**
 * Event types the dashboard subscribes to.
 *
 * `EventSource` has no wildcard listener: a named SSE event (`event: fault.injected`)
 * is only delivered to listeners registered for that exact name. Keep this list in
 * sync with `app/events/bus.py` publishers as phases add them.
 */
export const EVENT_TYPES = [
  "system.ready",
  "fault.injected",
  "fault.active",
  "fault.reverted",
  "fault.expired",
  "fault.failed",
  "fault.revert_failed",
  "fault.reset_all",
  "fault.reconciled",
  "incident.opened",
  "incident.transition",
  "incident.signal",
  "incident.analysis",
  "incident.closed",
  "agent.run.started",
  "agent.step",
  "agent.run.finished",
  "memory.stored",
  "approval.requested",
  "approval.approved",
  "approval.rejected",
  "approval.expired",
  "approval.withdrawn",
  "action.started",
  "action.executed",
  "action.failed",
  "action.refused",
  "action.rolled_back",
  "verification.started",
  "verification.progress",
  "verification.completed",
  "report.generated",
] as const;

export type EventType = (typeof EVENT_TYPES)[number];

export type LabEvent = {
  id: number;
  type: string;
  data: Record<string, unknown>;
  receivedAt: number;
};

/** Namespace prefix used for colour-coding in the live feed. */
export function eventGroup(type: string): string {
  return type.split(".")[0] ?? "system";
}
