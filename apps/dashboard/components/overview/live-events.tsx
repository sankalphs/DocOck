"use client";

import { Radio } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatClock, formatRelative } from "@/lib/format";
import { eventGroup, type LabEvent } from "@/lib/events";
import { cn } from "@/lib/utils";

const GROUP_CLASS: Record<string, string> = {
  system: "border-border bg-muted/50 text-muted-foreground",
  fault: "border-destructive/30 bg-destructive/10 text-destructive",
  incident: "border-warning/30 bg-warning/10 text-warning",
  agent: "border-primary/30 bg-primary/10 text-primary",
  approval: "border-primary/30 bg-primary/10 text-primary",
  action: "border-warning/30 bg-warning/10 text-warning",
  verification: "border-success/30 bg-success/10 text-success",
};

function EventRow({ event }: { event: LabEvent }) {
  const group = eventGroup(event.type);
  const payload = JSON.stringify(event.data);
  return (
    <li className="border-b border-border/50 py-2 last:border-0">
      <div className="flex items-center gap-2">
        <span className="tabular text-[10px] text-muted-foreground">#{event.id}</span>
        <span
          className={cn(
            "rounded border px-1.5 py-0.5 text-[10px] font-medium",
            GROUP_CLASS[group] ?? GROUP_CLASS.system,
          )}
        >
          {event.type}
        </span>
        <span className="tabular ml-auto text-[10px] text-muted-foreground">
          {formatClock(new Date(event.receivedAt).toISOString())}
        </span>
      </div>
      <code className="mt-1 block truncate font-mono text-[10px] text-muted-foreground/80">
        {payload.length > 120 ? `${payload.slice(0, 120)}…` : payload}
      </code>
    </li>
  );
}

export function LiveEvents() {
  const { stream } = useLive();

  return (
    <Card className="h-full">
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Radio className="size-3.5 text-primary" aria-hidden />
            Live events
          </CardTitle>
          <CardDescription>
            SSE from <code className="font-mono text-[10px]">/api/events</code>, replayed on
            reconnect.
          </CardDescription>
        </div>
        <Badge
          variant={
            stream.status === "open"
              ? "success"
              : stream.status === "reconnecting"
                ? "warning"
                : "destructive"
          }
        >
          {stream.status}
        </Badge>
      </CardHeader>
      <CardContent>
        {stream.events.length > 0 ? (
          <ul className="-mt-2">
            {stream.events.slice(0, 8).map((event) => (
              <EventRow key={`${event.id}-${event.receivedAt}`} event={event} />
            ))}
          </ul>
        ) : (
          <div className="rounded-lg border border-dashed border-border/70 px-3 py-4 text-xs text-muted-foreground">
            <p className="flex items-center gap-2">
              <span className="relative flex size-2">
                <span className="absolute inline-flex size-full animate-ping rounded-full bg-primary/60" />
                <span className="relative inline-flex size-2 rounded-full bg-primary" />
              </span>
              {stream.status === "open"
                ? "Connected. Waiting for events."
                : "Connecting to the event stream…"}
            </p>
            <p className="mt-2 leading-relaxed">
              The API publishes <code className="font-mono">system.ready</code> on startup.
              Fault, incident, agent, and approval events arrive with P6–P14.
            </p>
          </div>
        )}
        <div className="mt-3 flex items-center justify-between text-[10px] text-muted-foreground">
          <span>
            {stream.lastEventAt
              ? `last event ${formatRelative(stream.lastEventAt)}`
              : "no events yet"}
          </span>
          {stream.reconnects > 0 ? <span>{stream.reconnects} reconnect(s)</span> : null}
        </div>
      </CardContent>
    </Card>
  );
}
