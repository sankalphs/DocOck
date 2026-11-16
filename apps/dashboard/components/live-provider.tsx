"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { HealthReport } from "@/lib/api/client";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { useHealth, type HealthState } from "@/lib/hooks/use-health";
import { usePendingApprovals, type PendingApprovals } from "@/lib/hooks/use-pending-approvals";

type LiveValue = {
  health: HealthState;
  stream: ReturnType<typeof useEventStream>;
  approvals: PendingApprovals;
};

const LiveContext = createContext<LiveValue | null>(null);

/**
 * One health poll, one SSE connection and one pending-approvals count for the
 * entire app. Mounted in the
 * shell, so navigating between sections does not drop the event stream.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const health = useHealth(5000);
  const stream = useEventStream(60);
  const approvals = usePendingApprovals(stream.events);

  return (
    <LiveContext.Provider value={{ health, stream, approvals }}>{children}</LiveContext.Provider>
  );
}

export function useLive(): LiveValue {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLive must be used inside <LiveProvider>");
  return value;
}

export type { HealthReport };
