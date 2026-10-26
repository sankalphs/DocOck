"use client";

import { useState } from "react";
import { Activity, ArrowRight, Check, Clock, RefreshCw, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { suspectSignals, type IncidentDetail } from "@/lib/api/incidents";
import { eventBadge, severityBadge, statusBadge } from "@/components/incidents/badges";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

function metric(value: number | null, signal: string): string {
  if (value === null) return "—";
  if (signal === "error_rate") return `${(value * 100).toFixed(1)}%`;
  if (signal === "p95_latency") return `${value.toFixed(0)} ms`;
  return value.toFixed(2);
}

function payloadSummary(payload: Record<string, unknown>): string {
  if (typeof payload.detail === "string" && payload.detail) return payload.detail;
  if (Array.isArray(payload.services)) return `services: ${payload.services.join(", ")}`;
  if (typeof payload.service === "string") return payload.service;
  if (typeof payload.reason === "string") return String(payload.reason);
  return "";
}

export function IncidentDetailPanel({
  incident,
  busy,
  analyzing,
  onClose,
  onAnalyze,
  onRefresh,
}: {
  incident: IncidentDetail;
  busy: boolean;
  analyzing: boolean;
  onClose: () => void;
  onAnalyze: () => void;
  onRefresh: () => void;
}) {
  const [confirmClose, setConfirmClose] = useState(false);
  const terminal = ["RESOLVED", "ESCALATED", "REJECTED"].includes(incident.status);
  const detector = (incident.trigger.detector ?? {}) as Record<string, unknown>;
  const primarySignal = (incident.trigger.primary_signal ?? "") as string;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="tabular text-xs text-muted-foreground">{incident.public_id}</span>
              <Badge variant={severityBadge(incident.severity)} className="uppercase">
                {incident.severity}
              </Badge>
              <Badge variant={statusBadge(incident.status)} className="uppercase">
                {incident.status}
              </Badge>
              {incident.signals.length > 0 ? (
                <Badge variant="outline">
                  {incident.signals.length} signal{incident.signals.length === 1 ? "" : "s"}
                </Badge>
              ) : null}
            </div>
            <CardTitle className="mt-1 text-base">{incident.title}</CardTitle>
            <CardDescription>
              primary <span className="font-mono">{incident.primary_service ?? "—"}</span>
              {incident.signature ? (
                <>
                  {" "}
                  · signature <code className="font-mono text-[10px]">{incident.signature}</code>
                </>
              ) : null}{" "}
              · opened {formatClock(incident.opened_at)}
              {incident.closed_at ? ` · closed ${formatClock(incident.closed_at)}` : ""} · duration{" "}
              {incident.duration_s ?? 0}s
            </CardDescription>
            {incident.services.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1">
                {incident.services.map((service) => (
                  <Badge key={service} variant="outline" className="font-mono">
                    {service}
                  </Badge>
                ))}
              </div>
            ) : null}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={onRefresh} title="Refresh">
              <RefreshCw className={cn("size-3.5", busy && "animate-spin")} />
            </Button>
            <Button variant="default" size="sm" onClick={onAnalyze} disabled={analyzing}>
              <Activity className={cn("size-3.5", analyzing && "animate-pulse")} />
              {analyzing ? "Analyzing…" : "Analyze"}
            </Button>
            {!terminal ? (
              confirmClose ? (
                <>
                  <Button variant="destructive" size="sm" onClick={onClose} disabled={busy}>
                    <Check className="size-3.5" />
                    Confirm close
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setConfirmClose(false)}>
                    <X className="size-3.5" />
                  </Button>
                </>
              ) : (
                <Button variant="outline" size="sm" onClick={() => setConfirmClose(true)}>
                  Close incident
                </Button>
              )
            ) : null}
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 text-[11px] text-muted-foreground sm:grid-cols-3">
            <span>
              window <code className="font-mono">{String(detector.window ?? "—")}</code>
            </span>
            <span>
              cadence{" "}
              <code className="font-mono">
                {detector.interval_s ? `${String(detector.interval_s)} s` : "—"}
              </code>
            </span>
            <span>
              consecutive breaches{" "}
              <code className="font-mono">{String(detector.consecutive ?? "—")}</code>
            </span>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Activity className="size-3.5 text-warning" aria-hidden />
                Signals
              </CardTitle>
              <CardDescription>
                Every breached observable the detector attached. The first one opened the incident
                ({primarySignal || "error_rate"}).
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {incident.signals.map((signal) => (
                <div
                  key={signal.signature}
                  className="rounded-lg border border-border/70 bg-background/40 p-2.5"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">{signal.service}</span>
                    <Badge variant="outline">{signal.signal}</Badge>
                    <span className="tabular ml-auto text-[11px]">
                      {metric(signal.value, signal.signal)}
                      <span className="text-muted-foreground">
                        {" "}
                        / base {metric(signal.baseline, signal.signal)}
                      </span>
                    </span>
                  </div>
                  <p className="mt-1 text-[10px] text-muted-foreground">{signal.detail}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle className="flex items-center gap-2">
                <Clock className="size-3.5 text-primary" aria-hidden />
                Timeline
              </CardTitle>
              <CardDescription>
                Append-only events; replayed by the SSE stream after a reload.
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <ol className="space-y-2">
              {incident.events.map((event) => (
                <li key={event.seq} className="flex gap-2">
                  <div className="flex flex-col items-center pt-1">
                    <span className="size-1.5 rounded-full bg-primary" aria-hidden />
                    <span className="mt-1 h-full w-px bg-border" aria-hidden />
                  </div>
                  <div className="min-w-0 flex-1 pb-1">
                    <div className="flex items-center gap-2">
                      <Badge variant={eventBadge(event.kind)} className="text-[10px]">
                        {event.kind}
                      </Badge>
                      <span className="tabular text-[10px] text-muted-foreground">
                        {formatClock(event.at)}
                      </span>
                      <span className="ml-auto text-[10px] text-muted-foreground">#{event.seq}</span>
                    </div>
                    {event.payload.from && event.payload.to ? (
                      <p className="mt-0.5 flex items-center gap-1 text-[10px] text-muted-foreground">
                        {String(event.payload.from)}
                        <ArrowRight className="size-2.5" />
                        {String(event.payload.to)}
                      </p>
                    ) : null}
                    <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                      {payloadSummary(event.payload)}
                    </p>
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <div>
              <CardTitle>Evidence</CardTitle>
              <CardDescription>
                Stable E#### codes from the deterministic analysis. Re-running adds only new
                observations.
              </CardDescription>
            </div>
            <Badge variant="outline">{incident.evidence.length}</Badge>
          </CardHeader>
          <CardContent>
            {incident.evidence.length > 0 ? (
              <ul className="space-y-1.5">
                {incident.evidence.map((row) => (
                  <li key={row.code} className="rounded border border-border/60 px-2 py-1.5">
                    <div className="flex items-center gap-2">
                      <code className="font-mono text-[11px] text-primary">{row.code}</code>
                      <Badge variant="outline" className="text-[10px]">
                        {row.type}
                      </Badge>
                      <span className="truncate text-[10px] text-muted-foreground">
                        {row.service ?? row.source}
                      </span>
                    </div>
                    <p className="mt-0.5 truncate font-mono text-[10px] text-muted-foreground">
                      {JSON.stringify(row.data)}
                    </p>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                No evidence yet. Analyze attaches metric anomalies, onset ordering, error origin,
                log templates, container/flag state, and topology.
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div>
              <CardTitle>Suspects</CardTitle>
              <CardDescription>
                Deterministic ranking; each row lists the signals that contributed.
              </CardDescription>
            </div>
            <Badge variant="outline">{incident.suspects.length}</Badge>
          </CardHeader>
          <CardContent>
            {incident.suspects.length > 0 ? (
              <ul className="space-y-2">
                {incident.suspects.map((suspect) => (
                  <li key={`${suspect.rank}-${suspect.service}`}>
                    <div className="flex items-center gap-2 text-xs">
                      <span className="tabular w-4 text-muted-foreground">{suspect.rank}</span>
                      <span className="font-mono">{suspect.service}</span>
                      <span className="tabular ml-auto">{(suspect.score * 100).toFixed(0)}%</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${Math.round(suspect.score * 100)}%` }}
                      />
                    </div>
                    <ul className="mt-1 space-y-0.5">
                      {suspectSignals(suspect.signals)
                        .filter((signal) => signal.contribution > 0)
                        .map((signal) => (
                          <li
                            key={signal.name}
                            className="flex items-baseline gap-2 text-[10px] text-muted-foreground"
                            title={`value ${signal.value} x weight ${signal.weight}`}
                          >
                            <span className="w-28 shrink-0 font-mono">{signal.name}</span>
                            <span className="tabular w-10 shrink-0 text-right text-foreground">
                              +{signal.contribution.toFixed(3)}
                            </span>
                            <span className="truncate">{signal.detail}</span>
                          </li>
                        ))}
                    </ul>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">
                No ranking yet. Analyze scores error origin, onset, error ratio, latency,
                throughput, and container state against per-service baselines.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Faults in the window</CardTitle>
            <CardDescription>
              Injected faults that were open at any point between open and close.
            </CardDescription>
          </div>
          <Badge variant="outline">{incident.faults.length}</Badge>
        </CardHeader>
        <CardContent>
          {incident.faults.length > 0 ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {incident.faults.map((fault) => (
                <div
                  key={fault.id}
                  className="rounded-lg border border-border/70 bg-background/40 p-2 text-xs"
                >
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{fault.fault_id}</span>
                    <Badge variant="outline" className="ml-auto text-[10px]">
                      {fault.status}
                    </Badge>
                  </div>
                  <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                    {fault.target} · {JSON.stringify(fault.params)}
                  </p>
                  <p className="text-[10px] text-muted-foreground">
                    {formatClock(fault.injected_at)}
                    {fault.reset_at ? ` → ${formatClock(fault.reset_at)}` : " (still open)"}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">
              No DocOck fault overlapped this window (the symptom may have cleared on its own).
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
