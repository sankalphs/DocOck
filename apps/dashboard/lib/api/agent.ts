import { request } from "./client";
import type { EvidenceRow } from "./incidents";
import type { components } from "./schema";

export type RunStatus = "RUNNING" | "DIAGNOSED" | "ESCALATED" | "FAILED" | "CANCELLED";

export type AgentStep = {
  seq: number;
  kind: string;
  tool: string | null;
  input: Record<string, unknown> | null;
  output: Record<string, unknown> | null;
  latency_ms: number | null;
  at: string;
};

export type AlternativeHypothesis = { service: string; confidence: number };

export type DiagnosisView = {
  id: number;
  root_cause_service: string;
  fault_class: string;
  confidence: number;
  summary: string;
  evidence_codes: string[];
  affected_services: string[];
  recommended_action: { action_id?: string; target?: string };
  alternative_hypotheses: AlternativeHypothesis[];
  valid: boolean;
  validation_errors: string[];
  created_at: string;
};

export type AgentRun = {
  id: number;
  incident_id: number;
  incident_ref: string;
  status: RunStatus;
  provider: string | null;
  model: string | null;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
  latency_ms: number | null;
  tool_calls: number;
  llm_calls: number;
  diagnosis: DiagnosisView | null;
};

export type AgentRunDetail = AgentRun & { steps: AgentStep[] };

export const TERMINAL_RUN_STATUSES: RunStatus[] = ["DIAGNOSED", "ESCALATED", "FAILED", "CANCELLED"];

export function isRunning(run: Pick<AgentRun, "status"> | null | undefined): boolean {
  return run?.status === "RUNNING";
}

function mapDiagnosis(raw: components["schemas"]["DiagnosisView"]): DiagnosisView {
  return {
    ...raw,
    recommended_action: raw.recommended_action as DiagnosisView["recommended_action"],
    alternative_hypotheses: (raw.alternative_hypotheses ?? []).map((item) => ({
      service: String(item.service ?? ""),
      confidence: Number(item.confidence ?? 0),
    })),
  };
}

function mapRun(raw: components["schemas"]["AgentRunSummary"]): AgentRun {
  return {
    ...raw,
    status: raw.status as RunStatus,
    diagnosis: raw.diagnosis ? mapDiagnosis(raw.diagnosis) : null,
  };
}

function mapDetail(raw: components["schemas"]["AgentRunDetail"]): AgentRunDetail {
  return {
    ...mapRun(raw),
    steps: (raw.steps ?? []).map((step) => ({
      seq: step.seq,
      kind: step.kind,
      tool: step.tool ?? null,
      input: step.input ?? null,
      output: step.output ?? null,
      latency_ms: step.latency_ms ?? null,
      at: step.at,
    })),
  };
}

export async function startInvestigation(ref: string, actor = "dashboard"): Promise<AgentRunDetail> {
  const raw = await request<components["schemas"]["AgentRunDetail"]>(
    `/api/incidents/${encodeURIComponent(ref)}/investigate?actor=${encodeURIComponent(actor)}`,
    { method: "POST" },
  );
  return mapDetail(raw);
}

export async function fetchRun(id: number, signal?: AbortSignal): Promise<AgentRunDetail> {
  return mapDetail(
    await request<components["schemas"]["AgentRunDetail"]>(`/api/agent/runs/${id}`, { signal }),
  );
}

export async function fetchRuns(
  { incident, limit = 20 }: { incident?: string | null; limit?: number } = {},
  signal?: AbortSignal,
): Promise<AgentRun[]> {
  const path = incident
    ? `/api/incidents/${encodeURIComponent(incident)}/runs?limit=${limit}`
    : `/api/agent/runs?limit=${limit}`;
  const raw = await request<components["schemas"]["AgentRunList"]>(path, { signal });
  return (raw.items ?? []).map(mapRun);
}

export async function cancelRun(id: number): Promise<AgentRun> {
  return mapRun(
    await request<components["schemas"]["AgentRunSummary"]>(`/api/agent/runs/${id}/cancel`, {
      method: "POST",
    }),
  );
}

/** One-line description of a step for the timeline. */
export function stepTitle(step: AgentStep): string {
  const output = step.output ?? {};
  switch (step.kind) {
    case "tool": {
      if (typeof output.error === "string") return `${step.tool} failed`;
      const code = output.evidence_code;
      return typeof code === "string" ? `${step.tool} → ${code}` : `${step.tool}`;
    }
    case "llm":
    case "repair": {
      const calls = Array.isArray(output.tool_calls) ? output.tool_calls : [];
      const label = step.kind === "repair" ? "repair turn" : "model turn";
      if (calls.length > 0) {
        const names = calls.map((call) => String((call as { name?: unknown }).name ?? "?"));
        return `${label}: calls ${names.join(", ")}`;
      }
      return `${label}: answered`;
    }
    case "context":
      return `context: ${String(output.codes_shown ?? 0)} evidence codes shown`;
    case "analysis":
      return `deterministic analysis: top ${String(output.top_suspect ?? "—")}`;
    case "validation":
      return output.valid
        ? `validation #${String(output.attempt ?? 1)}: valid`
        : `validation #${String(output.attempt ?? 1)}: ${
            Array.isArray(output.errors) ? output.errors.length : 0
          } error(s)`;
    case "diagnosis":
      return `diagnosis: ${String(
        (output.root_cause as { service?: unknown } | undefined)?.service ?? "—",
      )}`;
    case "gate":
      return output.passed
        ? `confidence gate passed (${String(output.confidence)} ≥ ${String(output.threshold)})`
        : `confidence gate: escalate (${String(output.confidence ?? "—")} < ${String(
            output.threshold,
          )})`;
    case "cancelled":
      return "cancelled by operator";
    case "budget":
      return `budget exhausted: ${String(output.kind ?? "")}`;
    case "error":
      return `error: ${String(output.error ?? "")}`;
    default:
      return step.kind;
  }
}

/** Tone for a step badge. */
export function stepTone(step: AgentStep): "default" | "success" | "warning" | "destructive" {
  const output = step.output ?? {};
  if (step.kind === "error" || step.kind === "budget" || typeof output.error === "string") {
    return "destructive";
  }
  if (step.kind === "validation") return output.valid ? "success" : "destructive";
  if (step.kind === "gate") return output.passed ? "success" : "warning";
  if (step.kind === "cancelled") return "warning";
  return "default";
}

export type Citation = { code: string; row: EvidenceRow | null };

/** Every cited code, resolved against the incident's evidence rows. */
export function citations(diagnosis: DiagnosisView | null, evidence: EvidenceRow[]): Citation[] {
  if (!diagnosis) return [];
  const byCode = new Map(evidence.map((row) => [row.code, row]));
  return diagnosis.evidence_codes.map((code) => ({ code, row: byCode.get(code) ?? null }));
}

/** Evidence codes a step produced or read, so the inspector can link them. */
export function stepCodes(step: AgentStep): string[] {
  const output = step.output ?? {};
  const codes = new Set<string>();
  if (Array.isArray(output.codes)) {
    for (const code of output.codes) if (typeof code === "string") codes.add(code);
  }
  if (typeof output.evidence_code === "string") codes.add(output.evidence_code);
  if (Array.isArray(output.cited)) {
    for (const code of output.cited) if (typeof code === "string") codes.add(code);
  }
  return [...codes].sort();
}

/** Count of tool steps; matches the budget the API enforces. */
export function toolCallCount(steps: AgentStep[]): number {
  return steps.filter((step) => step.kind === "tool").length;
}
