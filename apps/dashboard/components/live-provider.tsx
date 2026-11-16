"use client";

import { createContext, useContext, useEffect, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import type { HealthReport } from "@/lib/api/client";
import {
  affectsAttention,
  attentionTitle,
  fetchAttention,
  stripAttention,
  type Attention,
} from "@/lib/api/attention";
import { useEventStream } from "@/lib/hooks/use-event-stream";
import { useHealth, type HealthState } from "@/lib/hooks/use-health";
import { usePolling } from "@/lib/hooks/use-polling";

type LiveValue = {
  health: HealthState;
  stream: ReturnType<typeof useEventStream>;
  attention: Attention | null;
};

const LiveContext = createContext<LiveValue | null>(null);

/**
 * One health poll, one attention poll and one SSE connection for the entire
 * app. Mounted in the shell, so navigating between sections does not drop the
 * event stream.
 */
export function LiveProvider({ children }: { children: ReactNode }) {
  const health = useHealth(5000);
  const stream = useEventStream(60);
  const pathname = usePathname();

  // Counts change only on incident/approval/fault events; the slow poll is the
  // safety net for an approval that expires without one.
  const trigger = stream.events.find((event) => affectsAttention(event.type));
  const attention = usePolling(`attention:${trigger?.id ?? 0}`, fetchAttention, 15000);
  const counts = attention.data;

  useEffect(() => {
    document.title = attentionTitle(stripAttention(document.title), counts);
  }, [counts, pathname]);

  return (
    <LiveContext.Provider value={{ health, stream, attention: counts }}>
      {children}
    </LiveContext.Provider>
  );
}

export function useLive(): LiveValue {
  const value = useContext(LiveContext);
  if (!value) throw new Error("useLive must be used inside <LiveProvider>");
  return value;
}

export type { HealthReport };
