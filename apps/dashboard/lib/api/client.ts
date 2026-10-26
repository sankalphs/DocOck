import { API_BASE_URL } from "./base-url";
import type { components, paths } from "./schema";

export type HealthReport = components["schemas"]["HealthReport"];
export type DependencyProbe = components["schemas"]["DependencyProbe"];
export type DependencyState = DependencyProbe["status"];

/** The error envelope every non-2xx response uses (see app/errors.py). */
export type ErrorEnvelope = {
  error: {
    code: string;
    message: string;
    details: unknown;
    request_id: string | null;
  };
};

export class ApiClientError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;

  constructor(status: number, code: string, message: string, requestId: string | null) {
    super(message);
    this.name = "ApiClientError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export type ApiPaths = paths;

function isErrorEnvelope(value: unknown): value is ErrorEnvelope {
  if (typeof value !== "object" || value === null) return false;
  const error = (value as { error?: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error && "message" in error;
}

export async function request<T>(
  path: string,
  init: RequestInit & { baseUrl?: string } = {},
): Promise<T> {
  const { baseUrl, ...rest } = init;
  const response = await fetch(`${baseUrl ?? API_BASE_URL}${path}`, {
    ...rest,
    cache: "no-store",
    headers: { Accept: "application/json", ...(rest.headers ?? {}) },
  });

  const body: unknown = await response.json().catch(() => null);

  if (!response.ok) {
    if (isErrorEnvelope(body)) {
      throw new ApiClientError(
        response.status,
        body.error.code,
        body.error.message,
        body.error.request_id,
      );
    }
    throw new ApiClientError(response.status, "http_error", `HTTP ${response.status}`, null);
  }

  return body as T;
}

/**
 * `/api/health` answers 200 when healthy and 503 when a critical dependency is
 * down — both carry the full report, so this bypasses the error path instead of
 * turning "degraded" into "nothing to show".
 */
export async function fetchHealth(signal?: AbortSignal): Promise<HealthReport> {
  const response = await fetch(`${API_BASE_URL}/api/health`, {
    signal,
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const body: unknown = await response.json().catch(() => null);
  if (typeof body === "object" && body !== null && "dependencies" in body) {
    return body as HealthReport;
  }
  throw new ApiClientError(
    response.status,
    "health_unavailable",
    `Health probe returned HTTP ${response.status}`,
    null,
  );
}

export { API_BASE_URL };
