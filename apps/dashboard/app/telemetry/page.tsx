import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = { title: "Telemetry" };

export default function TelemetryPage() {
  return (
    <>
      <PageHeader
        title="Telemetry"
        description="Raw metrics, traces, and logs from the OpenTelemetry Astronomy Shop — no synthetic series, no sampled cache."
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_360px]">
        <PhaseNotice
          phase="P4–P5"
          title="Adapters and telemetry panes"
          description="P4 builds the typed adapters and contract tests; P5 renders them here."
          capabilities={[
            "Prometheus query and range API with a metric allowlist and step handling.",
            "Jaeger v3 search and trace lookup under the /jaeger/ui base path, rendered as a waterfall.",
            "OpenSearch log search over otel-logs-* with trace-id linking in both directions.",
            "Docker container state: health, restarts, limits, and network attachment.",
            "Service inventory joined across all four sources.",
          ]}
        />

        <Card className="h-fit">
          <CardHeader>
            <div>
              <CardTitle>Confirmed sources</CardTitle>
              <CardDescription>Verified against the live stack in P1.</CardDescription>
            </div>
          </CardHeader>
          <CardContent>
            <dl className="space-y-3 text-xs">
              <div>
                <dt className="font-mono text-[11px] text-foreground">Prometheus</dt>
                <dd className="mt-0.5 text-muted-foreground">
                  <code className="font-mono text-[10px]">traces_span_metrics_calls_total</code>{" "}
                  and{" "}
                  <code className="font-mono text-[10px]">
                    traces_span_metrics_duration_milliseconds_bucket
                  </code>
                  , labelled <code className="font-mono text-[10px]">service_name</code>. 354
                  metric names available.
                </dd>
              </div>
              <div>
                <dt className="font-mono text-[11px] text-foreground">Jaeger</dt>
                <dd className="mt-0.5 text-muted-foreground">
                  v3 query API at{" "}
                  <code className="font-mono text-[10px]">/jaeger/ui/api/v3/*</code>; 18 services
                  reporting.
                </dd>
              </div>
              <div>
                <dt className="font-mono text-[11px] text-foreground">OpenSearch</dt>
                <dd className="mt-0.5 text-muted-foreground">
                  Index <code className="font-mono text-[10px]">otel-logs-YYYY-MM-DD</code> on
                  9200.
                </dd>
              </div>
              <div>
                <dt className="font-mono text-[11px] text-foreground">Docker</dt>
                <dd className="mt-0.5 text-muted-foreground">
                  25 containers under Compose project{" "}
                  <code className="font-mono text-[10px]">docock-demo</code>.
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      </div>
    </>
  );
}
