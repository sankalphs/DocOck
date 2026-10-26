"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchFaultCatalog,
  fetchFaults,
  injectFault,
  resetAllFaults,
  revertFault,
  type FaultCatalog,
  type FaultList,
  type InjectionRequest,
  type InjectionResult,
  type ResetReport,
} from "@/lib/api/faults";
import { usePolling } from "@/lib/hooks/use-polling";

/** Local 1 s clock, used by the countdowns. */
export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}

export function useFaultCatalog() {
  const fetcher = useCallback((signal: AbortSignal) => fetchFaultCatalog(signal), []);
  return usePolling<FaultCatalog>("fault-catalog", fetcher, 60_000);
}

/**
 * The fault list polls every 4 s and refetches immediately when an SSE
 * `fault.*` event arrives (the event id is part of the polling key). The
 * server remains the source of truth for status; the local countdown only
 * ticks the display between polls.
 */
export function useFaults(eventId: number | null) {
  const fetcher = useCallback(
    (signal: AbortSignal) => fetchFaults({ status: "all", limit: 60 }, signal),
    [],
  );
  return usePolling<FaultList>(`faults:${eventId ?? 0}`, fetcher, 4000);
}

export function useFaultActions(onChanged: () => void) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const run = useCallback(
    async <T,>(label: string, action: () => Promise<T>): Promise<T | null> => {
      setBusy(label);
      setError(null);
      try {
        const result = await action();
        if (mounted.current) onChanged();
        return result;
      } catch (exc) {
        if (mounted.current) {
          setError(exc instanceof Error ? exc.message : "Request failed");
        }
        return null;
      } finally {
        if (mounted.current) setBusy(null);
      }
    },
    [onChanged],
  );

  return {
    busy,
    error,
    clearError: () => setError(null),
    inject: (body: InjectionRequest) =>
      run<InjectionResult>("inject", () => injectFault(body)),
    dryRun: (body: InjectionRequest) =>
      run<InjectionResult>("dry-run", () => injectFault({ ...body, dry_run: true })),
    revert: (id: number) => run("revert", () => revertFault(id)),
    resetAll: () => run<ResetReport>("reset-all", () => resetAllFaults()),
  };
}
