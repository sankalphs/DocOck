import { describe, expect, it } from "vitest";

import {
  beforeAfter,
  cleanProgress,
  percent,
  reportUrl,
  verdictVariant,
  type ReportVerification,
} from "@/lib/api/reports";

const verification: ReportVerification = {
  id: 1,
  verdict: "PASSED",
  observed_for_s: 61,
  created_at: null,
  criteria: { clean_cycles: 4, required_clean_cycles: 4, focus: ["payment"] },
  before: { metrics: { payment: { error_rate: 1 }, checkout: { error_rate: 0.4 } } },
  after: { metrics: { payment: { error_rate: 0 } } },
};

describe("report helpers", () => {
  it("colours verdicts", () => {
    expect(verdictVariant("PASSED")).toBe("success");
    expect(verdictVariant("FAILED")).toBe("destructive");
    expect(verdictVariant("INCONCLUSIVE")).toBe("destructive");
    expect(verdictVariant(null)).toBe("outline");
  });

  it("pairs before and after per service, sorted", () => {
    const rows = beforeAfter(verification);
    expect(rows.map((row) => row.service)).toEqual(["checkout", "payment"]);
    expect(rows[1]).toEqual({
      service: "payment",
      before: { error_rate: 1 },
      after: { error_rate: 0 },
    });
    expect(rows[0].after).toEqual({});
  });

  it("formats rates and progress", () => {
    expect(percent(0.256)).toBe("25.6%");
    expect(percent(null)).toBe("—");
    expect(cleanProgress(verification)).toBe("4/4 clean cycles");
  });

  it("links straight to the API exports", () => {
    expect(reportUrl("INC-00001", "md", true)).toMatch(
      /\/api\/incidents\/INC-00001\/report\.md\?download=true$/,
    );
    expect(reportUrl("INC-00001", "json")).toMatch(/\/api\/incidents\/INC-00001\/report$/);
  });
});
