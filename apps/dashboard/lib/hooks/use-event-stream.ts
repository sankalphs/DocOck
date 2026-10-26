"use client";

import { useEffect, useRef, useState } from "react";

import { API_BASE_URL } from "@/lib/api/base-url";
import { EVENT_TYPES, type LabEvent } from "@/lib/events";

export type StreamStatus = "connecting" | "open" | "reconnecting" | "closed";

function parseData(raw: string): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed === "object" && parsed !== null) {
      return parsed as Record<string, unknown>;
    }
    return { value: parsed };
  } catch {
    return { raw };
  }
}

/**
 * One EventSource for the whole app. The server replays from `Last-Event-ID`
 * automatically after a reconnect, so a reloaded tab catches up instead of
 * silently missing the fault it was opened to watch.
 */
export function useEventStream(maxEvents = 40) {
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [events, setEvents] = useState<LabEvent[]>([]);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const [reconnects, setReconnects] = useState(0);
  const openedOnce = useRef(false);

  useEffect(() => {
    const source = new EventSource(`${API_BASE_URL}/api/events`);

    const onOpen = () => {
      if (openedOnce.current) setReconnects((count) => count + 1);
      openedOnce.current = true;
      setStatus("open");
    };

    const onError = () => {
      setStatus(source.readyState === EventSource.CLOSED ? "closed" : "reconnecting");
    };

    const onEvent = (event: MessageEvent<string>) => {
      const receivedAt = Date.now();
      const parsed: LabEvent = {
        id: Number(event.lastEventId || 0),
        type: event.type,
        data: parseData(event.data),
        receivedAt,
      };
      setLastEventAt(receivedAt);
      setEvents((previous) => [parsed, ...previous].slice(0, maxEvents));
    };

    source.addEventListener("open", onOpen);
    source.addEventListener("error", onError);
    for (const type of EVENT_TYPES) {
      source.addEventListener(type, onEvent as EventListener);
    }

    return () => {
      source.removeEventListener("open", onOpen);
      source.removeEventListener("error", onError);
      for (const type of EVENT_TYPES) {
        source.removeEventListener(type, onEvent as EventListener);
      }
      source.close();
    };
  }, [maxEvents]);

  return { status, events, lastEvent: events[0] ?? null, lastEventAt, reconnects };
}
