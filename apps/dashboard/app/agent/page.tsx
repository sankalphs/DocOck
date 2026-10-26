import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Agent" };

export default function AgentPage() {
  return (
    <>
      <PageHeader
        title="Agent"
        description="The investigation loop: read-only tools, a fixed budget, and a diagnosis that cites evidence codes created by code."
      />
      <PhaseNotice
        phase="P11–P13"
        title="Investigation timeline and tool inspector"
        description="The model may select evidence; it cannot invent an observable or execute an action."
        capabilities={[
          "A watchable timeline of every tool call with its arguments, latency, and trimmed output.",
          "11 read-only tools such as get_traces, get_log_templates, get_topology, and get_container_health.",
          "Budget enforcement: at most 20 tool calls and 90 seconds of wall clock.",
          "A validated diagnosis card with root cause, confidence, affected services, and evidence citations that link to the evidence rows.",
          "Run controls: rerun and cancel, with failed validation surfaced — never silently dropped.",
        ]}
      />
    </>
  );
}
