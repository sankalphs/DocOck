/**
 * Desktop alerts for remediation approvals.
 *
 * Nothing executes without a human, so the human has to know a decision is
 * waiting. Alerts are driven by the *pending count* rather than by
 * `approval.requested` events: the SSE stream replays history after every
 * reconnect, and an event-driven alert would re-announce old approvals.
 */

export const ALERTS_STORAGE_KEY = "docock.approval-alerts";

export type AlertPermission = NotificationPermission | "unsupported";

/** Alert only when the count grows after the first successful read (the baseline). */
export function shouldAlert(previous: number | null, next: number): boolean {
  return previous !== null && next > previous;
}

export function alertBody(pending: number, added: number): string {
  const head =
    added === 1 ? "A remediation awaits your approval." : `${added} new remediations await approval.`;
  return pending > added ? `${head} ${pending} pending in total.` : head;
}

export function readAlertPreference(): boolean {
  try {
    return window.localStorage.getItem(ALERTS_STORAGE_KEY) === "on";
  } catch {
    return false;
  }
}

// Fallback for when storage is unavailable, so the toggle still works per tab.
let memoryPreference: boolean | null = null;
const listeners = new Set<() => void>();

export function readAlertPreferenceSafe(): boolean {
  return memoryPreference ?? readAlertPreference();
}

export function writeAlertPreference(enabled: boolean): void {
  memoryPreference = enabled;
  try {
    window.localStorage.setItem(ALERTS_STORAGE_KEY, enabled ? "on" : "off");
  } catch {
    // Private mode or blocked storage: the in-memory value covers this tab.
  }
  notifyAlertListeners();
}

export function notifyAlertListeners(): void {
  for (const listener of listeners) listener();
}

/** `useSyncExternalStore` plumbing: the browser is the store. */
export function subscribeAlerts(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export type AlertSnapshot = `${AlertPermission}|${"on" | "off"}`;

export function alertSnapshot(): AlertSnapshot {
  return `${currentPermission()}|${readAlertPreferenceSafe() ? "on" : "off"}`;
}

export const SERVER_ALERT_SNAPSHOT: AlertSnapshot = "unsupported|off";

export function parseAlertSnapshot(snapshot: AlertSnapshot): {
  permission: AlertPermission;
  enabled: boolean;
} {
  const [permission, preference] = snapshot.split("|") as [AlertPermission, string];
  // A remembered "on" is meaningless once the browser revoked permission.
  return { permission, enabled: preference === "on" && permission === "granted" };
}

export function currentPermission(): AlertPermission {
  if (typeof window === "undefined" || !("Notification" in window)) return "unsupported";
  return Notification.permission;
}
