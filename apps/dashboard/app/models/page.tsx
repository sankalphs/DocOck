import type { Metadata } from "next";

import { PageHeader } from "@/components/shell/page-header";
import { PhaseNotice } from "@/components/shell/phase-notice";

export const metadata: Metadata = { title: "Models" };

export default function ModelsPage() {
  return (
    <>
      <PageHeader
        title="Models"
        description="The reasoning model and the embedding model, both behind provider interfaces so a swap is configuration."
      />
      <PhaseNotice
        phase="P10"
        title="Model gateway"
        description="No key is ever sent to the browser: reads are masked and writes stay server-side."
        capabilities={[
          "OpenAI-compatible adapter with tool calling, JSON mode, retries, timeouts, and token accounting.",
          "ZenMux as the default provider; a local OpenAI-compatible server is a config change.",
          "Embedding providers: ONNX local, remote, and a deterministic stub so tests run offline.",
          "A connection test that reports the resolved model ID and measured latency.",
          "API keys masked as sk-…abcd in every response.",
        ]}
      />
    </>
  );
}
