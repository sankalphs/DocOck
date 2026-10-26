import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return (
    <>
      <PageHeader
        title="Settings"
        description="Runtime thresholds and guardrails, stored in Postgres so a change survives a restart."
      />
      <PhaseNotice
        phase="P10"
        title="Runtime configuration"
        description="Thresholds are data, not constants: they live in the settings table and are editable from here."
        capabilities={[
          "Detector thresholds: error-rate floor and multiplier, latency multiplier, breach count, cooldown.",
          "Agent budget: tool-call cap, wall-clock limit, confidence gate, daily cost guard.",
          "Remediation mode: AGENT_AUTO_REMEDIATE stays off until you flip it, and even auto-eligible actions write an audit row.",
          "Embedding dimension is checked at startup so a model change cannot silently mix vector spaces.",
          "Secrets are read-only and masked; the database never stores a key.",
        ]}
      />
    </>
  );
}
