"use client";

import { Loader2, Radio } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { stepTitle, stepTone, type AgentRunDetail } from "@/lib/api/agent";
import { formatClock, formatLatency } from "@/lib/format";
import { cn } from "@/lib/utils";

export function RunTimeline({
  run,
  selectedSeq,
  onSelect,
}: {
  run: AgentRunDetail | null;
  selectedSeq: number | null;
  onSelect: (seq: number) => void;
}) {
  const running = run?.status === "RUNNING";
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Radio className={cn("size-3.5 text-primary", running && "animate-pulse")} aria-hidden />
            Live timeline
          </CardTitle>
          <CardDescription>
            Every step is persisted and pushed over SSE (<code className="font-mono">agent.step</code>
            ). Select a step to inspect its input and output.
          </CardDescription>
        </div>
        {run ? <Badge variant="outline">{run.steps.length} steps</Badge> : null}
      </CardHeader>
      <CardContent>
        {run && run.steps.length > 0 ? (
          <ol className="max-h-[30rem] space-y-1 overflow-y-auto pr-1" aria-label="Agent steps">
            {run.steps.map((step) => (
              <li key={step.seq}>
                <button
                  type="button"
                  onClick={() => onSelect(step.seq)}
                  aria-current={selectedSeq === step.seq}
                  className={cn(
                    "flex w-full items-center gap-2 rounded-md border px-2 py-1.5 text-left text-[11px] transition-colors",
                    selectedSeq === step.seq
                      ? "border-primary/60 bg-primary/10"
                      : "border-border/60 hover:bg-accent/60",
                  )}
                >
                  <span className="tabular w-5 shrink-0 text-muted-foreground">{step.seq}</span>
                  <Badge variant={stepTone(step)} className="w-20 shrink-0 justify-center text-[10px]">
                    {step.kind}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate font-mono">{stepTitle(step)}</span>
                  <span className="tabular shrink-0 text-[10px] text-muted-foreground">
                    {step.latency_ms !== null ? formatLatency(step.latency_ms) : ""}
                  </span>
                  <span className="tabular shrink-0 text-[10px] text-muted-foreground">
                    {formatClock(step.at)}
                  </span>
                </button>
              </li>
            ))}
            {running ? (
              <li className="flex items-center gap-2 px-2 py-1.5 text-[11px] text-muted-foreground">
                <Loader2 className="size-3 animate-spin" aria-hidden />
                investigating — waiting for the next step…
              </li>
            ) : null}
          </ol>
        ) : (
          <p className="text-xs text-muted-foreground">
            {run ? "Starting…" : "No run yet. Start an investigation to watch it here."}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
