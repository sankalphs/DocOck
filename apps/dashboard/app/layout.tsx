import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AppShell } from "@/components/shell/app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "DocOck — Incident Response Lab", template: "%s · DocOck" },
  description:
    "Local incident-response lab: inject faults into the OpenTelemetry demo, watch real telemetry, and drive an evidence-backed investigation.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
