import { describe, expect, it } from "vitest";

import {
  affectsAttention,
  attentionTitle,
  navBadge,
  stripAttention,
  type Attention,
} from "@/lib/api/attention";
import { formatDuration, formatWhen } from "@/lib/format";

const quiet: Attention = {
  open_incidents: 0,
  pending_approvals: 0,
  active_faults: 0,
  worst_severity: null,
  latest_incident: null,
  generated_at: "2026-11-16T10:00:00Z",
};

describe("formatDuration", () => {
  it("keeps the two largest units", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(185)).toBe("3m 05s");
    expect(formatDuration(2 * 3600 + 14 * 60 + 9)).toBe("2h 14m");
    expect(formatDuration(3 * 86400 + 4 * 3600)).toBe("3d 4h");
  });

  it("never shows negatives and dashes missing values", () => {
    expect(formatDuration(-5)).toBe("0s");
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
  });
});

describe("formatWhen", () => {
  const now = new Date(2026, 10, 16, 12, 0, 0);

  it("shows only the time for today", () => {
    const today = new Date(2026, 10, 16, 9, 5, 0).toISOString();
    expect(formatWhen(today, now)).not.toMatch(/Nov/);
  });

  it("adds the date for another day", () => {
    const yesterday = new Date(2026, 10, 15, 23, 50, 0).toISOString();
    expect(formatWhen(yesterday, now)).toMatch(/15/);
    expect(formatWhen(yesterday, now)).not.toBe(formatWhen(yesterday, new Date(2026, 10, 15, 23, 55)));
  });

  it("dashes bad input", () => {
    expect(formatWhen(null)).toBe("—");
    expect(formatWhen("nope")).toBe("—");
  });
});

describe("attention", () => {
  it("badges only what needs someone", () => {
    expect(navBadge("/approvals", quiet)).toBeNull();
    expect(navBadge("/approvals", { ...quiet, pending_approvals: 2 })).toEqual({
      count: 2,
      tone: "destructive",
    });
    expect(navBadge("/incidents", { ...quiet, open_incidents: 1, worst_severity: "sev3" })?.tone).toBe(
      "warning",
    );
    expect(navBadge("/incidents", { ...quiet, open_incidents: 1, worst_severity: "sev1" })?.tone).toBe(
      "destructive",
    );
    expect(navBadge("/faults", { ...quiet, active_faults: 1 })?.count).toBe(1);
    expect(navBadge("/memory", { ...quiet, active_faults: 1 })).toBeNull();
    expect(navBadge("/approvals", null)).toBeNull();
  });

  it("prefixes the tab title, approvals first, and round-trips", () => {
    const base = "Incidents · DocOck";
    expect(attentionTitle(base, quiet)).toBe(base);
    const one = attentionTitle(base, { ...quiet, pending_approvals: 1, open_incidents: 3 });
    expect(one).toBe("(1) approval pending · Incidents · DocOck");
    expect(stripAttention(one)).toBe(base);
    const many = attentionTitle(base, { ...quiet, open_incidents: 3 });
    expect(many).toBe("(3) open incidents · Incidents · DocOck");
    expect(stripAttention(attentionTitle(stripAttention(many), quiet))).toBe(base);
  });

  it("refreshes on the events that move the counts", () => {
    expect(affectsAttention("approval.requested")).toBe(true);
    expect(affectsAttention("incident.note")).toBe(true);
    expect(affectsAttention("fault.reverted")).toBe(true);
    expect(affectsAttention("agent.step")).toBe(false);
  });
});
