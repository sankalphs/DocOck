"use client";

import { useEffect, useRef, useState } from "react";

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
 */
export function usePolling<T>(
  key: string,
  fetcher: (signal: AbortSignal) => Promise<T>,
  intervalMs = 5000,
): PollState<T> & { refresh: () => void } {
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const [state, setState] = useState<PollState<T>>({
    data: null,
    error: null,
    updatedAt: null,
    loading: true,
  });
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const run = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
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
      }
    };

    void run();
    const timer = setInterval(run, intervalMs);

    return () => {
      cancelled = true;
      controller.abort();
      clearInterval(timer);
    };
  }, [key, intervalMs, nonce]);

  return { ...state, refresh: () => setNonce((value) => value + 1) };
}
