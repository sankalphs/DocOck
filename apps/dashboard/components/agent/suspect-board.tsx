"use client";

import { Target } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { DiagnosisView } from "@/lib/api/agent";
import { suspectSignals, type EvidenceRow, type SuspectRow } from "@/lib/api/incidents";
import { cn } from "@/lib/utils";

/** The deterministic ranking (the starting hypothesis) next to the agent's verdict. */
export function SuspectBoard({
  suspects,
  evidence,
  diagnosis,
  onCode,
}: {
  suspects: SuspectRow[];
  evidence: EvidenceRow[];
  diagnosis: DiagnosisView | null;
  onCode: (code: string) => void;
}) {
  const cited = new Set(diagnosis?.evidence_codes ?? []);
  const alternatives = new Map(
    (diagnosis?.alternative_hypotheses ?? []).map((alt) => [alt.service, alt.confidence]),
  );
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Target className="size-3.5 text-primary" aria-hidden />
            Suspect board
          </CardTitle>
          <CardDescription>
            P9 ranking is the starting hypothesis; the agent&apos;s root cause is marked.
          </CardDescription>
        </div>
        <Badge variant="outline">{suspects.length}</Badge>
      </CardHeader>
      <CardContent>
        {suspects.length > 0 ? (
          <ul className="space-y-2">
            {suspects.slice(0, 6).map((suspect) => {
              const isRoot = diagnosis?.root_cause_service === suspect.service;
              const codes = evidence
                .filter((row) => row.service === suspect.service)
                .map((row) => row.code);
              const top = suspectSignals(suspect.signals).filter((s) => s.contribution > 0);
              return (
                <li
                  key={suspect.service}
                  className={cn(
                    "rounded-lg border p-2",
                    isRoot ? "border-success/60 bg-success/5" : "border-border/60",
                  )}
                >
                  <div className="flex items-center gap-2 text-xs">
                    <span className="tabular w-4 text-muted-foreground">{suspect.rank}</span>
                    <span className="font-mono">{suspect.service}</span>
                    {isRoot ? <Badge variant="success">root cause</Badge> : null}
                    {alternatives.has(suspect.service) ? (
                      <Badge variant="outline">
                        alt {((alternatives.get(suspect.service) ?? 0) * 100).toFixed(0)}%
                      </Badge>
                    ) : null}
                    <span className="tabular ml-auto">{(suspect.score * 100).toFixed(0)}%</span>
                  </div>
                  <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn("h-full rounded-full", isRoot ? "bg-success" : "bg-primary")}
                      style={{ width: `${Math.round(suspect.score * 100)}%` }}
                    />
                  </div>
                  <p className="mt-1 truncate text-[10px] text-muted-foreground">
                    {top.map((signal) => signal.name).join(" · ") || "no contributing signal"}
                  </p>
                  {codes.length > 0 ? (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {codes.slice(0, 12).map((code) => (
                        <button
                          key={code}
                          type="button"
                          onClick={() => onCode(code)}
                          className={cn(
                            "rounded border px-1 font-mono text-[10px]",
                            cited.has(code)
                              ? "border-primary/60 bg-primary/10 text-primary"
                              : "border-border/60 text-muted-foreground hover:text-foreground",
                          )}
                          title={cited.has(code) ? "cited by the diagnosis" : "not cited"}
                        >
                          {code}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">
            No ranking yet. The agent runs the deterministic analysis first.
          </p>
        )}
      </CardContent>
    </Card>
  );
}
