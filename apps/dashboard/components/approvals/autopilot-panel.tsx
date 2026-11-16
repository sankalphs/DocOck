"use client";

import { useCallback } from "react";
import { Bot, TriangleAlert } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchAutopilot, fetchDrift } from "@/lib/api/autopilot";
import { formatClock } from "@/lib/format";
import { usePolling } from "@/lib/hooks/use-polling";

/** What the autonomous loop is doing, and how far the lab is from its desired state. */
export function AutopilotPanel() {
  const { stream } = useLive();
  const latest = stream.events.find(
    (event) => event.type === "autopilot.decision" || event.type.startsWith("verification."),
  );
  const status = usePolling(
    `autopilot:${latest?.id ?? 0}`,
    useCallback((signal: AbortSignal) => fetchAutopilot(signal), []),
    5000,
  );
  const drift = usePolling(
    `drift:${latest?.id ?? 0}`,
    useCallback((signal: AbortSignal) => fetchDrift(signal), []),
    10000,
  );
  const pilot = status.data;
  const items = drift.data?.items ?? [];
  const decisions = [...(pilot?.recent_decisions ?? [])].reverse().slice(0, 8);

  return (
    <Card data-testid="autopilot-panel">
      <CardHeader>
        <div>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <Bot className="size-3.5 text-primary" aria-hidden />
            Autopilot
            <Badge variant={pilot?.enabled ? (pilot.running ? "success" : "warning") : "outline"}>
              {pilot ? (pilot.enabled ? (pilot.running ? "on" : "stalled") : "off") : "…"}
            </Badge>
            {pilot?.enabled ? (
              <>
                <Badge variant="outline">auto-approve ≤ {pilot.max_risk} risk</Badge>
                <Badge variant="outline">{pilot.max_attempts} attempts</Badge>
                <Badge variant="outline">
                  planner {pilot.llm_planner ? "LLM + deterministic" : "deterministic"}
                </Badge>
              </>
            ) : null}
          </CardTitle>
          <CardDescription>
            {pilot?.enabled
              ? "Every incident is investigated, fixed, verified and retried with the next candidate without a human. Each action still passes the catalog, the safety boundary, preconditions and a single-use token."
              : "Off: a human approves every remediation below. Set AUTOPILOT_ENABLED=true to run the loop autonomously."}
          </CardDescription>
        </div>
      </CardHeader>
      <CardContent className="grid gap-4 text-xs md:grid-cols-2">
        <section>
          <h3 className="mb-1 font-medium">Drift from the desired state</h3>
          {drift.error ? <p className="text-destructive">{drift.error}</p> : null}
          {items.length === 0 ? (
            <p className="text-muted-foreground">
              None: every container, dependency, flag and the load generator match.
            </p>
          ) : (
            <ul className="space-y-1">
              {items.slice(0, 8).map((item) => (
                <li key={`${item.kind}:${item.component}`} className="flex flex-wrap gap-1.5">
                  <TriangleAlert className="mt-0.5 size-3 text-amber-600" aria-hidden />
                  <span>{item.detail}</span>
                  {item.fix ? (
                    <code className="font-mono text-muted-foreground">
                      → {item.fix.action_id} {item.fix.target}
                    </code>
                  ) : (
                    <span className="text-destructive">no automatic fix</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
        <section>
          <h3 className="mb-1 font-medium">Recent decisions</h3>
          {decisions.length === 0 ? (
            <p className="text-muted-foreground">No decisions yet.</p>
          ) : (
            <ul className="space-y-1">
              {decisions.map((decision) => (
                <li key={`${decision.at}-${decision.kind}`} className="break-words">
                  <span className="font-mono text-muted-foreground">
                    {formatClock(decision.at)}
                  </span>{" "}
                  <span className="font-mono">{decision.incident}</span>{" "}
                  <Badge variant={decision.kind === "escalate" ? "destructive" : "secondary"}>
                    {decision.kind}
                  </Badge>{" "}
                  {decision.detail}
                </li>
              ))}
            </ul>
          )}
        </section>
      </CardContent>
    </Card>
  );
}
