import { describe, expect, it } from "vitest";

import { eventBadge } from "@/components/incidents/badges";
import { alertBody, shouldAlert } from "@/lib/approval-alerts";
import { incidentQueryParams, incidentsCsvUrl } from "@/lib/api/incidents";
import { EVENT_TYPES } from "@/lib/events";
import { formatDuration } from "@/lib/format";

describe("formatDuration", () => {
  it("picks a compact unit", () => {
    expect(formatDuration(0)).toBe("0s");
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(185)).toBe("3m 05s");
    expect(formatDuration(2 * 3600 + 4 * 60 + 9)).toBe("2h 04m");
    expect(formatDuration(27 * 3600)).toBe("1d 3h");
  });

  it("tolerates missing and bad values", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(undefined)).toBe("—");
    expect(formatDuration(Number.NaN)).toBe("—");
    expect(formatDuration(-5)).toBe("0s");
  });
});

describe("incident query", () => {
  it("omits empty search and trims it", () => {
    expect(incidentQueryParams({ status: "open", q: "   " }).has("q")).toBe(false);
    expect(incidentQueryParams({ q: "  checkout " }).get("q")).toBe("checkout");
    expect(incidentQueryParams({ q: "x".repeat(300) }).get("q")).toHaveLength(100);
  });

  it("exports the same filter as CSV", () => {
    const url = new URL(incidentsCsvUrl({ status: "closed", q: "cart 50%" }));
    expect(url.pathname).toBe("/api/incidents/export.csv");
    expect(url.searchParams.get("status")).toBe("closed");
    expect(url.searchParams.get("q")).toBe("cart 50%");
    expect(url.searchParams.get("limit")).toBe("1000");
  });
});

describe("approval alerts", () => {
  it("never alerts on the first read (a replayed backlog is not news)", () => {
    expect(shouldAlert(null, 3)).toBe(false);
  });

  it("alerts only when the pending count grows", () => {
    expect(shouldAlert(0, 1)).toBe(true);
    expect(shouldAlert(2, 2)).toBe(false);
    expect(shouldAlert(2, 1)).toBe(false);
  });

  it("describes what changed", () => {
    expect(alertBody(1, 1)).toBe("A remediation awaits your approval.");
    expect(alertBody(3, 1)).toBe("A remediation awaits your approval. 3 pending in total.");
    expect(alertBody(2, 2)).toBe("2 new remediations await approval.");
  });
});

describe("notes", () => {
  it("are subscribed to and stand out in the timeline", () => {
    expect(EVENT_TYPES).toContain("incident.note");
    expect(eventBadge("note")).toBe("default");
  });
});

describe("alert snapshot", () => {
  it("is off on the server and when permission was revoked", async () => {
    const { parseAlertSnapshot, SERVER_ALERT_SNAPSHOT } = await import("@/lib/approval-alerts");
    expect(parseAlertSnapshot(SERVER_ALERT_SNAPSHOT)).toEqual({
      permission: "unsupported",
      enabled: false,
    });
    expect(parseAlertSnapshot("denied|on").enabled).toBe(false);
    expect(parseAlertSnapshot("granted|on").enabled).toBe(true);
    expect(parseAlertSnapshot("granted|off").enabled).toBe(false);
  });
});
