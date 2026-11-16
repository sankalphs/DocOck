"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Download, RefreshCw, Search, Siren, X } from "lucide-react";

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
  incidentsCsvUrl,
  type IncidentDetail,
  type IncidentSummary,
} from "@/lib/api/incidents";
import { usePolling } from "@/lib/hooks/use-polling";
import { formatClock, formatDuration } from "@/lib/format";
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
          {formatClock(incident.opened_at)} · {formatDuration(incident.duration_s)}
        </span>
        {incident.evidence_count > 0 ? (
          <span className="ml-auto">{incident.evidence_count} evidence</span>
        ) : null}
      </div>
    </button>
  );
}

/** Wait for typing to pause before hitting the API. */
function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

export function IncidentExplorer({ initialIncident = null }: { initialIncident?: string | null }) {
  const { stream } = useLive();
  const lastEvent = stream.events.find((event) => event.type.startsWith("incident."));
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const query = useDebounced(search.trim(), 300);
  const [selectedId, setSelectedIdState] = useState<string | null>(
    initialIncident ? initialIncident.toUpperCase() : null,
  );
  const setSelectedId = (ref: string) => {
    setSelectedIdState(ref);
    // Shareable link to exactly this incident, without a navigation.
    window.history.replaceState(null, "", `/incidents?incident=${encodeURIComponent(ref)}`);
  };
  const [closing, setClosing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const listFetcher = useCallback(
    (signal: AbortSignal) => fetchIncidents({ status: filter, q: query, limit: 50 }, signal),
    [filter, query],
  );
  const list = usePolling(
    `incidents:${filter}:${query}:${lastEvent?.id ?? 0}`,
    listFetcher,
    5000,
  );
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
          <div className="flex items-center gap-1">
            <a
              href={incidentsCsvUrl({ status: filter, q: query })}
              download
              title="Download these incidents as CSV"
              className="inline-flex h-8 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <Download className="size-3.5" aria-hidden />
              CSV
            </a>
            <Button variant="ghost" size="sm" onClick={list.refresh} title="Refresh">
              <RefreshCw className={cn("size-3.5", list.loading && "animate-spin")} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <label className="relative mb-2 block">
            <span className="sr-only">Search incidents</span>
            <Search
              className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <input
              type="search"
              value={search}
              maxLength={100}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") setSearch("");
              }}
              placeholder="Search id, title, service, signature"
              className="h-8 w-full rounded-md border border-input bg-card pl-7 pr-7 text-xs outline-none placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring"
            />
            {search ? (
              <button
                type="button"
                onClick={() => setSearch("")}
                aria-label="Clear search"
                className="absolute right-1.5 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded text-muted-foreground hover:text-foreground"
              >
                <X className="size-3" />
              </button>
            ) : null}
          </label>
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
          ) : query ? (
            <div className="rounded-lg border border-dashed border-border/70 px-3 py-6 text-center text-xs text-muted-foreground">
              No incidents match <span className="font-mono">{query}</span>.
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
        ) : detail.error && selected ? (
          <Card>
            <CardHeader>
              <div>
                <CardTitle className="font-mono">{selected}</CardTitle>
                <CardDescription className="text-destructive">{detail.error}</CardDescription>
              </div>
            </CardHeader>
          </Card>
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
