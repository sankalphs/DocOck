import type { Metadata } from "next";

import { IncidentExplorer } from "@/components/incidents/incident-explorer";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Incidents" };

export default function IncidentsPage() {
  return (
    <>
      <PageHeader
        title="Incidents"
        description="Opened by the deterministic detector from per-service baselines — no LLM, no absolute latency threshold. One incident per fault; extra services attach as signals."
      />
      <IncidentExplorer />
    </>
  );
}
