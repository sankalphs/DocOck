import { describe, expect, it } from "vitest";

import {
  actionVariant,
  approvalVariant,
  canDecide,
  citedCodes,
  plannedCheck,
  riskVariant,
  ttlLabel,
  type ActionView,
} from "@/lib/api/approvals";

function action(args: Record<string, unknown>): ActionView {
  return { args } as unknown as ActionView;
}

describe("approval helpers", () => {
  it("formats the remaining time", () => {
    expect(ttlLabel(598.6)).toBe("9:58");
    expect(ttlLabel(5)).toBe("0:05");
    expect(ttlLabel(-3)).toBe("0:00");
  });

  it("allows a decision only while pending and unexpired", () => {
    expect(canDecide({ status: "PENDING" }, 12)).toBe(true);
    expect(canDecide({ status: "PENDING" }, 0)).toBe(false);
    expect(canDecide({ status: "APPROVED" }, 300)).toBe(false);
    expect(canDecide({ status: "WITHDRAWN" }, 300)).toBe(false);
  });

  it("colours risk and status", () => {
    expect(riskVariant("medium")).toBe("warning");
    expect(riskVariant("low")).toBe("success");
    expect(approvalVariant("REJECTED")).toBe("destructive");
    expect(approvalVariant("PENDING")).toBe("warning");
    expect(actionVariant("EXECUTED")).toBe("success");
    expect(actionVariant("ROLLED_BACK")).toBe("destructive");
  });

  it("reads the planner's stored check and citations", () => {
    const row = action({
      evidence_codes: ["E0001", "E0004"],
      planned_check: { ok: true, preconditions: [], steps: ["x"], rollback: {} },
    });
    expect(citedCodes(row)).toEqual(["E0001", "E0004"]);
    expect(plannedCheck(row)?.steps).toEqual(["x"]);
    expect(citedCodes(action({}))).toEqual([]);
    expect(plannedCheck(action({}))).toBeNull();
  });
});
