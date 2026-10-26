"use client";

import { AlertTriangle, CheckCircle2, Stethoscope } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { citations, type AgentRun } from "@/lib/api/agent";
import type { EvidenceRow } from "@/lib/api/incidents";
import { cn } from "@/lib/utils";

export function DiagnosisCard({
  run,
  evidence,
  threshold,
  selectedCode,
  onCode,
}: {
  run: AgentRun | null;
  evidence: EvidenceRow[];
  threshold: number | null;
  selectedCode: string | null;
  onCode: (code: string) => void;
}) {
  const diagnosis = run?.diagnosis ?? null;
  const cited = citations(diagnosis, evidence);
  const missing = cited.filter((item) => item.row === null);
  const confidence = diagnosis?.confidence ?? 0;
  const gate = threshold ?? 0.7;
  const passed = Boolean(diagnosis?.valid) && confidence >= gate;

  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <Stethoscope className="size-3.5 text-primary" aria-hidden />
            Diagnosis
          </CardTitle>
          <CardDescription>
            Validated against this incident&apos;s evidence. Every citation links to its evidence
            row.
          </CardDescription>
        </div>
        {diagnosis ? (
          <Badge variant={diagnosis.valid ? (passed ? "success" : "warning") : "destructive"}>
            {diagnosis.valid ? (passed ? "passed gate" : "below gate") : "invalid"}
          </Badge>
        ) : null}
      </CardHeader>
      <CardContent>
        {diagnosis ? (
          <div className="space-y-3 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-muted-foreground">root cause</span>
              <span className="font-mono text-sm font-semibold">
                {diagnosis.root_cause_service}
              </span>
              <Badge variant="outline">{diagnosis.fault_class}</Badge>
            </div>

            <div>
              <div className="flex items-center justify-between text-[11px]">
                <span className="text-muted-foreground">confidence</span>
                <span className="tabular">
                  {(confidence * 100).toFixed(0)}%{" "}
                  <span className="text-muted-foreground">/ gate {(gate * 100).toFixed(0)}%</span>
                </span>
              </div>
              <div className="relative mt-1 h-2 overflow-hidden rounded-full bg-muted">
                <div
                  className={cn("h-full rounded-full", passed ? "bg-success" : "bg-warning")}
                  style={{ width: `${Math.round(confidence * 100)}%` }}
                />
                <div
                  className="absolute top-0 h-full w-px bg-foreground/70"
                  style={{ left: `${Math.round(gate * 100)}%` }}
                  aria-hidden
                />
              </div>
            </div>

            <p className="leading-relaxed">{diagnosis.summary}</p>

            <div>
              <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                citations ({cited.length})
              </p>
              <div className="flex flex-wrap gap-1">
                {cited.map(({ code, row }) => (
                  <a
                    key={code}
                    href={`#evidence-${code}`}
                    onClick={(event) => {
                      event.preventDefault();
                      onCode(code);
                    }}
                    title={row ? `${row.type} · ${row.service ?? row.source}` : "not in evidence"}
                    className={cn(
                      "rounded border px-1.5 py-0.5 font-mono text-[10px]",
                      row === null
                        ? "border-destructive/60 text-destructive"
                        : selectedCode === code
                          ? "border-primary bg-primary/15 text-primary"
                          : "border-primary/40 text-primary hover:bg-primary/10",
                    )}
                  >
                    {code}
                    {row ? <span className="ml-1 text-muted-foreground">{row.type}</span> : null}
                  </a>
                ))}
              </div>
              {missing.length > 0 ? (
                <p className="mt-1 text-[10px] text-destructive">
                  {missing.length} citation(s) not in this incident&apos;s evidence.
                </p>
              ) : null}
            </div>

            <div className="grid gap-2 sm:grid-cols-2">
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  affected services
                </p>
                <div className="flex flex-wrap gap-1">
                  {diagnosis.affected_services.map((service) => (
                    <Badge key={service} variant="outline" className="font-mono">
                      {service}
                    </Badge>
                  ))}
                </div>
              </div>
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  recommended action (not executed)
                </p>
                <code className="font-mono text-[11px]">
                  {diagnosis.recommended_action.action_id ?? "—"} →{" "}
                  {diagnosis.recommended_action.target ?? "—"}
                </code>
              </div>
            </div>

            {diagnosis.alternative_hypotheses.length > 0 ? (
              <div>
                <p className="mb-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                  alternatives
                </p>
                <ul className="space-y-0.5">
                  {diagnosis.alternative_hypotheses.map((alt) => (
                    <li key={alt.service} className="flex items-center gap-2 text-[11px]">
                      <span className="w-32 font-mono">{alt.service}</span>
                      <span className="tabular text-muted-foreground">
                        {(alt.confidence * 100).toFixed(0)}%
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {diagnosis.validation_errors.length > 0 ? (
              <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2">
                <p className="mb-1 flex items-center gap-1 text-[11px] text-destructive">
                  <AlertTriangle className="size-3" aria-hidden /> validator rejected this output
                </p>
                <ul className="list-disc space-y-0.5 pl-4 text-[10px] text-destructive">
                  {diagnosis.validation_errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="flex items-center gap-1 text-[10px] text-muted-foreground">
                <CheckCircle2 className="size-3 text-success" aria-hidden /> no validation errors
              </p>
            )}
          </div>
        ) : run?.error ? (
          <p className="text-xs text-destructive">{run.error}</p>
        ) : (
          <p className="text-xs text-muted-foreground">
            {run?.status === "RUNNING" ? "Investigating…" : "No diagnosis yet."}
          </p>
        )}
        {run?.error && diagnosis ? (
          <p className="mt-2 text-[11px] text-warning">{run.error}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
