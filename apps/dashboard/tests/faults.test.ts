import { describe, expect, it } from "vitest";

import {
  countdown,
  formatParams,
  openStatus,
  statusLabel,
  statusVariant,
} from "@/components/faults/fault-status";

const START = "2026-10-24T10:00:00.000Z";
const EXPIRES = "2026-10-24T10:15:00.000Z";

describe("fault countdown", () => {
  it("shows the full TTL at injection time", () => {
    const result = countdown(START, EXPIRES, new Date(START).getTime());
    expect(result.label).toBe("15:00");
    expect(result.fraction).toBe(1);
    expect(result.expired).toBe(false);
  });

  it("counts down and clamps at zero", () => {
    const halfway = new Date("2026-10-24T10:07:30.000Z").getTime();
    expect(countdown(START, EXPIRES, halfway).label).toBe("7:30");
    const after = new Date("2026-10-24T10:16:00.000Z").getTime();
    const result = countdown(START, EXPIRES, after);
    expect(result.label).toBe("expired");
    expect(result.expired).toBe(true);
    expect(result.fraction).toBe(0);
  });

  it("handles a fault without an expiry", () => {
    expect(countdown(START, null, Date.now())).toEqual({
      label: "no expiry",
      fraction: 0,
      expired: false,
    });
  });
});

describe("fault status", () => {
  it("maps statuses to badge tones and labels", () => {
    expect(statusVariant("ACTIVE")).toBe("destructive");
    expect(statusVariant("PROPAGATING")).toBe("warning");
    expect(statusVariant("REVERTED")).toBe("success");
    expect(statusLabel("PROPAGATING")).toBe("propagating");
    expect(statusLabel("FAILED")).toBe("failed");
  });

  it("treats propagating and active as open", () => {
    expect(openStatus("PROPAGATING")).toBe(true);
    expect(openStatus("REVERTING")).toBe(true);
    expect(openStatus("EXPIRED")).toBe(false);
    expect(openStatus("REVERTED")).toBe(false);
  });

  it("renders params compactly", () => {
    expect(formatParams({ variant: "100%" })).toBe("variant 100%");
    expect(formatParams({ cpus: 0.5 })).toBe("cpus 0.5");
    expect(formatParams({})).toBe("no parameters");
  });
});
