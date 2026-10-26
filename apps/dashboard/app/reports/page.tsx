import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Reports" };

export default function ReportsPage() {
  return (
    <>
      <PageHeader
        title="Reports"
        description="A self-contained record of what happened, what was concluded, what was done, and whether it worked."
      />
      <PhaseNotice
        phase="P15"
        title="Verified outcomes and exports"
        description="A report is only generated for an incident with a verification verdict."
        capabilities={[
          "Timeline, evidence, diagnosis, action, and before/after metrics in one document.",
          "JSON for machines and Markdown for humans, from the same source.",
          "Verification verdicts with the criteria that were checked and the observation window.",
          "Failed remediation escalates and rolls back where it is safe to do so.",
          "Download and copy actions, with no external calls.",
        ]}
      />
    </>
  );
}
