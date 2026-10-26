import { request } from "./client";
import type { components } from "./schema";

export type ModelSettingsView = components["schemas"]["ModelSettingsView"];
export type ModelSettingsPatch = components["schemas"]["ModelSettingsPatch"];
export type ModelTestReport = components["schemas"]["ModelTestReport"];
export type ModelTestResult = components["schemas"]["ModelTestResult"];

export function fetchModelSettings(signal?: AbortSignal): Promise<ModelSettingsView> {
  return request<ModelSettingsView>("/api/settings/models", { signal });
}

export function patchModelSettings(patch: ModelSettingsPatch): Promise<ModelSettingsView> {
  return request<ModelSettingsView>("/api/settings/models", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
}

export function testModels(target: "llm" | "embedding" | "all"): Promise<ModelTestReport> {
  return request<ModelTestReport>("/api/settings/models/test", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
}

/** Which fields currently come from a runtime override rather than `.env`. */
export function overriddenFields(view: ModelSettingsView, section: string): Set<string> {
  const values = (view.overrides as Record<string, Record<string, unknown> | undefined>)[section];
  return new Set(Object.keys(values ?? {}));
}
