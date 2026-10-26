"use client";

import { RefreshCw } from "lucide-react";

import { ServiceTable } from "@/components/overview/service-table";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { ServiceInventory } from "@/lib/api/telemetry";
import { formatMetric } from "@/lib/format";

export function ServicesPanel({
  inventory,
  loading,
  error,
  refresh,
}: {
  inventory: ServiceInventory | null;
  loading: boolean;
  error: string | null;
  refresh: () => void;
}) {
  const services = inventory?.services ?? [];
  const unhealthy = services.filter(
    (service) =>
      service.container !== null &&
      (service.state !== "running" || service.health === "unhealthy"),
  );
  const busiest = [...services]
    .filter((service) => service.throughput_rps !== null)
    .sort((a, b) => (b.throughput_rps ?? 0) - (a.throughput_rps ?? 0))
    .slice(0, 3);

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="gap-1 py-4">
          <CardHeader className="pb-0">
            <CardTitle className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Services
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="tabular text-2xl font-semibold">{services.length}</div>
            <p className="text-xs text-muted-foreground">
              {inventory
                ? `${inventory.containers_running}/${inventory.containers_total} containers running`
                : "loading"}
            </p>
          </CardContent>
        </Card>
        <Card className="gap-1 py-4">
          <CardHeader className="pb-0">
            <CardTitle className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Unhealthy
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="tabular text-2xl font-semibold">{unhealthy.length}</div>
            <p className="truncate text-xs text-muted-foreground">
              {unhealthy.length ? unhealthy.map((service) => service.name).join(", ") : "none"}
            </p>
          </CardContent>
        </Card>
        <Card className="gap-1 py-4">
          <CardHeader className="pb-0">
            <CardTitle className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Busiest
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="tabular text-2xl font-semibold">
              {busiest[0] ? `${(busiest[0].throughput_rps ?? 0).toFixed(1)}/s` : "—"}
            </div>
            <p className="truncate text-xs text-muted-foreground">
              {busiest.map((service) => service.name).join(" · ") || "no traffic"}
            </p>
          </CardContent>
        </Card>
        <Card className="gap-1 py-4">
          <CardHeader className="pb-0">
            <CardTitle className="text-[11px] uppercase tracking-wider text-muted-foreground">
              Slowest p95
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="tabular text-2xl font-semibold">
              {formatMetric(
                Math.max(...services.map((service) => service.p95_ms ?? 0)),
                "ms",
              )}
            </div>
            <p className="truncate text-xs text-muted-foreground">
              inbound SERVER spans · last 5m
            </p>
          </CardContent>
        </Card>
      </div>

      <div className="flex items-center justify-end">
        <Button variant="ghost" size="sm" onClick={refresh}>
          <RefreshCw className={loading ? "size-3.5 animate-spin" : "size-3.5"} /> refresh
        </Button>
      </div>

      {error ? <p className="text-xs text-destructive">{error}</p> : null}

      {loading && !inventory ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        <ServiceTable
          services={services}
          title="Inventory"
          description="Metric rate, trace presence, and container state for every service."
          emptyMessage="No services reported yet."
        />
      )}
    </div>
  );
}
