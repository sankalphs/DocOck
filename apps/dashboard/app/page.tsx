import type { Metadata } from "next";
import { Activity, ShieldCheck } from "lucide-react";

import { DependencyTable } from "@/components/overview/dependency-table";
import { LiveEvents } from "@/components/overview/live-events";
import { StackCards } from "@/components/overview/stack-cards";
import { PageHeader } from "@/components/shell/page-header";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "Overview" };

export default function OverviewPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        description="Live state of the lab: the API, the demo, all three telemetry stores, and Postgres. Every value below comes from a probe issued just now."
      />

      <StackCards />

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <DependencyTable />
        </div>
        <div className="min-w-0">
          <LiveEvents />
        </div>
      </div>

      <div className="mt-6 grid gap-3 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Activity className="size-3.5 text-primary" aria-hidden />
              Traffic KPIs arrive with P5
            </CardTitle>
            <CardDescription>
              Error rate, p95 latency, and throughput per service.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-xs leading-relaxed text-muted-foreground">
            P4 adds the typed adapters (Prometheus, Jaeger v3, OpenSearch, Docker). P5 turns
            them into KPI cards and charts here. The dashboard never talks to those services
            directly — the API does, and this page only renders its JSON.
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <ShieldCheck className="size-3.5 text-primary" aria-hidden />
              Trust boundary
            </CardTitle>
            <CardDescription>Browser → API → infrastructure.</CardDescription>
          </CardHeader>
          <CardContent className="text-xs leading-relaxed text-muted-foreground">
            This page holds no Docker socket, no Prometheus connection, and no model key.
            The only origin it talks to is the DocOck API on{" "}
            <code className="font-mono text-[11px]">127.0.0.1:8001</code>.
          </CardContent>
        </Card>
      </div>
    </>
  );
}
