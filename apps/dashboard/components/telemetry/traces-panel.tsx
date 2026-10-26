"use client";

import { useCallback, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { TraceWaterfall } from "@/components/telemetry/waterfall";
import { fetchTraces } from "@/lib/api/telemetry";
import { jaegerTraceUrl } from "@/lib/api/telemetry";
import { usePolling } from "@/lib/hooks/use-polling";
import { useTraceDetail } from "@/lib/hooks/use-trace-detail";
import { formatClock, formatMetric } from "@/lib/format";
import { cn } from "@/lib/utils";

const LOOKBACKS = [
  { value: 300, label: "5m" },
  { value: 900, label: "15m" },
  { value: 3600, label: "1h" },
];

export function TracesPanel({
  serviceNames,
  selectedTraceId,
  onSelectTrace,
  onOpenLogs,
}: {
  serviceNames: string[];
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  onOpenLogs: (traceId: string) => void;
}) {
  const [service, setService] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const [minDuration, setMinDuration] = useState(0);
  const [lookback, setLookback] = useState(900);

  const fetcher = useCallback(
    (signal: AbortSignal) =>
      fetchTraces(
        {
          service: service || undefined,
          errorsOnly,
          minDurationMs: minDuration > 0 ? minDuration : undefined,
          lookbackSeconds: lookback,
          limit: 25,
        },
        signal,
      ),
    [service, errorsOnly, minDuration, lookback],
  );
  const { data, loading, error, refresh, updatedAt } = usePolling(
    `traces:${service}:${errorsOnly}:${minDuration}:${lookback}`,
    fetcher,
    15_000,
  );
  const detail = useTraceDetail(selectedTraceId);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="pb-2">
          <div>
            <CardTitle>Trace search</CardTitle>
            <CardDescription>
              Jaeger v3, last {LOOKBACKS.find((item) => item.value === lookback)?.label ?? "15m"}.
              A search returns whole traces, so the root may belong to another service.
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={refresh} title="Refresh">
            <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
          </Button>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              Service
              <select
                value={service}
                onChange={(event) => setService(event.target.value)}
                className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground"
              >
                <option value="">All services</option>
                {serviceNames.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              Lookback
              <select
                value={lookback}
                onChange={(event) => setLookback(Number(event.target.value))}
                className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground"
              >
                {LOOKBACKS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {item.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              Min duration (ms)
              <input
                type="number"
                min={0}
                step={100}
                value={minDuration}
                onChange={(event) => setMinDuration(Number(event.target.value) || 0)}
                className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground"
              />
            </label>
            <label className="flex items-end gap-2 pb-1 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={errorsOnly}
                onChange={(event) => setErrorsOnly(event.target.checked)}
                className="size-3.5 accent-[var(--primary)]"
              />
              Errors only
            </label>
          </div>

          <div className="mt-3 text-[10px] text-muted-foreground">
            {data ? `${(data.traces ?? []).length} trace(s) · scanned ${data.scanned}` : "searching…"}
            {updatedAt ? ` · updated ${formatClock(new Date(updatedAt).toISOString())}` : ""}
          </div>

          {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}

          {loading && !data ? (
            <div className="mt-3 space-y-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-8 w-full" />
              ))}
            </div>
          ) : data && (data.traces ?? []).length === 0 ? (
            <p className="mt-3 rounded-lg border border-dashed border-border px-3 py-4 text-xs text-muted-foreground">
              No traces match. Widen the lookback, clear the service filter, or inject a fault
              from the Fault Lab (P6).
            </p>
          ) : (
            <Table className="mt-2">
              <TableHeader>
                <TableRow>
                  <TableHead className="pl-0">Started</TableHead>
                  <TableHead>Root</TableHead>
                  <TableHead className="text-right">Duration</TableHead>
                  <TableHead className="text-right">Spans</TableHead>
                  <TableHead className="text-right">Errors</TableHead>
                  <TableHead className="pr-0 text-right">Trace</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.traces ?? []).map((trace) => (
                  <TableRow
                    key={trace.trace_id}
                    className={cn(
                      "cursor-pointer",
                      selectedTraceId === trace.trace_id && "bg-accent/60",
                    )}
                    onClick={() => onSelectTrace(trace.trace_id)}
                  >
                    <TableCell className="tabular pl-0 text-xs text-muted-foreground">
                      {formatClock(trace.started_at)}
                    </TableCell>
                    <TableCell className="max-w-[240px]">
                      <div className="truncate text-xs">{trace.root_name}</div>
                      <div className="truncate text-[10px] text-muted-foreground">
                        {trace.root_service} · {(trace.services ?? []).length} services
                      </div>
                    </TableCell>
                    <TableCell className="tabular text-right text-xs">
                      {formatMetric(trace.duration_ms, "ms")}
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {trace.span_count}
                    </TableCell>
                    <TableCell className="text-right">
                      {trace.error_count > 0 ? (
                        <Badge variant="destructive">{trace.error_count}</Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">0</span>
                      )}
                    </TableCell>
                    <TableCell className="pr-0 text-right">
                      <button
                        type="button"
                        onClick={(event) => {
                          event.stopPropagation();
                          onSelectTrace(trace.trace_id);
                        }}
                        className="font-mono text-[10px] text-primary hover:underline"
                        title={trace.trace_id}
                      >
                        {trace.trace_id.slice(0, 8)}…
                      </button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {selectedTraceId ? (
        detail.loading && !detail.detail ? (
          <Skeleton className="h-64 w-full" />
        ) : detail.error ? (
          <Card>
            <CardContent className="pt-4 text-xs text-destructive">{detail.error}</CardContent>
          </Card>
        ) : detail.detail ? (
          <>
            <TraceWaterfall trace={detail.detail} onOpenLogs={onOpenLogs} />
            <p className="text-[10px] text-muted-foreground">
              Span attributes are filtered to http/rpc/db/messaging/error keys. The full trace
              is available in Jaeger{" "}
              <a
                href={jaegerTraceUrl(detail.detail.trace_id)}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-0.5 text-primary hover:underline"
              >
                here <ExternalLink className="size-2.5" />
              </a>
              .
            </p>
          </>
        ) : null
      ) : (
        <p className="text-xs text-muted-foreground">
          Select a trace above to render its waterfall.
        </p>
      )}
    </div>
  );
}
