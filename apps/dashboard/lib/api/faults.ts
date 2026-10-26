import { request } from "./client";
import type { components } from "./schema";

export type FaultStatus =
  | "PROPAGATING"
  | "ACTIVE"
  | "REVERTING"
  | "REVERTED"
  | "EXPIRED"
  | "FAILED";

export type FaultCategory = "application" | "infrastructure";
export type FaultMechanism =
  | "flagd"
  | "container_stop"
  | "container_pause"
  | "network_partition"
  | "resource_limit"
  | "load_spike";

export type ParamSpec = {
  name: string;
  label: string;
  kind: "variant" | "number" | "enum";
  required: boolean;
  default: string | number | boolean | null;
  choices: string[];
  minimum: number | null;
  maximum: number | null;
  unit: string | null;
  description: string;
};

export type SymptomSpec = {
  probe: string;
  service: string;
  comparator: string;
  threshold: number | null;
  baseline_multiplier: number | null;
  detail: string;
};

export type FaultDefinition = {
  id: string;
  label: string;
  description: string;
  category: FaultCategory;
  mechanism: FaultMechanism;
  target: string;
  target_mode: "fixed" | "choice";
  targets: string[];
  flag: string | null;
  variants: string[];
  params: ParamSpec[];
  default_ttl_s: number;
  risk: "low" | "medium";
  symptom: SymptomSpec;
  notes: string;
};

export type FaultCatalog = {
  version: number;
  defaults: { ttl_s: number; max_ttl_s: number; actor: string };
  limits: Record<string, number>;
  targets: string[];
  faults: FaultDefinition[];
  json_schema: Record<string, unknown>;
};

export type FaultView = {
  id: number;
  fault_id: string;
  label: string;
  category: FaultCategory;
  mechanism: FaultMechanism;
  target: string;
  params: Record<string, unknown>;
  status: FaultStatus;
  actor: string;
  risk: string;
  injected_at: string;
  expires_at: string | null;
  reset_at: string | null;
  ttl_remaining_s: number | null;
  symptom: SymptomSpec;
  observed: { at?: string; value?: number | null; detail?: string; baseline?: number | null } | null;
  injection_ref: Record<string, unknown>;
};

export type FaultList = {
  items: FaultView[];
  total: number;
  open: number;
  generated_at: string;
};

export type InjectionRequest = {
  fault_id: string;
  target?: string;
  params?: Record<string, unknown>;
  ttl_s?: number;
  dry_run?: boolean;
  actor?: string;
};

export type InjectionPlan = {
  fault_id?: string;
  label?: string;
  target?: string;
  params?: Record<string, unknown>;
  ttl_s?: number;
  mechanism?: string;
  safety?: {
    project?: string;
    container?: string;
    network?: string | null;
    aliases?: string[];
    cpu_limit_cores?: number | null;
    memory_limit_mb?: number | null;
  };
  pre_image?: Record<string, unknown>;
  baseline?: number | null;
  symptom?: Record<string, unknown>;
};

export type InjectionResult = {
  dry_run: boolean;
  fault: FaultView | null;
  plan: InjectionPlan | null;
};

export type ResetReport = {
  reverted: FaultView[];
  failed: { id: number | null; error: string; message: string }[];
  flag_drift: { flag: string; before?: string; after?: string; error?: string }[];
  generated_at: string;
};

export type AuditEntry = {
  at: string;
  actor: string;
  action: string;
  fault_row_id: number | null;
  fault_id: string | null;
  target: string | null;
  outcome: string;
  detail: string;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
};

export async function fetchFaultCatalog(signal?: AbortSignal): Promise<FaultCatalog> {
  const raw = await request<components["schemas"]["FaultCatalog"]>("/api/faults/catalog", {
    signal,
  });
  return {
    version: raw.version,
    defaults: {
      ttl_s: Number(raw.defaults.ttl_s ?? 900),
      max_ttl_s: Number(raw.defaults.max_ttl_s ?? 3600),
      actor: String(raw.defaults.actor ?? "operator"),
    },
    limits: (raw.limits ?? {}) as Record<string, number>,
    targets: raw.targets ?? [],
    faults: (raw.faults ?? []).map((fault) => ({
      id: fault.id,
      label: fault.label,
      description: fault.description,
      category: fault.category,
      mechanism: fault.mechanism,
      target: fault.target,
      target_mode: fault.target_mode ?? "fixed",
      targets: fault.targets ?? [],
      flag: fault.flag ?? null,
      variants: fault.variants ?? [],
      params: (fault.params ?? []).map((param) => ({
        name: param.name,
        label: param.label,
        kind: param.kind,
        required: param.required ?? true,
        default: (param.default ?? null) as string | number | boolean | null,
        choices: param.choices ?? [],
        minimum: param.minimum ?? null,
        maximum: param.maximum ?? null,
        unit: param.unit ?? null,
        description: param.description ?? "",
      })),
      default_ttl_s: fault.default_ttl_s ?? 900,
      risk: (fault.risk ?? "low") as "low" | "medium",
      symptom: {
        probe: fault.symptom.probe,
        service: fault.symptom.service,
        comparator: fault.symptom.comparator ?? ">",
        threshold: fault.symptom.threshold ?? null,
        baseline_multiplier: fault.symptom.baseline_multiplier ?? null,
        detail: fault.symptom.detail ?? "",
      },
      notes: fault.notes ?? "",
    })),
    json_schema: (raw.json_schema ?? {}) as Record<string, unknown>,
  };
}

function mapFault(raw: components["schemas"]["FaultView"]): FaultView {
  const observed = raw.observed ?? null;
  return {
    id: raw.id,
    fault_id: raw.fault_id,
    label: raw.label,
    category: raw.category,
    mechanism: raw.mechanism,
    target: raw.target,
    params: raw.params ?? {},
    status: raw.status,
    actor: raw.actor,
    risk: raw.risk,
    injected_at: raw.injected_at,
    expires_at: raw.expires_at ?? null,
    reset_at: raw.reset_at ?? null,
    ttl_remaining_s: raw.ttl_remaining_s ?? null,
    symptom: {
      probe: raw.symptom.probe,
      service: raw.symptom.service,
      comparator: raw.symptom.comparator ?? ">",
      threshold: raw.symptom.threshold ?? null,
      baseline_multiplier: raw.symptom.baseline_multiplier ?? null,
      detail: raw.symptom.detail ?? "",
    },
    observed: observed
      ? {
          at: typeof observed.at === "string" ? observed.at : undefined,
          value: typeof observed.value === "number" ? observed.value : null,
          detail: typeof observed.detail === "string" ? observed.detail : undefined,
          baseline: typeof observed.baseline === "number" ? observed.baseline : null,
        }
      : null,
    injection_ref: raw.injection_ref ?? {},
  };
}

export async function fetchFaults(
  options: { status?: string; limit?: number } = {},
  signal?: AbortSignal,
): Promise<FaultList> {
  const params = new URLSearchParams({
    status: options.status ?? "all",
    limit: String(options.limit ?? 50),
  });
  const raw = await request<components["schemas"]["FaultList"]>(`/api/faults?${params}`, {
    signal,
  });
  return {
    items: (raw.items ?? []).map(mapFault),
    total: raw.total,
    open: raw.open,
    generated_at: raw.generated_at,
  };
}

export async function injectFault(body: InjectionRequest): Promise<InjectionResult> {
  const raw = await request<components["schemas"]["InjectionResult"]>("/api/faults", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return {
    dry_run: raw.dry_run,
    fault: raw.fault ? mapFault(raw.fault) : null,
    plan: (raw.plan ?? null) as InjectionPlan | null,
  };
}

export async function revertFault(id: number, actor = "dashboard"): Promise<FaultView> {
  const raw = await request<components["schemas"]["FaultView"]>(
    `/api/faults/${id}?actor=${encodeURIComponent(actor)}`,
    { method: "DELETE" },
  );
  return mapFault(raw);
}

export async function resetAllFaults(actor = "dashboard"): Promise<ResetReport> {
  const raw = await request<components["schemas"]["ResetReport"]>(
    `/api/faults/reset-all?actor=${encodeURIComponent(actor)}`,
    { method: "POST" },
  );
  return {
    reverted: (raw.reverted ?? []).map(mapFault),
    failed: (raw.failed ?? []).map((item) => ({
      id: typeof item.id === "number" ? item.id : null,
      error: String(item.error ?? "error"),
      message: String(item.message ?? ""),
    })),
    flag_drift: (raw.flag_drift ?? []).map((item) => ({
      flag: String(item.flag ?? ""),
      before: item.before === undefined ? undefined : String(item.before),
      after: item.after === undefined ? undefined : String(item.after),
      error: item.error === undefined ? undefined : String(item.error),
    })),
    generated_at: raw.generated_at,
  };
}

export async function fetchFaultAudit(limit = 25): Promise<AuditEntry[]> {
  const raw = await request<components["schemas"]["AuditEntry"][]>(
    `/api/faults/audit?limit=${limit}`,
  );
  return raw.map((entry) => ({
    at: entry.at ?? new Date().toISOString(),
    actor: entry.actor,
    action: entry.action,
    fault_row_id: entry.fault_row_id ?? null,
    fault_id: entry.fault_id ?? null,
    target: entry.target ?? null,
    outcome: entry.outcome,
    detail: entry.detail ?? "",
    before: entry.before ?? null,
    after: entry.after ?? null,
  }));
}

/** One-line human description of a fault's parameters (variant, cpu, users…). */
export function describeParams(fault: Pick<FaultView, "params">): string {
  return Object.entries(fault.params)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join(" · ");
}
