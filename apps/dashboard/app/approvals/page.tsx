import type { Metadata } from "next";

import { ApprovalBoard } from "@/components/approvals/approval-board";
import { AutopilotPanel } from "@/components/approvals/autopilot-panel";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Approvals" };

export default function ApprovalsPage() {
  return (
    <>
      <PageHeader
        title="Approvals"
        description="Remediation never runs on the model's word alone: the autopilot's policy or a human decides, and the server issues the token."
      />
      <div className="space-y-4">
        <AutopilotPanel />
        <ApprovalBoard />
      </div>
    </>
  );
}
