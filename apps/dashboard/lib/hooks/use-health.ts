"use client";

import { useCallback } from "react";

import { fetchHealth, type HealthReport } from "@/lib/api/client";
import { usePolling, type PollState } from "@/lib/hooks/use-polling";

export type HealthState = PollState<HealthReport> & { refresh: () => void };

export function useHealth(intervalMs = 5000): HealthState {
  const fetcher = useCallback((signal: AbortSignal) => fetchHealth(signal), []);
  return usePolling("health", fetcher, intervalMs);
}
