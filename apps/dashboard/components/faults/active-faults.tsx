"use client";

import { RotateCcw, ShieldAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { FaultView } from "@/lib/api/faults";
import {
  countdown,
  formatParams,
  openStatus,
  statusLabel,
  statusVariant,
} from "@/components/faults/fault-status";
import { formatClock, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

export function ActiveFaults({
  faults,
  now,
  busyId,
  onRevert,
}: {
  faults: FaultView[];
  now: number;
  busyId: number | null;
  onRevert: (id: number) => void;
}) {
  const open = faults.filter((fault) => openStatus(fault.status));

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <ShieldAlert className="size-3.5 text-destructive" aria-hidden />
            Active faults
            <Badge variant={open.length > 0 ? "destructive" : "success"}>{open.length}</Badge>
          </CardTitle>
          <CardDescription>
            A flag fault stays <span className="text-warning">propagating</span> until the service
            shows the symptom — the demo&apos;s flagd SDK clients take 30–75 s.
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent>
        {open.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
            No active faults. The lab is clean.
          </div>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {open.map((fault) => (
              <FaultCard
                key={fault.id}
                fault={fault}
                now={now}
                busy={busyId === fault.id}
                onRevert={() => onRevert(fault.id)}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function FaultCard({
  fault,
  now,
  busy,
  onRevert,
}: {
  fault: FaultView;
  now: number;
  busy: boolean;
  onRevert: () => void;
}) {
  const remaining = countdown(fault.injected_at, fault.expires_at, now);
  const barTone =
    remaining.fraction <= 0.1
      ? "bg-destructive"
      : remaining.fraction <= 0.3
        ? "bg-warning"
        : "bg-primary";

  return (
    <div
      data-testid="active-fault"
      className="rounded-lg border border-border/80 bg-background/40 p-3"
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{fault.label}</p>
          <p className="mt-0.5 text-[11px] text-muted-foreground">
            <span className="font-mono">{fault.target}</span> · {fault.mechanism} ·{" "}
            {formatParams(fault.params)}
          </p>
        </div>
        <Badge variant={statusVariant(fault.status)} className="uppercase">
          {fault.status === "PROPAGATING" ? (
            <span className="mr-1 size-1.5 animate-pulse rounded-full bg-current" aria-hidden />
          ) : null}
          {statusLabel(fault.status)}
        </Badge>
      </div>

      <div className="mt-3">
        <div className="flex items-baseline justify-between text-[11px]">
          <span className="text-muted-foreground">TTL remaining</span>
          <span className={cn("tabular font-mono", remaining.expired && "text-destructive")}>
            {remaining.label}
          </span>
        </div>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
          <div
            className={cn("h-full rounded-full transition-[width] duration-500", barTone)}
            style={{ width: `${Math.round(remaining.fraction * 100)}%` }}
          />
        </div>
        <div className="mt-1 flex justify-between gap-2 text-[10px] text-muted-foreground">
          <span>
            injected {formatClock(fault.injected_at)} ({formatRelative(new Date(fault.injected_at).getTime(), now)})
          </span>
          <span>expires {fault.expires_at ? formatClock(fault.expires_at) : "never"}</span>
        </div>
      </div>

      {fault.observed?.detail ? (
        <p className="mt-2 rounded border border-border/60 bg-muted/30 px-2 py-1 font-mono text-[10px] text-muted-foreground">
          observed: {fault.observed.detail}
        </p>
      ) : (
        <p className="mt-2 rounded border border-warning/30 bg-warning/5 px-2 py-1 text-[10px] text-warning">
          waiting for {fault.symptom.detail || fault.symptom.probe} on {fault.symptom.service}
        </p>
      )}

      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-[10px] text-muted-foreground">fault #{fault.id}</span>
        <Button variant="outline" size="sm" disabled={busy} onClick={onRevert}>
          <RotateCcw className={cn("size-3.5", busy && "animate-spin")} />
          {busy ? "Reverting…" : "Revert"}
        </Button>
      </div>
    </div>
  );
}
