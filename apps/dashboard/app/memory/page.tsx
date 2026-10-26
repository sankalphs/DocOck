import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Memory" };

export default function MemoryPage() {
  return (
    <>
      <PageHeader
        title="Memory"
        description="Resolved incidents, retrieved by similarity — offered as hypotheses to check, never as evidence."
      />
      <PhaseNotice
        phase="P12"
        title="Retrieval over resolved incidents"
        description="Memory is a boost to a hypothesis, not a substitute for an observable."
        capabilities={[
          "Normalized summaries only: no raw logs and no unbounded payloads.",
          "1024-dimension embeddings (Qwen3-Embedding-0.6B, ONNX int8, CPU) with an HNSW cosine index.",
          "Top-5 retrieval with a 0.55 similarity floor, optional fault-class filter, recency rerank.",
          "A debug search endpoint to inspect exactly what would be injected.",
          "The diagnosis schema has no field for memory citations, so memory cannot become evidence.",
        ]}
      />
    </>
  );
}
