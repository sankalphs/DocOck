"use client";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
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
import { formatLatency, formatRelative, toneFor } from "@/lib/format";
import { cn } from "@/lib/utils";

export function DependencyTable() {
  const { health } = useLive();
  const report = health.data;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Dependency probes</CardTitle>
          <CardDescription>
            Every row is a live request issued by the API, not a cached config flag.
          </CardDescription>
        </div>
        <span className="text-[11px] text-muted-foreground">
          {health.updatedAt ? `checked ${formatRelative(health.updatedAt)}` : "checking…"}
        </span>
      </CardHeader>
      <CardContent className="px-0">
        {report ? (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Dependency</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Latency</TableHead>
                <TableHead className="pr-5">Detail</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {report.dependencies.map((dep) => {
                const tone = toneFor(dep.status);
                return (
                  <TableRow key={dep.name}>
                    <TableCell className="pl-5">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{dep.name}</span>
                        {dep.critical ? <Badge variant="outline">critical</Badge> : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <span className={cn("inline-flex items-center gap-1.5 text-xs", tone.text)}>
                        <span className={cn("size-1.5 rounded-full", tone.dot)} aria-hidden />
                        {tone.label}
                      </span>
                    </TableCell>
                    <TableCell className="tabular text-right text-xs text-muted-foreground">
                      {formatLatency(dep.latency_ms)}
                    </TableCell>
                    <TableCell className="pr-5 text-xs text-muted-foreground">
                      {dep.detail}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        ) : (
          <div className="space-y-2 px-5">
            {Array.from({ length: 7 }).map((_, index) => (
              <Skeleton key={index} className="h-8 w-full" />
            ))}
          </div>
        )}
        {health.error ? (
          <p className="mt-3 px-5 text-xs text-destructive">
            Last refresh failed: {health.error}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
