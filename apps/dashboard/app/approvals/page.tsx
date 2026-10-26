import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Approvals" };

export default function ApprovalsPage() {
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Remediation never runs on the model's word alone: a human decides, and the server issues the token."
      />
      <PhaseNotice
        phase="P14"
        title="Approval cards and the single-use token flow"
        description="The approval token is never present in the DOM; it is issued server-side when you decide."
        capabilities={[
          "A card per proposed action with target, risk, preconditions, and the evidence it is based on.",
          "Approve, reject, and dry-run; expiry returns 409 and nothing executes.",
          "Server-issued single-use tokens hashed at rest, with a 10-minute expiry.",
          "Full audit trail: who decided, when, and which action row resulted.",
          "Rejection escalates instead of silently closing the incident.",
        ]}
      />
    </>
  );
}
