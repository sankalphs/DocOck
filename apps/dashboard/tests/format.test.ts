import { describe, expect, it } from "vitest";

import { formatClock, formatLatency, formatNumber, formatRelative, toneFor } from "@/lib/format";

describe("formatLatency", () => {
  it("keeps sub-second values in milliseconds", () => {
    expect(formatLatency(48.4)).toBe("48 ms");
    expect(formatLatency(0)).toBe("<1 ms");
  });

  it("switches to seconds above 1000 ms", () => {
    expect(formatLatency(1500)).toBe("1.50 s");
    expect(formatLatency(12_000)).toBe("12.0 s");
  });

  it("renders an em dash for missing values", () => {
    expect(formatLatency(null)).toBe("—");
    expect(formatLatency(undefined)).toBe("—");
  });
});

describe("formatRelative", () => {
  const now = Date.UTC(2026, 9, 24, 12, 0, 0);

  it("describes recent and older timestamps", () => {
    expect(formatRelative(now, now)).toBe("just now");
    expect(formatRelative(now - 12_000, now)).toBe("12s ago");
    expect(formatRelative(now - 5 * 60_000, now)).toBe("5m ago");
    expect(formatRelative(now - 3 * 3600_000, now)).toBe("3h ago");
    expect(formatRelative(now - 2 * 86_400_000, now)).toBe("2d ago");
  });

  it("treats a missing timestamp as never", () => {
    expect(formatRelative(null)).toBe("never");
  });
});

describe("formatNumber", () => {
  it("compacts large counts", () => {
    expect(formatNumber(206)).toBe("206");
    expect(formatNumber(12_400)).toBe("12.4k");
    expect(formatNumber(1_500_000)).toBe("1.5M");
  });
});

describe("toneFor", () => {
  it("maps known statuses and falls back to unknown", () => {
    expect(toneFor("ok").label).toBe("Healthy");
    expect(toneFor("unconfigured").label).toBe("Not configured");
    expect(toneFor("something-else").label).toBe("Unknown");
    expect(toneFor(null).label).toBe("Unknown");
  });
});

describe("formatClock", () => {
  it("renders a local HH:MM:SS clock and rejects garbage", () => {
    expect(formatClock("2026-10-24T05:30:00Z")).toMatch(/\d{2}:\d{2}:\d{2}/);
    expect(formatClock("not-a-date")).toBe("—");
  });
});
