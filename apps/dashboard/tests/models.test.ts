import { describe, expect, it } from "vitest";

import { overriddenFields, type ModelSettingsView } from "@/lib/api/models";

describe("model settings overrides", () => {
  it("lists only the fields stored as runtime overrides", () => {
    const view = {
      overrides: { llm: { provider: "zenmux" }, embedding: {} },
    } as unknown as ModelSettingsView;
    expect([...overriddenFields(view, "llm")]).toEqual(["provider"]);
    expect(overriddenFields(view, "embedding").size).toBe(0);
    expect(overriddenFields(view, "agent").size).toBe(0);
  });
});
