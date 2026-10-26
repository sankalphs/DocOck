import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, fetchHealth, request } from "@/lib/api/client";
import type { HealthReport } from "@/lib/api/client";

function stubFetch(status: number, body: unknown) {
  const response = new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
  vi.stubGlobal("fetch", vi.fn(async () => response));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

const REPORT: HealthReport = {
  status: "ok",
  app: "DocOck",
  version: "0.1.0",
  environment: "local",
  checked_at: "2026-10-24T05:30:00Z",
  uptime_s: 12,
  dependencies: [],
};

describe("request", () => {
  it("returns the parsed body on success", async () => {
    stubFetch(200, { name: "DocOck", version: "0.1.0", environment: "local" });
    const body = await request<{ name: string }>("/api/version");
    expect(body.name).toBe("DocOck");
  });

  it("turns an error envelope into ApiClientError with the code and request id", async () => {
    stubFetch(404, {
      error: {
        code: "not_found",
        message: "Not Found",
        details: null,
        request_id: "abc123",
      },
    });

    await expect(request("/api/nope")).rejects.toMatchObject({
      name: "ApiClientError",
      status: 404,
      code: "not_found",
      requestId: "abc123",
    });
  });

  it("preserves the request id on the thrown instance", async () => {
    stubFetch(500, {
      error: { code: "internal_error", message: "boom", details: null, request_id: "rid" },
    });
    const error = await request("/api/boom").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiClientError);
    expect((error as ApiClientError).requestId).toBe("rid");
  });
});

describe("fetchHealth", () => {
  it("returns the report even when the endpoint answers 503", async () => {
    stubFetch(503, { ...REPORT, status: "down" });
    const report = await fetchHealth();
    expect(report.status).toBe("down");
  });

  it("throws when the body is not a health report", async () => {
    stubFetch(502, { error: { code: "bad_gateway", message: "nope" } });
    await expect(fetchHealth()).rejects.toBeInstanceOf(ApiClientError);
  });
});
