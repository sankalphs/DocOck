"use client";

import { useEffect, useState } from "react";

import { fetchTrace } from "@/lib/api/telemetry";
import type { TraceDetail } from "@/lib/api/telemetry";

type DetailState = {
  traceId: string | null;
  detail: TraceDetail | null;
  error: string | null;
};

/**
 * Load a trace on selection. State is keyed by trace id rather than reset in the
 * effect body, so switching traces never flashes the previous waterfall.
 */
export function useTraceDetail(traceId: string | null) {
  const [state, setState] = useState<DetailState>({ traceId: null, detail: null, error: null });

  useEffect(() => {
    if (!traceId) return;
    const controller = new AbortController();
    let cancelled = false;

    fetchTrace(traceId, controller.signal)
      .then((detail) => {
        if (!cancelled) setState({ traceId, detail, error: null });
      })
      .catch((caught: unknown) => {
        if (cancelled || (caught instanceof DOMException && caught.name === "AbortError")) return;
        setState({
          traceId,
          detail: null,
          error: caught instanceof Error ? caught.message : "Trace lookup failed",
        });
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [traceId]);

  const current = state.traceId === traceId ? state : null;
  return {
    detail: current?.detail ?? null,
    error: current?.error ?? null,
    loading: traceId !== null && current === null,
  };
}
