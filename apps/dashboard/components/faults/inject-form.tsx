"use client";

import { useState } from "react";
import { AlertTriangle, FlaskConical, Play, ShieldCheck } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  FaultCatalog,
  FaultDefinition,
  InjectionPlan,
  InjectionRequest,
} from "@/lib/api/faults";
import { cn } from "@/lib/utils";

type Values = Record<string, string>;

function defaultsFor(definition: FaultDefinition): Values {
  const values: Values = {};
  for (const param of definition.params) {
    values[param.name] = String(param.default ?? "");
  }
  return values;
}

function buildPayload(
  definition: FaultDefinition,
  target: string,
  values: Values,
  ttlSeconds: number,
): InjectionRequest {
  const params: Record<string, unknown> = {};
  for (const param of definition.params) {
    const raw = values[param.name] ?? "";
    params[param.name] = param.kind === "number" ? Number(raw) : raw;
  }
  return {
    fault_id: definition.id,
    target,
    params,
    ttl_s: ttlSeconds,
    actor: "dashboard",
  };
}

const inputClass =
  "h-8 w-full rounded-md border border-input bg-card px-2 text-xs text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function PlanSummary({ plan }: { plan: InjectionPlan }) {
  const safety = plan.safety ?? {};
  const pre = plan.pre_image ?? {};
  return (
    <div className="mt-4 rounded-lg border border-primary/30 bg-primary/5 p-3">
      <div className="flex items-center gap-2 text-xs font-medium text-primary">
        <ShieldCheck className="size-3.5" aria-hidden />
        Dry run — nothing was written
      </div>
      <dl className="mt-2 grid gap-x-4 gap-y-1 text-[11px] sm:grid-cols-2">
        <div className="flex gap-1">
          <dt className="text-muted-foreground">container</dt>
          <dd className="font-mono">{String(safety.container ?? "—")}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-muted-foreground">project label</dt>
          <dd className="font-mono">{String(safety.project ?? "—")}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-muted-foreground">network</dt>
          <dd className="font-mono">{String(safety.network ?? "—")}</dd>
        </div>
        <div className="flex gap-1">
          <dt className="text-muted-foreground">ttl</dt>
          <dd className="font-mono">{String(plan.ttl_s ?? "—")}s</dd>
        </div>
        <div className="col-span-full flex flex-wrap gap-1">
          <dt className="text-muted-foreground">pre-image</dt>
          <dd className="font-mono break-all text-muted-foreground">
            {JSON.stringify(pre)}
          </dd>
        </div>
        {plan.baseline !== undefined && plan.baseline !== null ? (
          <div className="col-span-full flex gap-1">
            <dt className="text-muted-foreground">baseline symptom</dt>
            <dd className="font-mono">{String(plan.baseline)}</dd>
          </div>
        ) : null}
      </dl>
      <p className="mt-2 text-[10px] text-muted-foreground">
        A real injection performs exactly this plan, then emits{" "}
        <code className="font-mono">fault.injected</code>.
      </p>
    </div>
  );
}

export function InjectForm({
  catalog,
  busy,
  error,
  plan,
  onDryRun,
  onInject,
}: {
  catalog: FaultCatalog;
  busy: string | null;
  error: string | null;
  plan: InjectionPlan | null;
  onDryRun: (body: InjectionRequest) => void;
  onInject: (body: InjectionRequest) => void;
}) {
  const [faultId, setFaultId] = useState(catalog.faults[0]?.id ?? "");
  const definition =
    catalog.faults.find((item) => item.id === faultId) ?? catalog.faults[0];
  const [target, setTarget] = useState(definition?.target ?? "");
  const [values, setValues] = useState<Values>(definition ? defaultsFor(definition) : {});
  const [ttl, setTtl] = useState(String(definition?.default_ttl_s ?? 900));

  if (!definition) return null;
  const application = catalog.faults.filter((item) => item.category === "application");
  const infrastructure = catalog.faults.filter((item) => item.category === "infrastructure");

  const selectFault = (nextId: string) => {
    const next = catalog.faults.find((item) => item.id === nextId);
    if (!next) return;
    setFaultId(nextId);
    setTarget(next.target);
    setValues(defaultsFor(next));
    setTtl(String(next.default_ttl_s));
  };

  const payload = () => buildPayload(definition, target, values, Number(ttl) || 60);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle className="flex items-center gap-2">
            <FlaskConical className="size-3.5 text-primary" aria-hidden />
            Inject a fault
          </CardTitle>
          <CardDescription>
            Generated from <code className="font-mono text-[10px]">GET /api/faults/catalog</code>.
            Variants are the flagd sets — the API rejects anything undeclared.
          </CardDescription>
        </div>
        <Badge variant="outline">{catalog.faults.length} faults</Badge>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground sm:col-span-2">
            Fault
            <select
              value={faultId}
              onChange={(event) => selectFault(event.target.value)}
              className={inputClass}
            >
              <optgroup label="Application (flagd)">
                {application.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} — {item.target}
                  </option>
                ))}
              </optgroup>
              <optgroup label="Infrastructure (Docker)">
                {infrastructure.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.label} — {item.target}
                  </option>
                ))}
              </optgroup>
            </select>
          </label>

          <div className="sm:col-span-2 rounded-lg border border-border/70 bg-muted/20 p-3 text-xs">
            <p className="text-foreground">{definition.description}</p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge variant="outline">{definition.category}</Badge>
              <Badge variant="outline">{definition.mechanism}</Badge>
              <Badge variant={definition.risk === "medium" ? "warning" : "secondary"}>
                {definition.risk} risk
              </Badge>
              {definition.flag ? (
                <Badge variant="secondary" className="font-mono">
                  {definition.flag}
                </Badge>
              ) : null}
            </div>
            <p className="mt-2 text-[11px] text-muted-foreground">
              <span className="font-medium text-foreground">Observed as:</span>{" "}
              {definition.symptom.detail || definition.symptom.probe} on{" "}
              <code className="font-mono">{definition.symptom.service}</code>.
            </p>
            {definition.notes ? (
              <p className="mt-1 text-[10px] text-muted-foreground">{definition.notes}</p>
            ) : null}
          </div>

          {definition.target_mode === "choice" ? (
            <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              Target
              <select
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                className={inputClass}
              >
                {(definition.targets.length > 0 ? definition.targets : [definition.target]).map(
                  (name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ),
                )}
              </select>
            </label>
          ) : (
            <div className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              Target
              <div className="flex h-8 items-center gap-2 rounded-md border border-border bg-muted/30 px-2 text-xs normal-case tracking-normal text-foreground">
                <span className="font-mono">{definition.target}</span>
                <Badge variant="outline">fixed</Badge>
              </div>
            </div>
          )}

          {definition.params.map((param) => (
            <label
              key={param.name}
              className="flex flex-col gap-1 text-xs font-medium text-muted-foreground"
            >
              {param.label}
              {param.kind === "number" ? (
                <input
                  type="number"
                  min={param.minimum ?? undefined}
                  max={param.maximum ?? undefined}
                  step={param.name === "cpus" ? 0.05 : 1}
                  value={values[param.name] ?? ""}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, [param.name]: event.target.value }))
                  }
                  className={inputClass}
                />
              ) : (
                <select
                  value={values[param.name] ?? ""}
                  onChange={(event) =>
                    setValues((previous) => ({ ...previous, [param.name]: event.target.value }))
                  }
                  className={inputClass}
                >
                  {(param.choices.length > 0
                    ? param.choices
                    : String(param.default ?? "")
                        .split(",")
                        .filter(Boolean)
                  ).map((choice) => (
                    <option key={choice} value={choice}>
                      {choice}
                    </option>
                  ))}
                </select>
              )}
              {param.unit ? (
                <span className="normal-case tracking-normal text-[10px] text-muted-foreground">
                  {param.unit}
                  {param.minimum !== null && param.maximum !== null
                    ? ` · ${param.minimum}–${param.maximum}`
                    : ""}
                </span>
              ) : null}
            </label>
          ))}

          <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
            TTL (seconds)
            <input
              type="number"
              min={5}
              max={catalog.defaults.max_ttl_s}
              step={5}
              value={ttl}
              onChange={(event) => setTtl(event.target.value)}
              className={inputClass}
            />
            <span className="normal-case tracking-normal text-[10px] text-muted-foreground">
              The watchdog reverts at expiry, even if the API is restarted.
            </span>
          </label>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="destructive"
            size="sm"
            disabled={busy !== null}
            onClick={() => onInject(payload())}
          >
            <Play className={cn("size-3.5", busy === "inject" && "animate-pulse")} />
            {busy === "inject" ? "Injecting…" : "Inject fault"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={busy !== null}
            onClick={() => onDryRun(payload())}
          >
            <ShieldCheck className="size-3.5" />
            {busy === "dry-run" ? "Checking…" : "Dry run"}
          </Button>
          <span className="text-[11px] text-muted-foreground">
            Writes are only allowed inside the{" "}
            <code className="font-mono">docock-demo</code> Compose project; anything else is a 403.
          </span>
        </div>

        {error ? (
          <p className="mt-3 flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/10 p-2 text-[11px] text-destructive">
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            {error}
          </p>
        ) : null}
        {plan ? <PlanSummary plan={plan} /> : null}
      </CardContent>
    </Card>
  );
}
