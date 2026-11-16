"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type PollState<T> = {
  data: T | null;
  error: string | null;
  updatedAt: number | null;
  loading: boolean;
};

/**
 * Poll a fetcher on an interval and expose the last good value plus the last
 * error. Keeps showing stale data while a refresh fails — an operator console
 * that blanks out on one dropped poll is useless.
 *
 * - At most one request is in flight per key: a slow upstream (Jaeger under a
 *   latency fault) must not stack requests whose answers land out of order and
 *   overwrite a newer value with an older one.
 * - Hidden tabs skip their ticks, and the moment the tab is visible again it
 *   refreshes instead of showing data up to one interval old.
 */
export function usePolling<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs = 5000,
): PollState<T> & { refresh: () => void } {
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    updatedAt: null,
    loading: true,
  });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    const controller = new AbortController();

    const run = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      if (inFlight) return;
      inFlight = true;
      try {
        const data = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        setState({ data, error: null, updatedAt: Date.now(), loading: false });
      } catch (error) {
        if (cancelled) return;
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState((previous) => ({
          ...previous,
          error: error instanceof Error ? error.message : "Request failed",
          loading: false,
        }));
      } finally {
        inFlight = false;
      }
    };

    const onVisible = () => {
      if (!document.hidden) void run();
    };

    void run();
    const timer = setInterval(run, intervalMs);
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisible);
    }

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(timer);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisible);
      }
    };
  }, [key, intervalMs, nonce]);

  const refresh = useCallback(() => setNonce((value) => value + 1), []);
  return { ...state, refresh };
}
