"use client";

import {
  Activity,
  AlertTriangle,
  Boxes,
  Timer,
  TrendingUp,
  type LucideIcon,
} from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { OverviewData } from "@/lib/hooks/use-overview";
import {
  aggregateErrorRate,
  maxLatest,
  sumLatest,
  worstService,
} from "@/lib/chart";
import { formatMetric, formatNumber, toneFor } from "@/lib/format";
import { cn } from "@/lib/utils";

type Kpi = {
  label: string;
  icon: LucideIcon;
  value: string;
  detail: string;
  tone?: string;
};

function KpiCard({ kpi, loading }: { kpi: Kpi; loading: boolean }) {
  const Icon = kpi.icon;
  return (
    <Card className="gap-2 py-4">
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          <Icon className="size-3.5" aria-hidden />
          {kpi.label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <div
            className={cn(
              "tabular text-2xl font-semibold leading-tight",
              kpi.tone === "bad" && "text-destructive",
              kpi.tone === "warn" && "text-warning",
              kpi.tone === "good" && "text-success",
            )}
          >
            {kpi.value}
          </div>
        )}
        <p className="mt-1 truncate text-xs text-muted-foreground" title={kpi.detail}>
          {kpi.detail}
        </p>
      </CardContent>
    </Card>
  );
}

export function KpiCards({ data, loading }: { data: OverviewData | null; loading: boolean }) {
  const kpis: Kpi[] = [];

  if (!data) {
    const placeholders: Kpi[] = [
      { label: "Error rate", icon: AlertTriangle, value: "—", detail: "waiting for Prometheus" },
      { label: "Worst p95", icon: Timer, value: "—", detail: "server spans" },
      { label: "Throughput", icon: TrendingUp, value: "—", detail: "span calls per second" },
      { label: "Services", icon: Activity, value: "—", detail: "metrics + traces + docker" },
      { label: "Containers", icon: Boxes, value: "—", detail: "docock-demo" },
    ];
    return (
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
        {placeholders.map((kpi) => (
          <KpiCard key={kpi.label} kpi={kpi} loading />
        ))}
      </div>
    );
  }

  const errorRate = maxLatest(data.errorRate.series);
  const failingService = worstService(data.errorRate.series);
  const worstP95 = maxLatest(data.p95.series);
  const worst = worstService(data.p95.series);
  const throughput = sumLatest(data.throughput.series);
  const services = data.inventory.services;
  const running = data.inventory.containers_running;
  const total = data.inventory.containers_total;
  const unhealthy = services.filter(
    (service) =>
      service.container !== null &&
      (service.state !== "running" || service.health === "unhealthy"),
  ).length;

  kpis.push(
    {
      label: "Worst error rate",
      icon: AlertTriangle,
      value: formatMetric(errorRate, "ratio"),
      detail: failingService
        ? `${failingService} · last 2m`
        : "weighted across services, last 2m",
      tone:
        errorRate !== null && errorRate > 0.05
          ? "bad"
          : errorRate !== null && errorRate > 0.02
            ? "warn"
            : "good",
    },
    {
      label: "Worst p95",
      icon: Timer,
      value: formatMetric(worstP95, "ms"),
      detail: worst ?? "inbound (SERVER) spans",
      tone: worstP95 !== null && worstP95 > 1000 ? "warn" : undefined,
    },
    {
      label: "Throughput",
      icon: TrendingUp,
      value: throughput !== null ? `${formatNumber(throughput, 2)}/s` : "—",
      detail: `${services.length} services and containers in the inventory`,
    },
    {
      label: "Services",
      icon: Activity,
      value: String(services.length),
      detail: unhealthy > 0 ? `${unhealthy} not healthy` : "all containers healthy",
      tone: unhealthy > 0 ? "warn" : "good",
    },
    {
      label: "Containers",
      icon: Boxes,
      value: `${running}/${total}`,
      detail:
        running === total ? "docock-demo fully up" : `${total - running} not running`,
      tone: running === total ? "good" : "warn",
    },
  );

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {kpis.map((kpi) => (
        <KpiCard key={kpi.label} kpi={kpi} loading={loading} />
      ))}
    </div>
  );
}

/** Compact health strip used under the KPI row. */
export function HealthSummary({ data }: { data: OverviewData }) {
  const weighted = aggregateErrorRate(data.errorRate.series, data.throughput.series);
  const unhealthy = data.inventory.services.filter(
    (service) =>
      service.container !== null &&
      (service.state !== "running" || service.health === "unhealthy"),
  );
  const worst = [...data.inventory.services]
    .filter((service) => service.p95_ms !== null)
    .sort((a, b) => (b.p95_ms ?? 0) - (a.p95_ms ?? 0))
    .slice(0, 1)[0];
  const tone = toneFor(unhealthy.length ? "degraded" : "ok");

  return (
    <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
      <span className={cn("inline-flex items-center gap-1.5", tone.text)}>
        <span className={cn("size-1.5 rounded-full", tone.dot)} />
        {unhealthy.length ? `${unhealthy.length} service(s) unhealthy` : "all services healthy"}
      </span>
      {worst ? (
        <span>
          slowest p95: <span className="text-foreground">{worst.name}</span>{" "}
          {formatMetric(worst.p95_ms, "ms")}
        </span>
      ) : null}
      <span>
        traffic: {formatNumber(sumLatest(data.throughput.series) ?? 0, 2)} spans/s
      </span>
      <span>weighted error rate: {formatMetric(weighted, "ratio")}</span>
      <span>window: 15m · step 30s · metrics flush 10s</span>
    </div>
  );
}
