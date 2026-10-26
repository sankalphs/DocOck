"use client";

import { useCallback, useMemo } from "react";

import { fetchMetric, fetchServices } from "@/lib/api/telemetry";
import type { MetricResult, ServiceInventory } from "@/lib/api/telemetry";
import { usePolling } from "@/lib/hooks/use-polling";

export type OverviewData = {
  errorRate: MetricResult;
  p95: MetricResult;
  throughput: MetricResult;
  inventory: ServiceInventory;
};

const RANGE_SECONDS = 900;
const STEP_SECONDS = 30;

/**
 * One poll for the whole overview: three range queries plus the inventory.
 * Metrics flush every 10 s, so polling faster than that only burns CPU.
 */
export function useOverview() {
  const fetcher = useCallback(async (signal: AbortSignal): Promise<OverviewData> => {
    const [errorRate, p95, throughput, inventory] = await Promise.all([
      fetchMetric(
        { metric: "error_rate", window: "2m", rangeSeconds: RANGE_SECONDS, step: STEP_SECONDS },
        signal,
      ),
      fetchMetric(
        { metric: "p95_latency", window: "2m", rangeSeconds: RANGE_SECONDS, step: STEP_SECONDS },
        signal,
      ),
      fetchMetric(
        { metric: "throughput", window: "2m", rangeSeconds: RANGE_SECONDS, step: STEP_SECONDS },
        signal,
      ),
      fetchServices("5m", signal),
    ]);
    return { errorRate, p95, throughput, inventory };
  }, []);

  return usePolling("overview", fetcher, 10_000);
}

export function useServiceNames() {
  const fetcher = useCallback(async (signal: AbortSignal) => {
    const inventory = await fetchServices("5m", signal);
    return (inventory.services ?? []).map((service) => service.name);
  }, []);
  const state = usePolling("service-names", fetcher, 30_000);
  return useMemo(() => state.data ?? [], [state.data]);
}
