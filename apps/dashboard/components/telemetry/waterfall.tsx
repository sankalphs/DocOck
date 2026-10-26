"use client";

import { useMemo, useState } from "react";
import { ExternalLink, FileText } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { SpanNode, TraceDetail } from "@/lib/api/telemetry";
import { jaegerTraceUrl } from "@/lib/api/telemetry";
import { SERIES_COLORS } from "@/lib/chart";
import { formatClock, formatMetric } from "@/lib/format";
import { cn } from "@/lib/utils";

export function TraceWaterfall({
  trace,
  onOpenLogs,
}: {
  trace: TraceDetail;
  onOpenLogs: (traceId: string) => void;
}) {
  const [selected, setSelected] = useState<SpanNode | null>(null);
  const total = Math.max(trace.duration_ms, 0.001);

  const services = useMemo(() => {
    const names = [...new Set((trace.spans ?? []).map((span) => span.service))].sort();
    return new Map(names.map((name, index) => [name, SERIES_COLORS[index % SERIES_COLORS.length]]));
  }, [trace]);

  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="truncate">
            {trace.root_service} · {trace.root_name}
          </CardTitle>
          <CardDescription className="font-mono text-[10px]">{trace.trace_id}</CardDescription>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={trace.error_count > 0 ? "destructive" : "success"}>
            {trace.error_count > 0 ? `${trace.error_count} error spans` : "no errors"}
          </Badge>
          <Badge variant="outline">{trace.span_count} spans</Badge>
          <Badge variant="outline">{formatMetric(trace.duration_ms, "ms")}</Badge>
          <Button variant="secondary" size="sm" onClick={() => onOpenLogs(trace.trace_id)}>
            <FileText className="size-3.5" /> Logs
          </Button>
          <Button variant="ghost" size="sm" asChild>
            <a href={jaegerTraceUrl(trace.trace_id)} target="_blank" rel="noreferrer">
              Jaeger <ExternalLink className="size-3" />
            </a>
          </Button>
        </div>
      </CardHeader>

      <CardContent>
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted-foreground">
          <span>started {formatClock(trace.started_at)}</span>
          <span>{(trace.services ?? []).join(" · ")}</span>
        </div>

        <div className="overflow-x-auto">
          <div className="min-w-[560px] space-y-px">
            {(trace.spans ?? []).map((span) => {
              const left = Math.min(100, (span.start_ms / total) * 100);
              const width = Math.max(0.6, Math.min(100 - left, (span.duration_ms / total) * 100));
              const isError = span.status === "error";
              return (
                <button
                  key={span.span_id}
                  type="button"
                  onClick={() => setSelected(span)}
                  className={cn(
                    "grid w-full grid-cols-[minmax(0,1fr)_140px_64px] items-center gap-2 rounded px-1 py-1 text-left hover:bg-accent/60",
                    selected?.span_id === span.span_id && "bg-accent",
                  )}
                >
                  <div
                    className="flex min-w-0 items-center gap-2"
                    style={{ paddingLeft: `${Math.min(span.depth, 8) * 12}px` }}
                  >
                    <span
                      className="size-1.5 shrink-0 rounded-full"
                      style={{ background: services.get(span.service) ?? "#60a5fa" }}
                    />
                    <span className="shrink-0 text-[10px] text-muted-foreground">
                      {span.service}
                    </span>
                    <span className="truncate text-xs" title={span.name}>
                      {span.name}
                    </span>
                    {isError ? (
                      <Badge variant="destructive" className="shrink-0 text-[9px]">
                        error
                      </Badge>
                    ) : null}
                    <span className="shrink-0 text-[9px] uppercase text-muted-foreground/70">
                      {span.kind}
                    </span>
                  </div>

                  <div className="relative h-3 rounded bg-muted/50">
                    <div
                      className={cn(
                        "absolute inset-y-0 rounded",
                        isError ? "bg-destructive/80" : "bg-primary/70",
                      )}
                      style={{ left: `${left}%`, width: `${width}%` }}
                    />
                  </div>

                  <span className="tabular text-right text-[10px] text-muted-foreground">
                    {formatMetric(span.duration_ms, "ms")}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {selected ? (
          <div className="mt-4 rounded-lg border border-border bg-muted/30 p-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium">{selected.name}</span>
              <Badge variant="outline">{selected.service}</Badge>
              <Badge variant={selected.status === "error" ? "destructive" : "secondary"}>
                {selected.status}
              </Badge>
              <span className="tabular text-[10px] text-muted-foreground">
                +{selected.start_ms.toFixed(1)} ms · {formatMetric(selected.duration_ms, "ms")}
              </span>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="ml-auto text-[10px] text-muted-foreground hover:text-foreground"
              >
                close
              </button>
            </div>
            <dl className="mt-2 grid gap-1 text-[10px] sm:grid-cols-2">
              <div className="flex gap-2">
                <dt className="text-muted-foreground">span_id</dt>
                <dd className="font-mono">{selected.span_id}</dd>
              </div>
              {selected.parent_span_id ? (
                <div className="flex gap-2">
                  <dt className="text-muted-foreground">parent</dt>
                  <dd className="font-mono">{selected.parent_span_id}</dd>
                </div>
              ) : null}
              {Object.entries(selected.attributes ?? {}).map(([key, value]) => (
                <div key={key} className="flex min-w-0 gap-2">
                  <dt className="shrink-0 text-muted-foreground">{key}</dt>
                  <dd className="truncate font-mono" title={value}>
                    {value}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
