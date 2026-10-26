import type { Metadata } from "next";

import { FaultLab } from "@/components/faults/fault-lab";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Fault Lab" };

export default function FaultsPage() {
  return (
    <>
      <PageHeader
        title="Fault Lab"
        description="Inject a reversible fault into the running demo, watch it propagate, then revert it and verify recovery. Every write is allowlisted and audited."
      />
      <FaultLab />
    </>
  );
}
