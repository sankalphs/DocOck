"use client";

import { useState, type ReactNode } from "react";

import { LiveProvider } from "@/components/live-provider";
import { Sidebar } from "@/components/shell/sidebar";
import { StatusHeader } from "@/components/shell/status-header";
import { API_BASE_URL } from "@/lib/api/base-url";

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);

  return (
    <LiveProvider>
      <div className="flex min-h-screen">
        <Sidebar open={open} onClose={() => setOpen(false)} />
        <div className="flex min-w-0 flex-1 flex-col">
          <StatusHeader onMenu={() => setOpen(true)} />
          <main className="mx-auto w-full max-w-[1440px] flex-1 px-4 py-6 sm:px-6 lg:px-8">
            {children}
          </main>
          <footer className="border-t border-border/60 px-4 py-3 text-[11px] text-muted-foreground sm:px-6 lg:px-8">
            <span className="tabular">{API_BASE_URL}</span>
            <span className="mx-1.5">·</span>
            local only, no credentials in the browser
          </footer>
        </div>
      </div>
    </LiveProvider>
  );
}
