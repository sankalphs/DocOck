import type { Metadata } from "next";

import { OperationsPanel } from "@/components/settings/operations-panel";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Operating statistics and guardrails: what the agent costs and how long it takes, how incidents end, and the limits that keep the lab safe. Model choice lives under Models."
      />
      <OperationsPanel />
    </>
  );
}
