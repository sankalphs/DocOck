"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Bot, Play, RefreshCw, Square } from "lucide-react";

import { DiagnosisCard } from "@/components/agent/diagnosis-card";
import { EvidenceViewer } from "@/components/agent/evidence-viewer";
import { RunTimeline } from "@/components/agent/run-timeline";
import { SuspectBoard } from "@/components/agent/suspect-board";
import { ToolInspector } from "@/components/agent/tool-inspector";
import { statusBadge } from "@/components/incidents/badges";
import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  cancelRun,
  fetchRun,
  fetchRuns,
  isRunning,
  startInvestigation,
  toolCallCount,
  type AgentRunDetail,
} from "@/lib/api/agent";
import { fetchIncident, fetchIncidents, type IncidentDetail } from "@/lib/api/incidents";
import { fetchModelSettings } from "@/lib/api/models";
import { formatClock, formatLatency } from "@/lib/format";
import { usePolling } from "@/lib/hooks/use-polling";
import { cn } from "@/lib/utils";

const TERMINAL_INCIDENT = ["RESOLVED"];

function syncUrl(incident: string | null, run: number | null) {
  if (typeof window === "undefined") return;
  const params = new URLSearchParams();
  if (incident) params.set("incident", incident);
  if (run !== null) params.set("run", String(run));
  const next = `${window.location.pathname}${params.size ? `?${params}` : ""}`;
  window.history.replaceState(null, "", next);
}

export function AgentWorkspace({
  initialIncident,
  initialRun,
}: {
  initialIncident: string | null;
  initialRun: number | null;
}) {
  const { stream } = useLive();
  // Any agent/incident event refetches; agent.step drives the live timeline.
  const lastEvent = stream.events.find(
    (event) => event.type.startsWith("agent.") || event.type.startsWith("incident."),
  );
  const eventKey = lastEvent?.id ?? 0;

  const [incidentRef, setIncidentRef] = useState<string | null>(initialIncident);
  const [runId, setRunId] = useState<number | null>(initialRun);
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const incidents = usePolling(
    `agent-incidents:${eventKey}`,
    useCallback((signal: AbortSignal) => fetchIncidents({ status: "all", limit: 40 }, signal), []),
    10000,
  );
  const settings = usePolling("agent-settings", fetchModelSettings, 60000);
  const threshold = settings.data?.config.agent.confidence_threshold ?? null;

  const items = incidents.data?.items ?? [];
  const selectedIncident =
    incidentRef ??
    items.find((item) => ["OPEN", "INVESTIGATING", "DIAGNOSED"].includes(item.status))
      ?.public_id ??
    items[0]?.public_id ??
    null;

  const detail = usePolling<IncidentDetail | null>(
    `agent-incident:${selectedIncident}:${eventKey}`,
    useCallback(
      (signal: AbortSignal) =>
        selectedIncident ? fetchIncident(selectedIncident, signal) : Promise.resolve(null),
      [selectedIncident],
    ),
    15000,
  );
  const runs = usePolling(
    `agent-runs:${selectedIncident}:${eventKey}`,
    useCallback(
      (signal: AbortSignal) =>
        selectedIncident
          ? fetchRuns({ incident: selectedIncident, limit: 20 }, signal)
          : Promise.resolve([]),
      [selectedIncident],
    ),
    15000,
  );
  const runList = runs.data ?? [];
  const activeRunId = runId ?? runList[0]?.id ?? null;
  const listedActive = runList.find((run) => run.id === activeRunId);

  const run = usePolling<AgentRunDetail | null>(
    `agent-run:${activeRunId}:${eventKey}`,
    useCallback(
      (signal: AbortSignal) =>
        activeRunId !== null ? fetchRun(activeRunId, signal) : Promise.resolve(null),
      [activeRunId],
    ),
    // SSE drives updates; the short poll is the fallback while a run is live.
    isRunning(listedActive) ? 2000 : 30000,
  );
  const current = run.data && run.data.id === activeRunId ? run.data : null;
  const steps = useMemo(() => current?.steps ?? [], [current]);
  const lastSeq = steps.length ? steps[steps.length - 1]?.seq ?? null : null;
  const inspected =
    steps.find((step) => step.seq === selectedSeq) ??
    (lastSeq !== null ? steps.find((step) => step.seq === lastSeq) : null) ??
    null;

  useEffect(() => {
    syncUrl(selectedIncident, activeRunId);
  }, [selectedIncident, activeRunId]);

  const chooseIncident = (ref: string) => {
    setIncidentRef(ref);
    setRunId(null);
    setSelectedSeq(null);
    setSelectedCode(null);
  };

  const handleInvestigate = async () => {
    if (!selectedIncident) return;
    setBusy(true);
    setActionError(null);
    try {
      const started = await startInvestigation(selectedIncident);
      setRunId(started.id);
      setSelectedSeq(null);
      runs.refresh();
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : "Investigation failed to start");
    } finally {
      setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (activeRunId === null) return;
    setBusy(true);
    setActionError(null);
    try {
      await cancelRun(activeRunId);
      run.refresh();
      runs.refresh();
    } catch (exc) {
      setActionError(exc instanceof Error ? exc.message : "Cancel failed");
    } finally {
      setBusy(false);
    }
  };

  const incident = detail.data && detail.data.public_id === selectedIncident ? detail.data : null;
  const evidence = incident?.evidence ?? [];
  const running = isRunning(current);
  const canInvestigate =
    Boolean(incident) && !running && !TERMINAL_INCIDENT.includes(incident?.status ?? "");

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2">
              <Bot className="size-4 text-primary" aria-hidden />
              Investigation
              {incident ? (
                <Badge variant={statusBadge(incident.status)} className="uppercase">
                  {incident.status}
                </Badge>
              ) : null}
              {current ? (
                <Badge
                  variant={
                    current.status === "DIAGNOSED"
                      ? "success"
                      : current.status === "RUNNING"
                        ? "default"
                        : current.status === "ESCALATED"
                          ? "warning"
                          : "destructive"
                  }
                >
                  run #{current.id} {current.status}
                </Badge>
              ) : null}
            </CardTitle>
            <CardDescription>
              {incident ? (
                <>
                  <Link
                    href="/incidents"
                    className="font-mono underline-offset-2 hover:underline"
                    title="Open the Incidents screen"
                  >
                    {incident.public_id}
                  </Link>{" "}
                  · {incident.title}
                </>
              ) : (
                "Select an incident."
              )}
            </CardDescription>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={selectedIncident ?? ""}
              onChange={(event) => chooseIncident(event.target.value)}
              className="h-8 rounded-md border border-border bg-background px-2 text-xs"
              aria-label="Incident"
            >
              {items.map((item) => (
                <option key={item.public_id} value={item.public_id}>
                  {item.public_id} · {item.status} · {item.primary_service ?? "—"}
                </option>
              ))}
            </select>
            <Button size="sm" onClick={handleInvestigate} disabled={!canInvestigate || busy}>
              <Play className="size-3.5" />
              {runList.length > 0 ? "Rerun" : "Investigate"}
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={handleCancel}
              disabled={!running || busy}
            >
              <Square className="size-3.5" />
              Cancel
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                run.refresh();
                runs.refresh();
                detail.refresh();
              }}
              title="Refresh"
            >
              <RefreshCw className={cn("size-3.5", run.loading && "animate-spin")} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {actionError ? <p className="mb-2 text-[11px] text-destructive">{actionError}</p> : null}
          <div className="grid gap-2 text-[11px] sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="tool calls" value={current ? `${toolCallCount(steps)} / 20` : "—"} />
            <Stat label="model turns" value={current ? String(current.llm_calls) : "—"} />
            <Stat
              label="tokens in / out"
              value={current ? `${current.tokens_in} / ${current.tokens_out}` : "—"}
            />
            <Stat label="cost" value={current ? `$${current.cost_usd.toFixed(4)}` : "—"} />
            <Stat
              label="wall clock"
              value={current?.latency_ms != null ? formatLatency(current.latency_ms) : "—"}
            />
            <Stat
              label="model"
              value={current ? `${current.provider ?? "—"} · ${current.model ?? "—"}` : "—"}
            />
          </div>
          {runList.length > 0 ? (
            <div className="mt-3 flex flex-wrap items-center gap-1 text-[10px]">
              <span className="text-muted-foreground">runs</span>
              {runList.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setRunId(item.id);
                    setSelectedSeq(null);
                  }}
                  className={cn(
                    "rounded border px-1.5 py-0.5 font-mono",
                    item.id === activeRunId
                      ? "border-primary bg-primary/10 text-primary"
                      : "border-border/60 text-muted-foreground hover:text-foreground",
                  )}
                  title={`${item.status} · started ${formatClock(item.started_at)}`}
                >
                  #{item.id} {item.status.toLowerCase()}
                </button>
              ))}
            </div>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <RunTimeline
          run={current}
          selectedSeq={inspected?.seq ?? null}
          onSelect={(seq) => setSelectedSeq(seq)}
        />
        <DiagnosisCard
          run={current}
          evidence={evidence}
          threshold={threshold}
          selectedCode={selectedCode}
          onCode={setSelectedCode}
        />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <ToolInspector step={inspected} onCode={setSelectedCode} />
        <SuspectBoard
          suspects={incident?.suspects ?? []}
          evidence={evidence}
          diagnosis={current?.diagnosis ?? null}
          onCode={setSelectedCode}
        />
      </div>

      <EvidenceViewer
        evidence={evidence}
        cited={current?.diagnosis?.evidence_codes ?? []}
        selectedCode={selectedCode}
        onSelect={setSelectedCode}
      />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-border/60 px-2 py-1.5">
      <p className="text-[10px] uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="tabular truncate font-mono">{value}</p>
    </div>
  );
}
