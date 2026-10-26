"use client";

import { useCallback, useState } from "react";
import { Filter, RefreshCw, X } from "lucide-react";

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
import { fetchLogs } from "@/lib/api/telemetry";
import { usePolling } from "@/lib/hooks/use-polling";
import { formatClock, severityVariant } from "@/lib/format";
import { cn } from "@/lib/utils";

const LEVELS = ["", "trace", "debug", "info", "warn", "error", "fatal"];
const LOOKBACKS = [
  { value: 300, label: "5m" },
  { value: 900, label: "15m" },
  { value: 3600, label: "1h" },
  { value: 21600, label: "6h" },
];

export function LogsPanel({
  serviceNames,
  traceFilter,
  onClearTraceFilter,
  onOpenTrace,
}: {
  serviceNames: string[];
  traceFilter: string | null;
  onClearTraceFilter: () => void;
  onOpenTrace: (traceId: string) => void;
}) {
  const [service, setService] = useState("");
  // null = "auto": errors by default, any severity when drilled in from a trace.
  // A trace view must not hide the warn-level logs that explain the failure.
  const [level, setLevel] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [lookback, setLookback] = useState(900);
  const effectiveLevel = level ?? (traceFilter ? "" : "error");

  const fetcher = useCallback(
    (signal: AbortSignal) =>
      fetchLogs(
        {
          service: service || undefined,
          level: effectiveLevel || undefined,
          q: text || undefined,
          traceId: traceFilter ?? undefined,
          lookbackSeconds: lookback,
          limit: 50,
        },
        signal,
      ),
    [service, effectiveLevel, text, traceFilter, lookback],
  );
  const { data, loading, error, refresh, updatedAt } = usePolling(
    `logs:${service}:${effectiveLevel}:${text}:${traceFilter}:${lookback}`,
    fetcher,
    15_000,
  );

  return (
    <Card>
      <CardHeader className="pb-2">
        <div>
          <CardTitle>Log search</CardTitle>
          <CardDescription>
            OpenSearch <code className="font-mono text-[10px]">otel-logs*</code>. Click a trace
            id to open the trace.
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={refresh} title="Refresh">
          <RefreshCw className={cn("size-3.5", loading && "animate-spin")} />
        </Button>
      </CardHeader>
      <CardContent>
        {traceFilter ? (
          <div className="mb-3 flex items-center gap-2 rounded-md border border-primary/30 bg-primary/10 px-2 py-1 text-[11px]">
            <Filter className="size-3 text-primary" aria-hidden />
            <span className="text-muted-foreground">trace</span>
            <span className="font-mono">{traceFilter}</span>
            <button
              type="button"
              onClick={onClearTraceFilter}
              className="ml-auto text-muted-foreground hover:text-foreground"
              aria-label="Clear trace filter"
            >
              <X className="size-3" />
            </button>
          </div>
        ) : null}

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
            Severity
            <select
              value={effectiveLevel}
              onChange={(event) => setLevel(event.target.value)}
              className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground"
            >
              {LEVELS.map((value) => (
                <option key={value} value={value}>
                  {value || "any"}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            Contains
            <input
              type="search"
              value={text}
              maxLength={200}
              placeholder="e.g. charge failed"
              onChange={(event) => setText(event.target.value)}
              className="h-8 rounded-md border border-input bg-card px-2 text-xs text-foreground placeholder:text-muted-foreground/60"
            />
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
        </div>

        <div className="mt-3 text-[10px] text-muted-foreground">
          {data
            ? `${data.total.toLocaleString()} hits · showing ${data.returned} · ${data.took_ms} ms`
            : "searching…"}
          {updatedAt ? ` · updated ${formatClock(new Date(updatedAt).toISOString())}` : ""}
        </div>

        {error ? <p className="mt-2 text-xs text-destructive">{error}</p> : null}

        {loading && !data ? (
          <div className="mt-3 space-y-2">
            {Array.from({ length: 5 }).map((_, index) => (
              <Skeleton key={index} className="h-8 w-full" />
            ))}
          </div>
        ) : data && (data.records ?? []).length === 0 ? (
          <p className="mt-3 rounded-lg border border-dashed border-border px-3 py-4 text-xs text-muted-foreground">
            No logs match. The demo is quiet at <code className="font-mono">info</code> — try
            severity <code className="font-mono">any</code> or a wider lookback.
          </p>
        ) : (
          <Table className="mt-2">
            <TableHeader>
              <TableRow>
                <TableHead className="pl-0">Time</TableHead>
                <TableHead>Severity</TableHead>
                <TableHead>Service</TableHead>
                <TableHead>Body</TableHead>
                <TableHead className="pr-0 text-right">Trace</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(data?.records ?? []).map((record, index) => (
                <TableRow key={`${record.timestamp}-${index}`}>
                  <TableCell className="tabular whitespace-nowrap pl-0 text-[10px] text-muted-foreground">
                    {formatClock(record.timestamp)}
                  </TableCell>
                  <TableCell>
                    <Badge variant={severityVariant(record.severity)}>{record.severity}</Badge>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-[11px]">{record.service}</TableCell>
                  <TableCell className="max-w-[520px]">
                    <div className="truncate font-mono text-[11px]" title={record.body}>
                      {record.body}
                    </div>
                  </TableCell>
                  <TableCell className="pr-0 text-right">
                    {record.trace_id ? (
                      <button
                        type="button"
                        onClick={() => onOpenTrace(record.trace_id as string)}
                        className="font-mono text-[10px] text-primary hover:underline"
                        title={record.trace_id}
                      >
                        {record.trace_id.slice(0, 8)}…
                      </button>
                    ) : (
                      <span className="text-[10px] text-muted-foreground/60">—</span>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
