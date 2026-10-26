import type { Metadata } from "next";

import { AgentWorkspace } from "@/components/agent/agent-workspace";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Agent" };

export default async function AgentPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const incident = typeof params.incident === "string" ? params.incident : null;
  const run = typeof params.run === "string" && /^\d+$/.test(params.run) ? Number(params.run) : null;
  return (
    <>
      <PageHeader
        title="Agent"
        description="The investigation loop: read-only tools, a fixed budget, and a diagnosis that cites evidence codes created by code."
      />
      <AgentWorkspace initialIncident={incident} initialRun={run} />
    </>
  );
}
