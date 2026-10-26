import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Fault Lab" };

export default function FaultsPage() {
  return (
    <>
      <PageHeader
        title="Fault Lab"
        description="Inject a reversible fault into the running demo, watch the symptom, then revert it and verify recovery."
      />
      <PhaseNotice
        phase="P6–P7"
        title="Fault controller and dynamic forms"
        description="The catalog is served by the API, so the form is generated from schema rather than hand-written per fault."
        capabilities={[
          "13 application faults driven through flagd variants (10%, 25%, … 100x) with OFREP read-back.",
          "6 infrastructure faults: stop, pause, network partition, CPU and memory limits, load spike.",
          "Every fault carries an expiry (default 15 min) and a watchdog that reverts it even if the API restarts.",
          "Active-fault cards with a countdown, revert, panic reset, and an audit history.",
          "Non-lab targets are rejected with 403 before anything is touched.",
        ]}
      />
    </>
  );
}
