import type { Metadata } from "next";

import { MemoryBrowser } from "@/components/memory/memory-browser";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Memory" };

export default function MemoryPage() {
  return (
    <>
      <PageHeader
        title="Memory"
        description="Resolved incidents, retrieved by similarity — offered as hypotheses to check, never as evidence."
      />
      <MemoryBrowser />
    </>
  );
}
