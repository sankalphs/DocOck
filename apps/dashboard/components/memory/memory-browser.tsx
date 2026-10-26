"use client";

import { useCallback, useState } from "react";
import { Brain, Search, ShieldAlert } from "lucide-react";

import { useLive } from "@/components/live-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { fetchIncidents } from "@/lib/api/incidents";
import {
  fetchMemories,
  fetchMemoryStatus,
  headline,
  scorePercent,
  searchMemory,
  type MemorySearchResult,
} from "@/lib/api/memory";
import { formatClock } from "@/lib/format";
import { usePolling } from "@/lib/hooks/use-polling";

export function MemoryBrowser() {
  const { stream } = useLive();
  const stored = stream.events.find((event) => event.type === "memory.stored");
  const status = usePolling("memory-status", fetchMemoryStatus, 30000);
  const memories = usePolling(
    `memories:${stored?.id ?? 0}`,
    useCallback((signal: AbortSignal) => fetchMemories(100, signal), []),
    30000,
  );
  const incidents = usePolling(
    "memory-incidents",
    useCallback((signal: AbortSignal) => fetchIncidents({ status: "all", limit: 40 }, signal), []),
    30000,
  );

  const [query, setQuery] = useState("");
  const [incident, setIncident] = useState("");
  const [result, setResult] = useState<MemorySearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  const run = async (input: { q?: string; incident?: string }) => {
    setSearching(true);
    setError(null);
    try {
      setResult(await searchMemory(input));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Search failed");
    } finally {
      setSearching(false);
    }
  };

  const s = status.data;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Brain className="size-3.5 text-primary" aria-hidden />
              Memory store
              {s ? (
                <Badge variant={s.ready ? "success" : "destructive"}>
                  {s.ready ? "ready" : "disabled"}
                </Badge>
              ) : null}
            </CardTitle>
            <CardDescription>
              Startup dimension guard: column vector({s?.column_dimensions ?? "—"}) · code{" "}
              {s?.expected_dimensions ?? "—"} · embedder{" "}
              <code className="font-mono">
                {s ? `${String(s.current.provider)}:${String(s.current.model)}` : "—"}
              </code>
            </CardDescription>
          </div>
          <div className="flex gap-1">
            <Badge variant="outline">{s?.memories ?? 0} memories</Badge>
            <Badge variant="outline">{s?.seeds ?? 0} seeds</Badge>
          </div>
        </CardHeader>
        <CardContent>
          {s?.reason ? <p className="text-[11px] text-destructive">{s.reason}</p> : null}
          <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <ShieldAlert className="size-3 text-warning" aria-hidden />
            Retrieved memories reach the agent as hypotheses only. They carry no evidence codes and
            the diagnosis has no field for them.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle className="flex items-center gap-2">
              <Search className="size-3.5 text-primary" aria-hidden />
              Debug retrieval
            </CardTitle>
            <CardDescription>
              Exactly what an investigation would receive: cosine (HNSW) above the floor, plus
              lexical overlap and recency.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-2">
            <select
              value={incident}
              onChange={(event) => setIncident(event.target.value)}
              className="h-8 rounded-md border border-border bg-background px-2 text-xs"
              aria-label="Incident"
            >
              <option value="">— query from incident —</option>
              {(incidents.data?.items ?? []).map((item) => (
                <option key={item.public_id} value={item.public_id}>
                  {item.public_id} · {item.status} · {item.primary_service ?? "—"}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              disabled={!incident || searching}
              onClick={() => run({ incident })}
            >
              Search by incident
            </Button>
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="payment. network. telemetry silence at payment."
              className="h-8 min-w-64 flex-1 rounded-md border border-border bg-background px-2 text-xs"
              aria-label="Free-text query"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!query || searching}
              onClick={() => run({ q: query })}
            >
              Search text
            </Button>
          </div>
          {error ? <p className="mt-2 text-[11px] text-destructive">{error}</p> : null}
          {result ? (
            <div className="mt-3 space-y-2">
              <pre className="rounded-md border border-border/60 bg-background/60 p-2 font-mono text-[10px] whitespace-pre-wrap">
                {result.query}
              </pre>
              <p className="text-[10px] text-muted-foreground">
                top {result.top_k} · floor {result.min_score} · fault class filter{" "}
                {result.fault_class ?? "none"} · {result.hits.length} hit(s)
              </p>
              <ol className="space-y-1.5">
                {result.hits.map((hit, index) => (
                  <li key={hit.memory_id} className="rounded-lg border border-border/60 p-2">
                    <div className="flex items-center gap-2 text-xs">
                      <span className="tabular w-4 text-muted-foreground">{index + 1}</span>
                      <span className="font-mono">{hit.incident_ref ?? `seed-${hit.memory_id}`}</span>
                      {hit.seed ? <Badge variant="secondary">seed</Badge> : null}
                      <Badge variant="outline">{hit.root_cause}</Badge>
                      <Badge variant="outline">{hit.fault_class}</Badge>
                      <span className="tabular ml-auto">{hit.score.toFixed(3)}</span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                      <div
                        className="h-full rounded-full bg-primary"
                        style={{ width: `${scorePercent(hit.score)}%` }}
                      />
                    </div>
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      cosine {hit.cosine.toFixed(3)} · lexical {hit.lexical.toFixed(3)} · recency{" "}
                      {hit.recency.toFixed(3)}
                    </p>
                    <pre className="mt-1 font-mono text-[10px] whitespace-pre-wrap">{hit.summary}</pre>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Stored memories</CardTitle>
            <CardDescription>
              One per resolved incident with a valid diagnosis, from the PLAN §12 template.
            </CardDescription>
          </div>
          <Badge variant="outline">{memories.data?.total ?? 0}</Badge>
        </CardHeader>
        <CardContent>
          <ul className="grid gap-2 lg:grid-cols-2">
            {(memories.data?.items ?? []).map((item) => (
              <li key={item.id} className="rounded-lg border border-border/60 p-2">
                <div className="flex items-center gap-2 text-xs">
                  <span className="font-mono">{item.incident_ref ?? `seed-${item.id}`}</span>
                  {item.seed ? <Badge variant="secondary">seed</Badge> : null}
                  <Badge variant="outline">{item.fault_class}</Badge>
                  <span className="ml-auto text-[10px] text-muted-foreground">
                    {formatClock(item.created_at)}
                  </span>
                </div>
                <p className="mt-1 text-[11px] font-medium">{headline(item.summary)}</p>
                <pre className="mt-1 font-mono text-[10px] whitespace-pre-wrap text-muted-foreground">
                  {item.summary.split("\n").slice(1).join("\n")}
                </pre>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
