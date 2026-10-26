import { API_BASE_URL, request } from "./client";
import type { components } from "./schema";

export type ReportList = components["schemas"]["ReportList"];
export type ReportSummary = components["schemas"]["ReportSummary"];
export type VerificationList = components["schemas"]["VerificationList"];

/** One criterion the verifier checked on the last detector cycle. */
export type Criterion = { id: string; label: string; ok: boolean; detail: string };

export type MetricsByService = Record<
  string,
  { error_rate?: number | null; p95_latency?: number | null; throughput?: number | null }
>;

export type ReportVerification = {
  id: number;
  verdict: string;
  observed_for_s: number;
  created_at: string | null;
  criteria: {
    final?: Criterion[];
    focus?: string[];
    clean_cycles?: number;
    required_clean_cycles?: number;
    timeout_s?: number;
    rollback?: Record<string, unknown>;
  };
  before: { at?: string; metrics?: MetricsByService; breaches?: Breach[] };
  after: {
    at?: string;
    metrics?: MetricsByService;
    breaches?: Breach[];
    samples?: { at: string; clean: number; failing?: string[]; stale?: boolean }[];
  };
};

export type Breach = { service: string; signal: string; detail: string };

export type ReportAction = {
  action: {
    id: number;
    action_id: string;
    target: string;
    status: string;
    risk: string | null;
    approval_mode: string | null;
    requested_by: string | null;
    started_at: string | null;
    finished_at: string | null;
    error: string | null;
    result: { steps?: Record<string, unknown>[] } | null;
    rollback: Record<string, unknown> | null;
  };
  approval: {
    id: number;
    status: string;
    decided_by: string | null;
    decided_at: string | null;
    note: string | null;
    token_issued: boolean;
    token_used: boolean;
  } | null;
  verification: ReportVerification | null;
};

export type EvidenceRow = {
  code: string;
  type: string;
  source: string;
  service: string | null;
  at: string | null;
  summary: string;
  data?: Record<string, unknown>;
};

/** The JSON report (`docock.report/v1`), as built by app/reports/builder.py. */
export type IncidentReport = {
  schema: string;
  generated_at: string;
  incident: {
    ref: string;
    title: string;
    severity: string;
    status: string;
    primary_service: string | null;
    opened_at: string | null;
    closed_at: string | null;
    duration_s: number | null;
  };
  outcome: {
    status: string;
    action: string | null;
    verdict: string | null;
    observed_for_s: number | null;
    closed_by: string | null;
  };
  timeline: { seq: number; at: string | null; kind: string; detail: string }[];
  suspects: { rank: number; service: string; score: number }[];
  diagnosis: {
    root_cause_service: string;
    fault_class: string;
    confidence: number;
    summary: string;
    evidence_codes: string[];
    recommended_action: { action_id?: string; target?: string };
    valid: boolean;
  } | null;
  agent_runs: {
    id: number;
    status: string;
    provider: string | null;
    model: string | null;
    cost_usd: number;
    latency_ms: number | null;
  }[];
  evidence: { cited: EvidenceRow[]; missing_citations: string[]; index: EvidenceRow[] };
  faults: { id: number; fault_id: string; target: string; status: string }[];
  remediation: ReportAction[];
  audit: { at: string; actor: string; event: string; outcome: string; detail: string }[];
};

export function fetchReports(limit = 40, signal?: AbortSignal): Promise<ReportList> {
  return request<ReportList>(`/api/reports?limit=${limit}`, { signal });
}

export function fetchReport(ref: string, signal?: AbortSignal): Promise<IncidentReport> {
  return request<IncidentReport>(`/api/incidents/${encodeURIComponent(ref)}/report`, { signal });
}

export function fetchVerifications(ref: string, signal?: AbortSignal): Promise<VerificationList> {
  return request<VerificationList>(`/api/incidents/${encodeURIComponent(ref)}/verifications`, {
    signal,
  });
}

/** Direct links: the browser downloads from the API, nothing is proxied. */
export function reportUrl(ref: string, format: "json" | "md", download = false): string {
  const path =
    format === "md"
      ? `/api/incidents/${encodeURIComponent(ref)}/report.md${download ? "?download=true" : ""}`
      : `/api/incidents/${encodeURIComponent(ref)}/report`;
  return `${API_BASE_URL}${path}`;
}

type BadgeVariant = "default" | "secondary" | "outline" | "success" | "warning" | "destructive";

export function verdictVariant(verdict: string | null | undefined): BadgeVariant {
  if (verdict === "PASSED") return "success";
  if (verdict === "FAILED" || verdict === "INCONCLUSIVE") return "destructive";
  return "outline";
}

export function percent(value: number | null | undefined): string {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "—";
}

/** Rows for the before → after table: one per focus service. */
export function beforeAfter(
  verification: ReportVerification,
): { service: string; before: MetricsByService[string]; after: MetricsByService[string] }[] {
  const before = verification.before.metrics ?? {};
  const after = verification.after.metrics ?? {};
  return [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .sort()
    .map((service) => ({ service, before: before[service] ?? {}, after: after[service] ?? {} }));
}

/** Clean-cycle progress for a running or finished verification. */
export function cleanProgress(verification: ReportVerification): string {
  const c = verification.criteria;
  return `${c.clean_cycles ?? 0}/${c.required_clean_cycles ?? "?"} clean cycles`;
}
