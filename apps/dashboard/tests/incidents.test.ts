import { describe, expect, it } from "vitest";

import { eventBadge, severityBadge, statusBadge } from "@/components/incidents/badges";
import { suspectSignals } from "@/lib/api/incidents";

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

describe("suspectSignals", () => {
  it("orders the scorer's signals by contribution and tolerates junk", () => {
    const rows = suspectSignals({
      latency_anomaly: { value: 0.3, weight: 0.2, contribution: 0.06, detail: "p95 3x" },
      earliest_onset: { value: 1, weight: 0.4, contribution: 0.4, detail: "earliest" },
      container_unhealthy: { value: 0, weight: 0.2, contribution: 0, detail: "healthy" },
      broken: "not an object",
    });
    expect(rows.map((row) => row.name)).toEqual([
      "earliest_onset",
      "latency_anomaly",
      "broken",
      "container_unhealthy",
    ]);
    expect(rows[0]).toEqual({
      name: "earliest_onset",
      value: 1,
      weight: 0.4,
      contribution: 0.4,
      detail: "earliest",
    });
    expect(rows[2]).toMatchObject({ value: 0, contribution: 0, detail: "" });
  });
});
