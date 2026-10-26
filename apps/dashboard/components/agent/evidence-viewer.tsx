"use client";

import { useEffect, useMemo, useState } from "react";
import { Database } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { EvidenceRow } from "@/lib/api/incidents";
import { formatClock } from "@/lib/format";
import { cn } from "@/lib/utils";

export function EvidenceViewer({
  evidence,
  cited,
  selectedCode,
  onSelect,
}: {
  evidence: EvidenceRow[];
  cited: string[];
  selectedCode: string | null;
  onSelect: (code: string) => void;
}) {
  const [citedOnly, setCitedOnly] = useState(false);
  const [query, setQuery] = useState("");
  const citedSet = useMemo(() => new Set(cited), [cited]);

  const rows = evidence.filter((row) => {
    if (citedOnly && !citedSet.has(row.code)) return false;
    if (!query) return true;
    const needle = query.toLowerCase();
    return (
      row.code.toLowerCase().includes(needle) ||
      row.type.includes(needle) ||
      (row.service ?? "").includes(needle)
    );
  });

  useEffect(() => {
    if (!selectedCode) return;
    document
      .getElementById(`evidence-${selectedCode}`)
      ?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [selectedCode]);

  const selected = evidence.find((row) => row.code === selectedCode) ?? null;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <Database className="size-3.5 text-primary" aria-hidden />
            Evidence viewer
          </CardTitle>
          <CardDescription>
            Rows created by code (analysis and tool observations). Agents may cite only these
            codes.
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="filter code/type/service"
            className="h-7 w-44 rounded-md border border-border bg-background px-2 text-[11px]"
            aria-label="Filter evidence"
          />
          <button
            type="button"
            onClick={() => setCitedOnly((value) => !value)}
            aria-pressed={citedOnly}
            className={cn(
              "rounded-md px-2 py-1 text-[11px]",
              citedOnly ? "bg-accent text-foreground" : "text-muted-foreground",
            )}
          >
            cited only
          </button>
          <Badge variant="outline">
            {rows.length}/{evidence.length}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 lg:grid-cols-[1fr_1.2fr]">
          <ul className="max-h-[26rem] space-y-1 overflow-y-auto pr-1">
            {rows.map((row) => (
              <li key={row.code} id={`evidence-${row.code}`}>
                <button
                  type="button"
                  onClick={() => onSelect(row.code)}
                  className={cn(
                    "flex w-full items-center gap-2 rounded border px-2 py-1 text-left",
                    row.code === selectedCode
                      ? "border-primary bg-primary/10"
                      : "border-border/60 hover:bg-accent/50",
                  )}
                >
                  <code className="font-mono text-[11px] text-primary">{row.code}</code>
                  <Badge variant="outline" className="text-[10px]">
                    {row.type}
                  </Badge>
                  <span className="truncate text-[10px] text-muted-foreground">
                    {row.service ?? row.source}
                  </span>
                  {citedSet.has(row.code) ? (
                    <Badge variant="success" className="ml-auto text-[10px]">
                      cited
                    </Badge>
                  ) : null}
                </button>
              </li>
            ))}
          </ul>
          <div className="min-w-0">
            {selected ? (
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2 text-[11px]">
                  <code className="font-mono text-primary">{selected.code}</code>
                  <Badge variant="outline">{selected.type}</Badge>
                  <span className="text-muted-foreground">
                    {selected.source} · {selected.service ?? "—"} · {formatClock(selected.at)}
                  </span>
                </div>
                <pre className="max-h-[24rem] overflow-auto rounded-md border border-border/60 bg-background/60 p-2 font-mono text-[10px] whitespace-pre-wrap break-all">
                  {JSON.stringify(selected.data, null, 2)}
                </pre>
                <p className="font-mono text-[9px] text-muted-foreground">hash {selected.hash}</p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Select a row, a citation, or a code in the inspector.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
