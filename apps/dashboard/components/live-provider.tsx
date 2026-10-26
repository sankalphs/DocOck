"use client";

import { createContext, useContext, type ReactNode } from "react";

import type { HealthReport } from "@/lib/api/client";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { useHealth, type HealthState } from "@/lib/hooks/use-health";

type LiveValue = {
  health: HealthState;
  stream: ReturnType<typeof useEventStream>;
};

const LiveContext = createContext<LiveValue | null>(null);

/**
 * One health poll and one SSE connection for the entire app. Mounted in the
 * shell, so navigating between sections does not drop the event stream.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const health = useHealth(5000);
  const stream = useEventStream(60);

  return <LiveContext.Provider value={{ health, stream }}>{children}</LiveContext.Provider>;
}

export function useLive(): LiveValue {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLive must be used inside <LiveProvider>");
  return value;
}

export type { HealthReport };
