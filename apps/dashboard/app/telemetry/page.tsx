import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { TelemetryExplorer } from "@/components/telemetry/telemetry-explorer";

export const metadata: Metadata = { title: "Telemetry" };

export default function TelemetryPage() {
  return (
    <>
      <PageHeader
        title="Telemetry"
        description="Traces from Jaeger, logs from OpenSearch, and the service inventory joined across all four sources. Read-only, local, and always live."
      />
      <TelemetryExplorer />
    </>
  );
}
