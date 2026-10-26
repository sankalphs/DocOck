"use client";

import { useCallback, useState } from "react";
import { Activity, FileText, Server } from "lucide-react";

import { LogsPanel } from "@/components/telemetry/logs-panel";
import { ServicesPanel } from "@/components/telemetry/services-panel";
import { TracesPanel } from "@/components/telemetry/traces-panel";
import { fetchServices } from "@/lib/api/telemetry";
import { usePolling } from "@/lib/hooks/use-polling";
import { cn } from "@/lib/utils";

type Tab = "traces" | "logs" | "services";

const TABS: { id: Tab; label: string; icon: typeof Activity }[] = [
  { id: "traces", label: "Traces", icon: Activity },
  { id: "logs", label: "Logs", icon: FileText },
  { id: "services", label: "Services", icon: Server },
];

/**
 * Holds the cross-pane navigation state. Trace → logs and log → trace both just
 * switch the tab and seed the other pane's filter, so no page reload or route
 * change loses what you were looking at.
 */
export function TelemetryExplorer() {
  const [tab, setTab] = useState<Tab>("traces");
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [logTraceId, setLogTraceId] = useState<string | null>(null);

  const fetcher = useCallback((signal: AbortSignal) => fetchServices("5m", signal), []);
  const inventory = usePolling("inventory", fetcher, 10_000);
  const serviceNames = (inventory.data?.services ?? []).map((service) => service.name);

  const openLogsForTrace = (traceId: string) => {
    setLogTraceId(traceId);
    setTab("logs");
  };

  const openTrace = (traceId: string) => {
    setSelectedTraceId(traceId);
    setTab("traces");
  };

  return (
    <div className="space-y-4">
      <div
        role="tablist"
        aria-label="Telemetry views"
        className="inline-flex items-center gap-1 rounded-lg border border-border bg-card/60 p-1"
      >
        {TABS.map((item) => {
          const Icon = item.icon;
          const active = tab === item.id;
          return (
            <button
              key={item.id}
              role="tab"
              aria-selected={active}
              onClick={() => setTab(item.id)}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition-colors",
                active
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              <Icon className="size-3.5" aria-hidden />
              {item.label}
              {item.id === "services" && inventory.data ? (
                <span className="tabular text-[10px] text-muted-foreground">
                  {inventory.data.services.length}
                </span>
              ) : null}
            </button>
          );
        })}
      </div>

      {tab === "traces" ? (
        <TracesPanel
          serviceNames={serviceNames}
          selectedTraceId={selectedTraceId}
          onSelectTrace={setSelectedTraceId}
          onOpenLogs={openLogsForTrace}
        />
      ) : null}

      {tab === "logs" ? (
        <LogsPanel
          serviceNames={serviceNames}
          traceFilter={logTraceId}
          onClearTraceFilter={() => setLogTraceId(null)}
          onOpenTrace={openTrace}
        />
      ) : null}

      {tab === "services" ? (
        <ServicesPanel
          inventory={inventory.data}
          loading={inventory.loading}
          error={inventory.error}
          refresh={inventory.refresh}
        />
      ) : null}
    </div>
  );
}
