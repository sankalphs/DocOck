import { describe, expect, it } from "vitest";

import { eventBadge, severityBadge, statusBadge } from "@/components/incidents/badges";

describe("incident badge mapping", () => {
  it("maps terminal states", () => {
    expect(statusBadge("RESOLVED")).toBe("success");
    expect(statusBadge("ESCALATED")).toBe("destructive");
    expect(statusBadge("REJECTED")).toBe("destructive");
  });

  it("maps active states to warning", () => {
    expect(statusBadge("OPEN")).toBe("warning");
    expect(statusBadge("AWAITING_APPROVAL")).toBe("warning");
    expect(statusBadge("VERIFYING")).toBe("warning");
  });

  it("maps severity", () => {
    expect(severityBadge("sev1")).toBe("destructive");
    expect(severityBadge("sev2")).toBe("warning");
    expect(severityBadge("sev4")).toBe("secondary");
  });

  it("maps timeline events", () => {
    expect(eventBadge("opened")).toBe("destructive");
    expect(eventBadge("signal")).toBe("warning");
    expect(eventBadge("recovered")).toBe("success");
  });
});
