import type { Metadata } from "next";

import { OverviewDashboard } from "@/components/overview/overview-dashboard";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Overview" };

export default function OverviewPage() {
  return (
    <>
      <PageHeader
        title="Overview"
        description="Live KPIs from the OpenTelemetry demo: error rate, latency, and throughput per service, with the container health behind them."
      />
      <OverviewDashboard />
    </>
  );
}
