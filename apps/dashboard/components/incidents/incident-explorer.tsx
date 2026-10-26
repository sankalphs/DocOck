"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, RefreshCw, Siren } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useLive } from "@/components/live-provider";
import {
  analyzeIncident,
  closeIncident,
  fetchIncident,
  fetchIncidents,
  type IncidentDetail,
  type IncidentSummary,
} from "@/lib/api/incidents";
import { usePolling } from "@/lib/hooks/use-polling";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";
import { IncidentDetailPanel } from "@/components/incidents/incident-detail";
import { severityBadge, statusBadge } from "@/components/incidents/badges";

type Filter = "all" | "open" | "closed";

function IncidentRow({
  incident,
  selected,
  onSelect,
}: {
  incident: IncidentSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
        selected
          ? "border-primary/50 bg-primary/10"
          : "border-border/70 bg-background/30 hover:bg-accent/40",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="tabular text-[10px] text-muted-foreground">{incident.public_id}</span>
        <Badge variant={severityBadge(incident.severity)} className="uppercase">
          {incident.severity}
        </Badge>
        <Badge variant={statusBadge(incident.status)} className="ml-auto text-[10px] uppercase">
          {incident.status}
        </Badge>
      </div>
      <p className="mt-1 truncate text-xs font-medium">{incident.title}</p>
      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
        <span className="font-mono">{incident.primary_service ?? "—"}</span>
        <span>·</span>
        <span>
          {formatClock(incident.opened_at)} · {incident.duration_s ?? 0}s
        </span>
        {incident.evidence_count > 0 ? (
          <span className="ml-auto">{incident.evidence_count} evidence</span>
        ) : null}
      </div>
    </button>
  );
}

export function IncidentExplorer() {
  const { stream } = useLive();
  const lastEvent = stream.events.find((event) => event.type.startsWith("incident."));
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const listFetcher = useCallback(
    (signal: AbortSignal) => fetchIncidents({ status: filter, limit: 50 }, signal),
    [filter],
  );
  const list = usePolling(`incidents:${filter}:${lastEvent?.id ?? 0}`, listFetcher, 5000);
  const items = list.data?.items ?? [];
  const selected = selectedId ?? items[0]?.public_id ?? null;

  const detailFetcher = useCallback(
    (signal: AbortSignal) =>
      selected ? fetchIncident(selected, signal) : Promise.resolve(null),
    [selected],
  );
  const detail = usePolling<IncidentDetail | null>(
    `incident:${selected}:${lastEvent?.id ?? 0}`,
    detailFetcher,
    5000,
  );

  const handleClose = async () => {
    if (!selected) return;
    setClosing(true);
    setActionError(null);
    try {
      await closeIncident(selected, "dashboard", "closed from the Incidents screen");
      list.refresh();
      detail.refresh();
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : "Close failed");
    } finally {
      setClosing(false);
    }
  };

  const handleAnalyze = async () => {
    if (!selected) return;
    setAnalyzing(true);
    setActionError(null);
    try {
      await analyzeIncident(selected);
      list.refresh();
      detail.refresh();
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : "Analysis failed");
    } finally {
      setAnalyzing(false);
    }
  };

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(18rem,1fr)_2.2fr]">
      <Card className="h-fit">
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Siren className="size-3.5 text-primary" aria-hidden />
              Incidents
              <Badge variant={list.data?.open ? "warning" : "success"}>
                {list.data?.open ?? 0} open
              </Badge>
            </CardTitle>
            <CardDescription>
              {list.data
                ? `${list.data.total} total · detector every 10 s · updated ${
                    list.updatedAt
                      ? formatClock(new Date(list.updatedAt).toISOString())
                      : "—"
                  }`
                : "loading incidents…"}
            </CardDescription>
          </div>
          <Button variant="ghost" size="sm" onClick={list.refresh} title="Refresh">
            <RefreshCw className={cn("size-3.5", list.loading && "animate-spin")} />
          </Button>
        </CardHeader>
        <CardContent>
          <div role="tablist" aria-label="Incident filter" className="mb-3 flex gap-1">
            {(["all", "open", "closed"] as Filter[]).map((value) => (
              <button
                key={value}
                role="tab"
                aria-selected={filter === value}
                onClick={() => setFilter(value)}
                className={cn(
                  "rounded-md px-2 py-1 text-[11px] capitalize transition-colors",
                  filter === value
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {value}
              </button>
            ))}
          </div>

          {list.error ? (
            <p className="mb-2 text-[11px] text-destructive">{list.error}</p>
          ) : null}

          {items.length > 0 ? (
            <div className="max-h-[34rem] space-y-2 overflow-y-auto pr-1">
              {items.map((incident) => (
                <IncidentRow
                  key={incident.public_id}
                  incident={incident}
                  selected={incident.public_id === selected}
                  onSelect={() => setSelectedId(incident.public_id)}
                />
              ))}
            </div>
          ) : list.loading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <Skeleton key={index} className="h-16 rounded-lg" />
              ))}
            </div>
          ) : (
            <div className="rounded-lg border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
              No incidents yet. Inject a fault from the{" "}
              <Link href="/faults" className="underline underline-offset-2">
                Fault Lab
              </Link>{" "}
              and the detector opens one within ~60–90 s.
            </div>
          )}

          <p className="mt-3 border-t border-border/60 pt-2 text-[10px] text-muted-foreground">
            Baseline: 15 m rolling per service. Three consecutive breaches open one incident;
            duplicate services attach as signals.{" "}
            <Link href="/telemetry" className="underline underline-offset-2">
              Inspect telemetry <ArrowUpRight className="inline size-2.5" />
            </Link>
          </p>
        </CardContent>
      </Card>

      <div className="min-w-0">
        {actionError ? (
          <p className="mb-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-destructive">
            {actionError}
          </p>
        ) : null}
        {detail.data ? (
          <IncidentDetailPanel
            incident={detail.data}
            busy={closing}
            analyzing={analyzing}
            onClose={handleClose}
            onAnalyze={handleAnalyze}
            onRefresh={detail.refresh}
          />
        ) : (
          <Card>
            <CardHeader>
              <div>
                <CardTitle>Select an incident</CardTitle>
                <CardDescription>
                  The aggregate includes the trigger, the timeline, related faults, and (from P9)
                  evidence and suspects.
                </CardDescription>
              </div>
            </CardHeader>
          </Card>
        )}
      </div>
    </div>
  );
}
