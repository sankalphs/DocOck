import { describe, expect, it } from "vitest";

import { EVENT_TYPES, eventGroup } from "@/lib/events";
import { NAV_ITEMS, isNavItemActive, navItemFor } from "@/lib/nav";

describe("EVENT_TYPES", () => {
  it("is unique", () => {
    expect(new Set(EVENT_TYPES).size).toBe(EVENT_TYPES.length);
  });

  it("covers every namespace the API publishes", () => {
    const groups = new Set(EVENT_TYPES.map((type) => type.split(".")[0]));
    expect(groups).toEqual(
      new Set([
        "system",
        "fault",
        "incident",
        "agent",
        "memory",
        "approval",
        "action",
        "verification",
        "report",
      ]),
    );
  });
});

describe("eventGroup", () => {
  it("returns the namespace prefix", () => {
    expect(eventGroup("fault.injected")).toBe("fault");
    expect(eventGroup("unknown")).toBe("unknown");
  });
});

describe("navigation", () => {
  it("has exactly the ten PLAN sections", () => {
    expect(NAV_ITEMS).toHaveLength(10);
    expect(NAV_ITEMS.map((item) => item.label)).toEqual([
      "Overview",
      "Fault Lab",
      "Telemetry",
      "Incidents",
      "Agent",
      "Approvals",
      "Reports",
      "Memory",
      "Models",
      "Settings",
    ]);
  });

  it("has every section built after P16", () => {
    expect(NAV_ITEMS.filter((item) => !item.ready).map((item) => item.label)).toEqual([]);
  });

  it("resolves nested paths to their section", () => {
    expect(navItemFor("/dashboard").label).toBe("Overview");
    expect(navItemFor("/telemetry").label).toBe("Telemetry");
    expect(navItemFor("/incidents/INC-00042").label).toBe("Incidents");
    expect(navItemFor("/nope").label).toBe("Overview");
  });

  it("does not match a section by a shared prefix", () => {
    expect(isNavItemActive(NAV_ITEMS[1], "/faults")).toBe(true);
    expect(isNavItemActive(NAV_ITEMS[1], "/faultsy")).toBe(false);
    expect(isNavItemActive(NAV_ITEMS[0], "/")).toBe(false);
  });
});
