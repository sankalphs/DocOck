"use client";

import { useState } from "react";
import { KeyRound, PlugZap, RotateCcw, Save } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  fetchModelSettings,
  overriddenFields,
  patchModelSettings,
  testModels,
  type ModelSettingsPatch,
  type ModelSettingsView,
  type ModelTestResult,
} from "@/lib/api/models";
import { usePolling } from "@/lib/hooks/use-polling";
import { formatClock } from "@/lib/format";

function Row({
  label,
  value,
  overridden,
}: {
  label: string;
  value: React.ReactNode;
  overridden?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/50 py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="flex min-w-0 items-center gap-2 text-right font-mono text-xs break-all">
        {value}
        {overridden ? <Badge variant="warning">override</Badge> : null}
      </span>
    </div>
  );
}

function TestResultRow({ result }: { result: ModelTestResult }) {
  return (
    <div className="rounded-md border border-border/70 p-3 text-sm" data-testid={`test-${result.target}`}>
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={result.ok ? "success" : "destructive"}>{result.ok ? "ok" : "failed"}</Badge>
        <span className="font-medium">{result.target}</span>
        <span className="font-mono text-xs text-muted-foreground">{result.provider}</span>
        {result.latency_ms !== null && result.latency_ms !== undefined ? (
          <span className="tabular text-xs">{result.latency_ms.toFixed(1)} ms</span>
        ) : null}
      </div>
      <div className="mt-1 grid gap-0.5 text-xs text-muted-foreground">
        <span>
          model requested <span className="font-mono text-foreground">{result.model_requested}</span>
          {result.model_reported ? (
            <>
              {" "}
              · reported <span className="font-mono text-foreground">{result.model_reported}</span>
            </>
          ) : null}
        </span>
        {result.dimensions ? <span>dimensions {result.dimensions}</span> : null}
        {result.tokens_in !== null && result.tokens_in !== undefined ? (
          <span>
            tokens {result.tokens_in} in / {result.tokens_out ?? 0} out · cost $
            {(result.cost_usd ?? 0).toFixed(6)}
          </span>
        ) : null}
        <span>
          {result.error_kind ? `${result.error_kind}: ` : ""}
          {result.detail}
        </span>
      </div>
    </div>
  );
}

export function ModelSettingsPanel() {
  const poll = usePolling<ModelSettingsView>("models", fetchModelSettings, 15000);
  const [draft, setDraft] = useState<{ llm: string; model: string; embedding: string } | null>(
    null,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<ModelTestResult[] | null>(null);
  const [testedAt, setTestedAt] = useState<string | null>(null);

  const view = poll.data;
  if (!view) {
    return (
      <Card>
        <CardContent className="text-sm text-muted-foreground">
          {poll.error ? `Cannot load model settings: ${poll.error}` : "Loading model settings…"}
        </CardContent>
      </Card>
    );
  }
  const cfg = view.config;
  const llmOver = overriddenFields(view, "llm");
  const embOver = overriddenFields(view, "embedding");
  const current = draft ?? {
    llm: cfg.llm.provider,
    model: cfg.llm.model,
    embedding: cfg.embedding.provider,
  };

  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Request failed");
    } finally {
      setBusy(null);
    }
  };

  const save = () =>
    run("save", async () => {
      const patch: ModelSettingsPatch = {
        llm: { provider: current.llm as "stub" | "zenmux" | "openai_compatible", model: current.model },
        embedding: { provider: current.embedding as "onnx_local" | "remote" | "stub" },
        reset: false,
      };
      await patchModelSettings(patch);
      setDraft(null);
      poll.refresh();
    });

  const reset = () =>
    run("reset", async () => {
      await patchModelSettings({ reset: true });
      setDraft(null);
      poll.refresh();
    });

  const test = (target: "llm" | "embedding" | "all") =>
    run(`test-${target}`, async () => {
      const report = await testModels(target);
      setResults(report.results);
      setTestedAt(report.tested_at);
      poll.refresh();
    });

  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Card>
        <CardHeader>
          <div>
            <CardTitle>Reasoning model</CardTitle>
            <CardDescription>OpenAI-compatible wire format; the stub runs offline.</CardDescription>
          </div>
          <Badge variant={view.llm_ready ? "success" : "warning"}>
            {view.llm_ready ? "ready" : "unconfigured"}
          </Badge>
        </CardHeader>
        <CardContent>
          <Row label="provider" value={cfg.llm.provider} overridden={llmOver.has("provider")} />
          <Row label="model" value={cfg.llm.model} overridden={llmOver.has("model")} />
          <Row label="base URL (.env only)" value={cfg.llm.base_url} />
          <Row
            label="API key (.env only)"
            value={
              <span className="flex items-center gap-1">
                <KeyRound className="size-3" />
                {view.llm_api_key.configured ? view.llm_api_key.masked : "not set"}
              </span>
            }
          />
          <Row label="temperature" value={cfg.llm.temperature} />
          <Row label="tool-call budget" value={cfg.llm.max_tool_calls} />
          <Row label="request timeout" value={`${cfg.llm.timeout_s} s · ${cfg.llm.retries} retries`} />
          <Row label="JSON mode" value={cfg.llm.json_mode ? "on" : "off"} />
          <Row
            label="price / M tokens"
            value={`$${cfg.llm.price_input_per_m} in · $${cfg.llm.price_output_per_m} out`}
          />
          <Row label="daily budget" value={`$${cfg.llm.daily_budget_usd.toFixed(2)}`} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div>
            <CardTitle>Embedding model</CardTitle>
            <CardDescription>
              Memory stores vector({view.store_dimensions}); a width change needs a re-embed.
            </CardDescription>
          </div>
          <Badge variant={view.embedding_loaded ? "success" : "outline"}>
            {view.embedding_loaded ? "loaded" : "lazy"}
          </Badge>
        </CardHeader>
        <CardContent>
          <Row
            label="provider"
            value={cfg.embedding.provider}
            overridden={embOver.has("provider")}
          />
          <Row label="model" value={cfg.embedding.model} overridden={embOver.has("model")} />
          <Row label="dimensions" value={cfg.embedding.dimensions} />
          <Row label="ONNX export" value={`${cfg.embedding.onnx_repo} / ${cfg.embedding.onnx_file}`} />
          <Row label="agent confidence gate" value={cfg.agent.confidence_threshold} />
          <Row label="agent wall clock" value={`${cfg.agent.wall_clock_s} s`} />
          <Row
            label="retrieval"
            value={`top ${cfg.retrieval.top_k} · min score ${cfg.retrieval.min_score}`}
          />
        </CardContent>
      </Card>

      <Card className="xl:col-span-2">
        <CardHeader>
          <div>
            <CardTitle>Swap and test</CardTitle>
            <CardDescription>
              A swap is a runtime override stored in the settings table. Keys and base URLs are
              never accepted here.
            </CardDescription>
          </div>
          {view.updated_at ? (
            <span className="text-xs text-muted-foreground">
              overrides saved {formatClock(view.updated_at)}
            </span>
          ) : null}
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-end gap-3 text-sm">
            <label className="grid gap-1">
              <span className="text-xs text-muted-foreground">LLM provider</span>
              <select
                aria-label="LLM provider"
                className="h-9 rounded-md border border-input bg-background px-2"
                value={current.llm}
                onChange={(event) => setDraft({ ...current, llm: event.target.value })}
              >
                {view.providers.llm.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </label>
            <label className="grid min-w-64 gap-1">
              <span className="text-xs text-muted-foreground">LLM model</span>
              <input
                aria-label="LLM model"
                className="h-9 rounded-md border border-input bg-background px-2 font-mono text-xs"
                value={current.model}
                onChange={(event) => setDraft({ ...current, model: event.target.value })}
              />
            </label>
            <label className="grid gap-1">
              <span className="text-xs text-muted-foreground">Embedding provider</span>
              <select
                aria-label="Embedding provider"
                className="h-9 rounded-md border border-input bg-background px-2"
                value={current.embedding}
                onChange={(event) => setDraft({ ...current, embedding: event.target.value })}
              >
                {view.providers.embedding.map((name) => (
                  <option key={name}>{name}</option>
                ))}
              </select>
            </label>
            <Button size="sm" onClick={save} disabled={busy !== null || draft === null}>
              <Save /> Save
            </Button>
            <Button size="sm" variant="outline" onClick={reset} disabled={busy !== null}>
              <RotateCcw /> Reset to .env
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => test("all")} disabled={busy !== null}>
              <PlugZap /> {busy === "test-all" ? "Testing…" : "Test connection"}
            </Button>
            <Button size="sm" variant="ghost" onClick={() => test("llm")} disabled={busy !== null}>
              LLM only
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => test("embedding")}
              disabled={busy !== null}
            >
              Embedding only
            </Button>
          </div>
          {error ? <p className="text-sm text-destructive">{error}</p> : null}
          {results ? (
            <div className="grid gap-2 md:grid-cols-2">
              {results.map((result) => (
                <TestResultRow key={result.target} result={result} />
              ))}
              {testedAt ? (
                <span className="text-xs text-muted-foreground">tested {formatClock(testedAt)}</span>
              ) : null}
            </div>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
