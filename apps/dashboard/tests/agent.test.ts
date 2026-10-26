import { describe, expect, it } from "vitest";

import {
  citations,
  isRunning,
  stepCodes,
  stepTitle,
  stepTone,
  toolCallCount,
  type AgentStep,
  type DiagnosisView,
} from "@/lib/api/agent";
import type { EvidenceRow } from "@/lib/api/incidents";

function step(kind: string, output: Record<string, unknown>, tool: string | null = null): AgentStep {
  return { seq: 1, kind, tool, input: null, output, latency_ms: 5, at: "2026-10-26T00:00:00Z" };
}

const evidence = [
  { code: "E0001", type: "onset", source: "jaeger", service: "payment" },
  { code: "E0002", type: "flag_state", source: "faults", service: "payment" },
] as EvidenceRow[];

const diagnosis = {
  evidence_codes: ["E0001", "E0002", "E0099"],
} as DiagnosisView;

describe("agent steps", () => {
  it("titles tool steps with the evidence code they recorded", () => {
    expect(stepTitle(step("tool", { evidence_code: "E0020" }, "get_traces"))).toBe(
      "get_traces → E0020",
    );
    expect(stepTitle(step("tool", { error: "boom" }, "get_traces"))).toBe("get_traces failed");
    expect(
      stepTitle(step("llm", { tool_calls: [{ name: "get_metrics" }, { name: "get_logs" }] })),
    ).toBe("model turn: calls get_metrics, get_logs");
    expect(stepTitle(step("gate", { passed: false, confidence: 0.5, threshold: 0.7 }))).toBe(
      "confidence gate: escalate (0.5 < 0.7)",
    );
  });

  it("colours failures and gate outcomes", () => {
    expect(stepTone(step("validation", { valid: false }))).toBe("destructive");
    expect(stepTone(step("validation", { valid: true }))).toBe("success");
    expect(stepTone(step("gate", { passed: false }))).toBe("warning");
    expect(stepTone(step("tool", { error: "x" }, "get_logs"))).toBe("destructive");
  });

  it("collects codes from tool output and validation", () => {
    expect(stepCodes(step("tool", { evidence_code: "E0003", codes: ["E0003", "E0001"] }))).toEqual([
      "E0001",
      "E0003",
    ]);
    expect(stepCodes(step("validation", { cited: ["E0002"] }))).toEqual(["E0002"]);
  });

  it("counts only tool steps against the budget", () => {
    expect(toolCallCount([step("tool", {}), step("llm", {}), step("tool", {})])).toBe(2);
    expect(isRunning({ status: "RUNNING" })).toBe(true);
    expect(isRunning(null)).toBe(false);
  });
});

describe("citations", () => {
  it("links every cited code to its evidence row and flags unknown ones", () => {
    const cited = citations(diagnosis, evidence);
    expect(cited.map((item) => [item.code, item.row?.type ?? null])).toEqual([
      ["E0001", "onset"],
      ["E0002", "flag_state"],
      ["E0099", null],
    ]);
    expect(citations(null, evidence)).toEqual([]);
  });
});
