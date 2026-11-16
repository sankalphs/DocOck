import type { Metadata } from "next";

import { IncidentExplorer } from "@/components/incidents/incident-explorer";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Incidents" };

export default async function IncidentsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const incident = typeof params.incident === "string" ? params.incident : null;
  return (
    <>
      <PageHeader
        title="Incidents"
        description="Opened by the deterministic detector from per-service baselines — no LLM, no absolute latency threshold. One incident per fault; extra services attach as signals."
      />
      <IncidentExplorer initialIncident={incident} />
    </>
  );
}
