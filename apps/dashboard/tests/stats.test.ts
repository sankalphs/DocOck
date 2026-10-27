import { describe, expect, it } from "vitest";

import { budgetShare, spread, usd, type Distribution, type Stats } from "@/lib/api/stats";

const empty: Distribution = { count: 0, p50: null, p95: null, max: null, mean: null };

function stats(budgetUsed: number | null): Stats {
  return {
    generated_at: "2026-10-26T08:00:00Z",
    since: "2026-10-19T08:00:00Z",
    window_h: 168,
    agent: {
      runs: 0,
      by_status: {},
      latency_ms: empty,
      tokens_in: 0,
      tokens_out: 0,
      cost_usd: 0,
      spent_today_usd: 0,
      daily_budget_usd: 2,
      budget_used: budgetUsed,
      models: [],
    },
    incidents: { opened: 0, by_status: {}, open: 0, time_to_close_s: empty },
    remediation: { actions: 0, by_status: {}, approvals: {}, verdicts: {}, verification_s: empty },
    rate_limits: { enabled: true, rejected: 0, rules: [] },
  };
}

describe("stats helpers", () => {
  it("formats a distribution as p50 / p95", () => {
    expect(spread(empty, "s")).toBe("—");
    expect(spread({ count: 3, p50: 9546, p95: 12000, max: 12000, mean: 10000 }, "s", 1000)).toBe(
      "10 s / 12 s",
    );
  });

  it("clamps the budget share and handles no cap", () => {
    expect(budgetShare(stats(null))).toBeNull();
    expect(budgetShare(stats(0.25))).toBe(0.25);
    expect(budgetShare(stats(1.7))).toBe(1);
  });

  it("shows sub-cent costs with four decimals", () => {
    expect(usd(0)).toBe("$0.00");
    expect(usd(0.0042)).toBe("$0.0042");
    expect(usd(1.5)).toBe("$1.50");
  });
});
