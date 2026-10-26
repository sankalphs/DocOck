import type { Metadata } from "next";

import { ReportViewer } from "@/components/reports/report-viewer";
import { PageHeader } from "@/components/shell/page-header";

export const metadata: Metadata = { title: "Reports" };

export default async function ReportsPage({
  searchParams,
}: {
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const params = await searchParams;
  const incident = typeof params.incident === "string" ? params.incident : null;
  return (
    <>
      <PageHeader
        title="Reports"
        description="A self-contained record of what happened, what was concluded, what was done, and whether it worked."
      />
      <ReportViewer initialIncident={incident} />
    </>
  );
}
