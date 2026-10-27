"use client";

import { Wrench } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { stepCodes, stepTitle, type AgentStep } from "@/lib/api/agent";
import { formatLatency } from "@/lib/format";

function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-64 overflow-auto rounded-md border border-border/60 bg-background/60 p-2 font-mono text-[10px] leading-relaxed whitespace-pre-wrap break-all">
      {value === null || value === undefined ? "—" : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function ToolInspector({
  step,
  onCode,
}: {
  step: AgentStep | null;
  onCode: (code: string) => void;
}) {
  const codes = step ? stepCodes(step) : [];
  return (
    <Card>
      <CardHeader>
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <Wrench className="size-3.5 text-primary" aria-hidden />
            Tool inspector
          </CardTitle>
          <CardDescription className="truncate">
            {step ? stepTitle(step) : "Select a step in the timeline."}
          </CardDescription>
        </div>
        {step ? (
          <div className="flex items-center gap-1">
            <Badge variant="outline">#{step.seq}</Badge>
            {step.latency_ms !== null ? (
              <Badge variant="outline">{formatLatency(step.latency_ms)}</Badge>
            ) : null}
          </div>
        ) : null}
      </CardHeader>
      <CardContent>
        {step ? (
          <div className="space-y-2">
            {codes.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1 text-[10px] text-muted-foreground">
                evidence
                {codes.map((code) => (
                  <button
                    key={code}
                    type="button"
                    onClick={() => onCode(code)}
                    className="rounded border border-primary/40 px-1 font-mono text-primary hover:bg-primary/10"
                  >
                    {code}
                  </button>
                ))}
              </div>
            ) : null}
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                {step.kind === "tool" ? "arguments" : "input"}
              </p>
              <Json value={step.input} />
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-muted-foreground">
                output (trimmed to 4000 chars per field)
              </p>
              <Json value={step.output} />
            </div>
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">Nothing selected.</p>
        )}
      </CardContent>
    </Card>
  );
}
