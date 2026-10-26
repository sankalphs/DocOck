import { request } from "./client";
import type { components } from "./schema";

export type MemoryStatus = components["schemas"]["MemoryStatus"];
export type MemoryHit = components["schemas"]["MemoryHit"];
export type MemorySearchResult = components["schemas"]["MemorySearchResult"];
export type MemoryView = components["schemas"]["MemoryView"];
export type MemoryList = components["schemas"]["MemoryList"];

export function fetchMemoryStatus(signal?: AbortSignal): Promise<MemoryStatus> {
  return request<MemoryStatus>("/api/memory/status", { signal });
}

export function fetchMemories(limit = 50, signal?: AbortSignal): Promise<MemoryList> {
  return request<MemoryList>(`/api/memory?limit=${limit}`, { signal });
}

export function searchMemory(
  query: { q?: string; incident?: string; k?: number },
  signal?: AbortSignal,
): Promise<MemorySearchResult> {
  const params = new URLSearchParams();
  if (query.incident) params.set("incident", query.incident);
  else if (query.q) params.set("q", query.q);
  if (query.k) params.set("k", String(query.k));
  return request<MemorySearchResult>(`/api/memory/search?${params}`, { signal });
}

/** The template's first line: `<service>. <fault class>. <symptom>.` */
export function headline(summary: string): string {
  return summary.split("\n", 1)[0] ?? summary;
}

/** Score bar width, clamped to 0–100 %. */
export function scorePercent(score: number): number {
  return Math.max(0, Math.min(100, Math.round(score * 100)));
}
