import { describe, expect, it } from "vitest";

import { autopilotOf } from "@/lib/api/autopilot";
import { EVENT_TYPES } from "@/lib/events";

describe("autopilotOf", () => {
  it("reads the autopilot record from an action's args", () => {
    const choice = autopilotOf({
      autopilot: {
        source: "drift",
        rationale: "valkey-cart is exited",
        planner: "deterministic",
        attempt: 2,
      },
    });
    expect(choice).toEqual({
      source: "drift",
      rationale: "valkey-cart is exited",
      planner: "deterministic",
      attempt: 2,
      planner_error: null,
    });
  });

  it("ignores actions the autopilot did not choose", () => {
    expect(autopilotOf({ override: true })).toBeNull();
    expect(autopilotOf(null)).toBeNull();
    expect(autopilotOf({ autopilot: { rationale: 3 } })).toBeNull();
  });

  it("streams autopilot decisions on the live feed", () => {
    expect(EVENT_TYPES).toContain("autopilot.decision");
  });
});
