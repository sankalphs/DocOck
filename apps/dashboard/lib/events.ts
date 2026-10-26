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
  "incident.closed",
  "agent.started",
  "agent.step",
  "agent.completed",
  "agent.failed",
  "approval.requested",
  "approval.decided",
  "action.started",
  "action.completed",
  "verification.completed",
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
