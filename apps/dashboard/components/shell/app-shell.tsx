"use client";

import { useState, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import { LiveProvider } from "@/components/live-provider";
import { Sidebar } from "@/components/shell/sidebar";
import { StatusHeader } from "@/components/shell/status-header";
import { API_BASE_URL } from "@/lib/api/base-url";
import { LANDING_PATH } from "@/lib/nav";

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // The landing page has its own frame and its own light health poll.
  if (pathname === LANDING_PATH) return <>{children}</>;

  return (
    <LiveProvider>
      <div className="flex min-h-screen">
        <Sidebar open={open} onClose={() => setOpen(false)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <StatusHeader onMenu={() => setOpen(true)} />
          <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
            {children}
          </main>
          <footer className="px-4 py-4 text-xs text-muted-foreground sm:px-6 lg:px-8">
            <span className="tabular">{API_BASE_URL}</span>
            <span className="mx-1.5">·</span>
            local only, no credentials in the browser
          </footer>
        </div>
      </div>
    </LiveProvider>
  );
}
