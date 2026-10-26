"use client";

import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { ServiceInfo } from "@/lib/api/telemetry";
import { formatMetric, formatNumber, toneFor } from "@/lib/format";
import { cn } from "@/lib/utils";

type SortKey = "error_rate" | "p95_ms" | "throughput_rps" | "cpu_percent" | "memory_mb" | "name";

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "name", label: "Service", numeric: false },
  { key: "error_rate", label: "Error rate", numeric: true },
  { key: "p95_ms", label: "p95", numeric: true },
  { key: "throughput_rps", label: "Throughput", numeric: true },
  { key: "cpu_percent", label: "CPU", numeric: true },
  { key: "memory_mb", label: "Memory", numeric: true },
];

function compare(a: ServiceInfo, b: ServiceInfo, key: SortKey): number {
  if (key === "name") return a.name.localeCompare(b.name);
  const left = a[key] ?? -1;
  const right = b[key] ?? -1;
  return left - right;
}

export function ServiceTable({
  services,
  title = "Service health",
  description = "Joined from Prometheus, Jaeger, and Docker — one row per service.",
  emptyMessage = "No services reported yet.",
}: {
  services: ServiceInfo[];
  title?: string;
  description?: string;
  emptyMessage?: string;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("error_rate");
  const [ascending, setAscending] = useState(false);

  const sorted = useMemo(() => {
    const rows = [...services].sort((a, b) => compare(a, b, sortKey));
    return ascending ? rows : rows.reverse();
  }, [services, sortKey, ascending]);

  const toggle = (key: SortKey) => {
    if (key === sortKey) {
      setAscending((value) => !value);
    } else {
      setSortKey(key);
      setAscending(key === "name");
    }
  };

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <span className="text-[11px] text-muted-foreground">{services.length} services</span>
      </CardHeader>
      <CardContent className="px-0">
        {services.length === 0 ? (
          <p className="px-5 text-xs text-muted-foreground">{emptyMessage}</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                {COLUMNS.map((column) => (
                  <TableHead
                    key={column.key}
                    className={cn(
                      "cursor-pointer select-none whitespace-nowrap",
                      column.key === "name" ? "pl-5" : "text-right",
                    )}
                    onClick={() => toggle(column.key)}
                  >
                    <span className="inline-flex items-center gap-1">
                      {column.label}
                      {sortKey === column.key ? (
                        ascending ? (
                          <ArrowUp className="size-3" />
                        ) : (
                          <ArrowDown className="size-3" />
                        )
                      ) : null}
                    </span>
                  </TableHead>
                ))}
                <TableHead className="pr-5">Sources</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sorted.map((service) => {
                const state = toneFor(
                  service.state !== "running"
                    ? "down"
                    : service.health === "unhealthy"
                      ? "degraded"
                      : "ok",
                );
                const errorPct = service.error_rate;
                return (
                  <TableRow key={service.name}>
                    <TableCell className="pl-5">
                      <div className="flex items-center gap-2">
                        <span className={cn("size-1.5 rounded-full", state.dot)} aria-hidden />
                        <span className="font-medium">{service.name}</span>
                        {service.container ? null : (
                          <span className="text-[10px] text-muted-foreground">no container</span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell
                      className={cn(
                        "tabular text-right text-xs",
                        errorPct !== null && errorPct > 0.05
                          ? "text-destructive"
                          : errorPct !== null && errorPct > 0.02
                            ? "text-warning"
                            : "text-muted-foreground",
                      )}
                    >
                      {service.error_rate === null
                        ? "—"
                        : `${(service.error_rate * 100).toFixed(2)}%`}
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {formatMetric(service.p95_ms, "ms")}
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {service.throughput_rps !== null
                        ? `${formatNumber(service.throughput_rps, 2)}/s`
                        : "—"}
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {service.cpu_percent !== null
                        ? `${service.cpu_percent.toFixed(0)}%`
                        : "—"}
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {service.memory_mb !== null
                        ? `${service.memory_mb.toFixed(0)} / ${
                            service.memory_limit_mb ?? "?"
                          } MB`
                        : "—"}
                    </TableCell>
                    <TableCell className="pr-5">
                      <div className="flex flex-wrap gap-1">
                        {(service.sources ?? []).map((source) => (
                          <Badge key={source} variant="outline" className="text-[10px]">
                            {source}
                          </Badge>
                        ))}
                        {service.state !== "running" ? (
                          <Badge variant="destructive" className="text-[10px]">
                            {service.state}
                          </Badge>
                        ) : service.health === "unhealthy" ? (
                          <Badge variant="warning" className="text-[10px]">
                            unhealthy
                          </Badge>
                        ) : null}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
