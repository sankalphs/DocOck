"use client";

import type { ComponentType } from "react";
import { Activity, Boxes, Database, Gauge } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { formatLatency, formatNumber, toneFor } from "@/lib/format";
import { cn } from "@/lib/utils";

type StatCardProps = {
  label: string;
  icon: ComponentType<{ className?: string }>;
  value: string;
  detail: string;
  status?: string;
  loading?: boolean;
};

function StatCard({ label, icon: Icon, value, detail, status, loading }: StatCardProps) {
  const tone = toneFor(status);
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-xs font-medium text-muted-foreground">
          <Icon className="size-3.5" aria-hidden />
          {label}
        </CardTitle>
        {status ? (
          <Badge variant={status === "ok" ? "success" : status === "unconfigured" ? "outline" : "destructive"}>
            <span className={cn("size-1.5 rounded-full", tone.dot)} aria-hidden />
            {tone.label}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <div className="tabular text-2xl font-semibold leading-tight">{value}</div>
        )}
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}

export function StackCards() {
  const { health } = useLive();
  const report = health.data;
  const loading = !report;

  const dep = (name: string) => report?.dependencies.find((item) => item.name === name);
  const docker = dep("docker");
  const demo = dep("demo");
  const prometheus = dep("prometheus");
  const jaeger = dep("jaeger");
  const opensearch = dep("opensearch");
  const postgres = dep("postgres");

  const dockerMeta = (docker?.meta ?? {}) as { running?: number; total?: number };
  const promMeta = (prometheus?.meta ?? {}) as { span_metric_series?: number };
  const jaegerMeta = (jaeger?.meta ?? {}) as { services?: number };
  const osMeta = (opensearch?.meta ?? {}) as { docs?: number };
  const pgMeta = (postgres?.meta ?? {}) as { version?: string; pgvector?: string; tables?: number };
  const demoMeta = (demo?.meta ?? {}) as { status?: number; latency_ms?: number };

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      <StatCard
        label="API"
        icon={Gauge}
        loading={loading}
        value={report ? `v${report.version}` : "—"}
        detail={
          report
            ? `${report.environment} · up ${Math.round(report.uptime_s / 60)}m · 127.0.0.1:8001`
            : "waiting for /api/health"
        }
        status={report?.status}
      />
      <StatCard
        label="Demo"
        icon={Boxes}
        loading={loading}
        value={
          dockerMeta.running !== undefined ? `${dockerMeta.running}/${dockerMeta.total}` : "—"
        }
        detail={
          demo
            ? `containers running · storefront HTTP ${demoMeta.status ?? 200} in ${formatLatency(
                demoMeta.latency_ms,
              )}`
            : "astronomy shop container state"
        }
        status={demo?.status ?? docker?.status}
      />
      <StatCard
        label="Telemetry"
        icon={Activity}
        loading={loading}
        value={jaegerMeta.services !== undefined ? `${jaegerMeta.services} services` : "—"}
        detail={`${formatNumber(promMeta.span_metric_series)} span-metric series · ${formatNumber(
          osMeta.docs,
        )} log docs`}
        status={
          jaeger?.status === "ok" && prometheus?.status === "ok" && opensearch?.status === "ok"
            ? "ok"
            : "degraded"
        }
      />
      <StatCard
        label="Database"
        icon={Database}
        loading={loading}
        value={pgMeta.tables !== undefined ? `${pgMeta.tables} tables` : "—"}
        detail={`PostgreSQL ${pgMeta.version ?? "?"} · pgvector ${pgMeta.pgvector ?? "?"} · port 5433`}
        status={postgres?.status}
      />
    </div>
  );
}
