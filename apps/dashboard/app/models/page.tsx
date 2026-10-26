import type { Metadata } from "next";

import { ModelSettingsPanel } from "@/components/models/model-settings";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Models" };

export default function ModelsPage() {
  return (
    <>
      <PageHeader
        title="Models"
        description="The reasoning model and the embedding model, both behind provider interfaces so a swap is configuration. No key is ever sent to the browser."
      />
      <ModelSettingsPanel />
    </>
  );
}
