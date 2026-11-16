"use client";

import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";

import { fetchApprovals } from "@/lib/api/approvals";
import {
  alertBody,
  alertSnapshot,
  currentPermission,
  notifyAlertListeners,
  parseAlertSnapshot,
  SERVER_ALERT_SNAPSHOT,
  shouldAlert,
  subscribeAlerts,
  writeAlertPreference,
  type AlertPermission,
} from "@/lib/approval-alerts";
import type { LabEvent } from "@/lib/events";
import { usePolling } from "@/lib/hooks/use-polling";

export type PendingApprovals = {
  pending: number | null;
  alerts: {
    enabled: boolean;
    permission: AlertPermission;
    toggle: () => Promise<void>;
  };
};

/**
 * How many approvals wait for a decision, refreshed on every approval or
 * incident event, plus opt-in desktop alerts when that number grows.
 */
export function usePendingApprovals(events: LabEvent[]): PendingApprovals {
  const trigger = events.find(
    (event) => event.type.startsWith("approval.") || event.type === "incident.transition",
  );
  const poll = usePolling(
    `pending-approvals:${trigger?.id ?? 0}`,
    useCallback((signal: AbortSignal) => fetchApprovals("pending", 1, signal), []),
    15000,
  );
  const pending = poll.data?.pending ?? null;

  const router = useRouter();
  // Browser-only state; the server snapshot keeps SSR and hydration in agreement.
  const { permission, enabled } = parseAlertSnapshot(
    useSyncExternalStore(subscribeAlerts, alertSnapshot, () => SERVER_ALERT_SNAPSHOT),
  );

  const previous = useRef<number | null>(null);
  useEffect(() => {
    if (pending === null) return;
    const before = previous.current;
    previous.current = pending;
    if (!enabled || permission !== "granted" || !shouldAlert(before, pending)) return;
    try {
      const notification = new Notification("DocOck · approval needed", {
        body: alertBody(pending, pending - (before ?? 0)),
        tag: "docock-approval",
        icon: "/icon.svg",
      });
      notification.onclick = () => {
        window.focus();
        router.push("/approvals");
        notification.close();
      };
    } catch {
      // Some browsers only allow notifications from a service worker.
    }
  }, [pending, enabled, permission, router]);

  const toggle = useCallback(async () => {
    if (enabled) {
      writeAlertPreference(false);
      return;
    }
    let next = currentPermission();
    if (next === "default") next = await Notification.requestPermission();
    writeAlertPreference(next === "granted");
    notifyAlertListeners(); // permission changed even if the preference did not
  }, [enabled]);

  return { pending, alerts: { enabled, permission, toggle } };
}
