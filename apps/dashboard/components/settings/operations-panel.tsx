"use client";

import { Gauge, ShieldCheck, Timer, Wallet } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { budgetShare, fetchStats, spread, usd, type Stats } from "@/lib/api/stats";
import { formatClock } from "@/lib/format";
import { usePolling } from "@/lib/hooks/use-polling";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-border/50 py-1.5 text-sm last:border-0">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right font-mono text-xs tabular">{value}</span>
    </div>
  );
}

function Counts({ values }: { values: Record<string, number> }) {
  const entries = Object.entries(values);
  if (entries.length === 0) return <span className="text-muted-foreground">none</span>;
  return (
    <span className="flex flex-wrap justify-end gap-1">
      {entries.map(([key, count]) => (
        <Badge key={key} variant="outline">
          {key} {count}
        </Badge>
      ))}
    </span>
  );
}

function BudgetBar({ stats }: { stats: Stats }) {
  const share = budgetShare(stats);
  if (share === null) return <p className="text-xs text-muted-foreground">No daily cap set.</p>;
  const tone = share >= 0.9 ? "bg-destructive" : share >= 0.6 ? "bg-warning" : "bg-success";
  return (
    <div className="space-y-1" data-testid="budget-bar">
      <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
        <div className={`h-full ${tone}`} style={{ width: `${Math.max(2, share * 100)}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">
        {usd(stats.agent.spent_today_usd)} of {usd(stats.agent.daily_budget_usd)} today (
        {Math.round(share * 100)}%). New investigations stop at the cap.
      </p>
    </div>
  );
}

export function OperationsPanel() {
  const poll = usePolling<Stats>("stats", (signal) => fetchStats(168, signal), 15000);
  const stats = poll.data;
  if (!stats) {
    return (
      <Card>
        <CardContent className="text-sm text-muted-foreground">
          {poll.error ? `Cannot load statistics: ${poll.error}` : "Loading statistics…"}
        </CardContent>
      </Card>
    );
  }
  const { agent, incidents, remediation, rate_limits: limits } = stats;
  return (
    <div className="grid gap-4 lg:grid-cols-2" data-testid="operations-panel">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Wallet className="size-4" /> Agent cost
          </CardTitle>
          <CardDescription>Last {stats.window_h} h · updated {formatClock(stats.generated_at)}</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <BudgetBar stats={stats} />
          <div>
            <Row label="Runs" value={agent.runs} />
            <Row label="By status" value={<Counts values={agent.by_status} />} />
            <Row label="Tokens in / out" value={`${agent.tokens_in} / ${agent.tokens_out}`} />
            <Row label="Cost in window" value={usd(agent.cost_usd)} />
            {agent.models.map((model) => (
              <Row
                key={`${model.provider}/${model.model}`}
                label={`${model.provider} / ${model.model}`}
                value={`${model.runs} runs · ${usd(model.cost_usd)}`}
              />
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Timer className="size-4" /> Latency and outcomes
          </CardTitle>
          <CardDescription>p50 / p95 over the same window</CardDescription>
        </CardHeader>
        <CardContent>
          <Row label="Agent investigation" value={spread(agent.latency_ms, "s", 1000)} />
          <Row label="Incident open → closed" value={spread(incidents.time_to_close_s, "s")} />
          <Row label="Verification" value={spread(remediation.verification_s, "s")} />
          <Row label="Incidents opened" value={incidents.opened} />
          <Row label="Open now" value={incidents.open} />
          <Row label="Incidents by status" value={<Counts values={incidents.by_status} />} />
          <Row label="Verdicts" value={<Counts values={remediation.verdicts} />} />
          <Row label="Actions" value={<Counts values={remediation.by_status} />} />
          <Row label="Approvals" value={<Counts values={remediation.approvals} />} />
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Gauge className="size-4" /> Rate limits
            <Badge variant={limits.enabled ? "success" : "warning"}>
              {limits.enabled ? "enforced" : "off"}
            </Badge>
          </CardTitle>
          <CardDescription>
            Per client, sliding 60 s window, writes only. {limits.rejected} request(s) rejected
            since the API started. The first matching rule applies.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-x-6 sm:grid-cols-2">
          {limits.rules.map((rule) => (
            <Row
              key={rule.name}
              label={rule.name}
              value={`${rule.limit} / ${rule.window_s} s · ${rule.pattern}`}
            />
          ))}
        </CardContent>
      </Card>

      <Card className="lg:col-span-2">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4" /> Guardrails
          </CardTitle>
          <CardDescription>Load-bearing safety controls. They are not editable from the browser.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-1 text-sm text-muted-foreground">
          <p>Fault and action allowlists: only catalogued actions on the demo&apos;s containers.</p>
          <p>Every fault has a TTL; a watchdog reverts it even when nobody does.</p>
          <p>Approval tokens are single-use, short-lived and bound to one action; only their hash is stored.</p>
          <p>A diagnosis may cite only evidence codes that exist for its incident.</p>
          <p>Secrets are read from the environment, masked in every response, and never stored.</p>
        </CardContent>
      </Card>
    </div>
  );
}
