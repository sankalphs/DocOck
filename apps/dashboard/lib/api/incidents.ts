import { request } from "./client";
import type { components } from "./schema";

export type IncidentStatus =
  | "OPEN"
  | "INVESTIGATING"
  | "DIAGNOSED"
  | "AWAITING_APPROVAL"
  | "REMEDIATING"
  | "VERIFYING"
  | "RESOLVED"
  | "REJECTED"
  | "ESCALATED";

export type IncidentSignal = {
  service: string;
  signal: string;
  signature: string;
  value: number | null;
  baseline: number | null;
  threshold: number | null;
  detail: string;
};

export type IncidentEvent = {
  seq: number;
  kind: string;
  payload: Record<string, unknown>;
  at: string;
};

export type IncidentSummary = {
  id: number;
  public_id: string;
  status: IncidentStatus;
  severity: string;
  title: string;
  primary_service: string | null;
  signature: string | null;
  opened_at: string;
  closed_at: string | null;
  duration_s: number | null;
  services: string[];
  signals: IncidentSignal[];
  event_count: number;
  evidence_count: number;
  suspect_count: number;
};

export type EvidenceRow = {
  id: number;
  code: string;
  type: string;
  source: string;
  service: string | null;
  at: string;
  data: Record<string, unknown>;
  hash: string;
};

export type SuspectRow = {
  service: string;
  score: number;
  rank: number;
  signals: Record<string, unknown>;
};

export type FaultContext = {
  id: number;
  fault_id: string;
  target: string;
  params: Record<string, unknown>;
  status: string;
  injected_at: string;
  reset_at: string | null;
};

export type IncidentDetail = IncidentSummary & {
  trigger: Record<string, unknown>;
  events: IncidentEvent[];
  evidence: EvidenceRow[];
  suspects: SuspectRow[];
  faults: FaultContext[];
};

export type IncidentList = {
  items: IncidentSummary[];
  total: number;
  open: number;
  generated_at: string;
};

function mapSignal(raw: {
  service: string;
  signal: string;
  signature?: string;
  value?: number | null;
  baseline?: number | null;
  threshold?: number | null;
  detail?: string;
}): IncidentSignal {
  return {
    service: raw.service,
    signal: raw.signal,
    signature: raw.signature ?? `${raw.signal}:${raw.service}`,
    value: raw.value ?? null,
    baseline: raw.baseline ?? null,
    threshold: raw.threshold ?? null,
    detail: raw.detail ?? "",
  };
}

function mapSummary(raw: components["schemas"]["IncidentSummary"]): IncidentSummary {
  return {
    id: raw.id,
    public_id: raw.public_id,
    status: raw.status,
    severity: raw.severity,
    title: raw.title,
    primary_service: raw.primary_service ?? null,
    signature: raw.signature ?? null,
    opened_at: raw.opened_at,
    closed_at: raw.closed_at ?? null,
    duration_s: raw.duration_s ?? null,
    services: raw.services ?? [],
    signals: (raw.signals ?? []).map((item) =>
      mapSignal(
        item as {
          service: string;
          signal: string;
          signature?: string;
          value?: number | null;
          baseline?: number | null;
          threshold?: number | null;
          detail?: string;
        },
      ),
    ),
    event_count: raw.event_count ?? 0,
    evidence_count: raw.evidence_count ?? 0,
    suspect_count: raw.suspect_count ?? 0,
  };
}

export async function fetchIncidents(
  options: { status?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<IncidentList> {
  const params = new URLSearchParams({
    status: options.status ?? "all",
    limit: String(options.limit ?? 50),
  });
  const raw = await request<components["schemas"]["IncidentList"]>(
    `/api/incidents?${params}`,
    { signal },
  );
  return {
    items: (raw.items ?? []).map(mapSummary),
    total: raw.total,
    open: raw.open,
    generated_at: raw.generated_at,
  };
}

export async function fetchIncident(
  ref: string,
  signal?: AbortSignal,
): Promise<IncidentDetail> {
  const raw = await request<components["schemas"]["IncidentDetail"]>(
    `/api/incidents/${encodeURIComponent(ref)}`,
    { signal },
  );
  return {
    ...mapSummary(raw),
    trigger: raw.trigger ?? {},
    events: (raw.events ?? []).map((event) => ({
      seq: event.seq,
      kind: event.kind,
      payload: event.payload ?? {},
      at: event.at,
    })),
    evidence: (raw.evidence ?? []).map((row) => ({
      id: row.id,
      code: row.code,
      type: row.type,
      source: row.source,
      service: row.service ?? null,
      at: row.at,
      data: row.data ?? {},
      hash: row.hash,
    })),
    suspects: (raw.suspects ?? []).map((row) => ({
      service: row.service,
      score: row.score,
      rank: row.rank,
      signals: row.signals ?? {},
    })),
    faults: (raw.faults ?? []).map((row) => ({
      id: row.id,
      fault_id: row.fault_id,
      target: row.target,
      params: row.params ?? {},
      status: row.status,
      injected_at: row.injected_at,
      reset_at: row.reset_at ?? null,
    })),
  };
}

export async function closeIncident(
  ref: string,
  actor = "dashboard",
  note = "",
): Promise<IncidentDetail> {
  const params = new URLSearchParams({ actor, note });
  const raw = await request<components["schemas"]["IncidentDetail"]>(
    `/api/incidents/${encodeURIComponent(ref)}/close?${params}`,
    { method: "POST" },
  );
  return {
    ...mapSummary(raw),
    trigger: raw.trigger ?? {},
    events: [],
    evidence: [],
    suspects: [],
    faults: [],
  };
}
