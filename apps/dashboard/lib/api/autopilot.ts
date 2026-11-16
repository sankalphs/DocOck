import { request } from "./client";

export type AutopilotDecision = {
  at: string;
  incident: string;
  kind: "investigate" | "remediate" | "escalate" | string;
  detail: string;
  auto_approved?: boolean;
};

export type AutopilotStatus = {
  enabled: boolean;
  running: boolean;
  max_attempts: number;
  max_risk: string;
  investigate_delay_s: number;
  llm_planner: boolean;
  last_sweep: string | null;
  recent_decisions: AutopilotDecision[];
};

export type DriftItem = {
  kind: string;
  service: string;
  component: string;
  detail: string;
  fix: { action_id: string; target: string } | null;
  explains: string[];
};

export type DriftReport = {
  at: string;
  items: DriftItem[];
  scanned: string[];
  errors: string[];
};

export function fetchAutopilot(signal?: AbortSignal): Promise<AutopilotStatus> {
  return request<AutopilotStatus>("/api/autopilot", { signal });
}

export function fetchDrift(signal?: AbortSignal): Promise<DriftReport> {
  return request<DriftReport>("/api/remediation/drift", { signal });
}

/** The autopilot's record on an action row (args.autopilot), if it chose it. */
export type AutopilotChoice = {
  source: string;
  rationale: string;
  planner: string;
  attempt: number;
  planner_error?: string | null;
};

export function autopilotOf(args: unknown): AutopilotChoice | null {
  if (!args || typeof args !== "object") return null;
  const value = (args as Record<string, unknown>)["autopilot"];
  if (!value || typeof value !== "object") return null;
  const choice = value as Partial<AutopilotChoice>;
  if (typeof choice.rationale !== "string" || typeof choice.attempt !== "number") return null;
  return {
    source: String(choice.source ?? ""),
    rationale: choice.rationale,
    planner: String(choice.planner ?? "deterministic"),
    attempt: choice.attempt,
    planner_error: choice.planner_error ?? null,
  };
}
