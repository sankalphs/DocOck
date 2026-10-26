"use client";

import { useMemo } from "react";
import Link from "next/link";
import { ArrowUpRight, Database, RefreshCw } from "lucide-react";

import { KpiCards, HealthSummary } from "@/components/overview/kpi-cards";
import { MetricChart } from "@/components/overview/metric-chart";
import { ServiceTable } from "@/components/overview/service-table";
import { LiveEvents } from "@/components/overview/live-events";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { toChartData } from "@/lib/chart";
import { formatRelative } from "@/lib/format";
import { useOverview } from "@/lib/hooks/use-overview";

export function OverviewDashboard() {
  const { data, loading, error, updatedAt, refresh } = useOverview();

  const errorChart = useMemo(() => toChartData(data?.errorRate.series ?? [], 6), [data]);
  const p95Chart = useMemo(() => toChartData(data?.p95.series ?? [], 6), [data]);
  const throughputChart = useMemo(() => toChartData(data?.throughput.series ?? [], 6), [data]);

  return (
    <div className="space-y-6">
      <KpiCards data={data} loading={loading} />

      {data ? <HealthSummary data={data} /> : null}

      {error ? (
        <div className="flex items-center justify-between rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
          <span>Telemetry refresh failed: {error}</span>
          <Button variant="ghost" size="sm" onClick={refresh}>
            <RefreshCw className="size-3.5" /> retry
          </Button>
        </div>
      ) : null}

      <MetricChart
        title="Error rate"
        description="Share of spans with STATUS_CODE_ERROR, top 6 services by current rate."
        unit="ratio"
        data={errorChart}
        loading={loading}
        error={error}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <MetricChart
          title="p95 latency"
          description="Inbound (SERVER) span duration at the 95th percentile."
          unit="ms"
          data={p95Chart}
          loading={loading}
          error={error}
          height={220}
        />
        <MetricChart
          title="Throughput"
          description="Span calls per second, before any sampling."
          unit="rps"
          data={throughputChart}
          loading={loading}
          error={error}
          height={220}
        />
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <ServiceTable
            services={data?.inventory.services ?? []}
            description="One row per service, joined from Prometheus, Jaeger, and Docker."
            emptyMessage={loading ? "Loading the service inventory…" : "No services reported."}
          />
        </div>

        <div className="min-w-0 space-y-6">
          <LiveEvents />
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Database className="size-3.5 text-primary" aria-hidden />
                  How to read this
                </CardTitle>
                <CardDescription>
                  Last refresh {formatRelative(updatedAt)} · window 15m
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-2 text-xs leading-relaxed text-muted-foreground">
              <p>
                Span metrics are pushed by the collector every 10 s, so a change takes
                up to 10 s to appear. The checkout path is slow even when healthy, so
                p95 is compared per service — the table is the source of truth, not an
                absolute threshold.
              </p>
              <Link
                href="/telemetry"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                Open traces, logs, and services <ArrowUpRight className="size-3" />
              </Link>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
