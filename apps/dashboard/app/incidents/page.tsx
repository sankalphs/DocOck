import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Incidents" };

export default function IncidentsPage() {
  return (
    <>
      <PageHeader
        title="Incidents"
        description="One incident per service and signature, with an append-only timeline and immutable evidence."
      />
      <PhaseNotice
        phase="P8"
        title="Detector and incident state machine"
        description="Detection is deterministic: thresholds and baselines, no model in the loop."
        capabilities={[
          "Error-rate and p95 detectors with three consecutive breach samples and a 5-minute cooldown.",
          "The state machine from PLAN §7: OPEN → INVESTIGATING → DIAGNOSED → AWAITING_APPROVAL → REMEDIATING → VERIFYING → RESOLVED | ESCALATED.",
          "Rolling 15-minute baselines persisted per service and metric.",
          "Every transition writes an incident event and publishes to SSE, replayable from Last-Event-ID.",
          "Duplicate alerts attach as events instead of opening a second incident.",
        ]}
      />
    </>
  );
}
