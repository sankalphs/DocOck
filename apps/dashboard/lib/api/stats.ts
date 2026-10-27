import { request } from "./client";
import type { components } from "./schema";

export type Stats = components["schemas"]["Stats"];
export type Distribution = components["schemas"]["Distribution"];

export function fetchStats(windowH = 168, signal?: AbortSignal): Promise<Stats> {
  return request<Stats>(`/api/stats?window_h=${windowH}`, { signal });
}

/** `p50 / p95` of a distribution in a unit, or a dash when empty. */
export function spread(dist: Distribution, unit: string, scale = 1): string {
  if (!dist.count || dist.p50 === null || dist.p50 === undefined) return "—";
  const fmt = (value: number | null | undefined) =>
    value === null || value === undefined ? "—" : `${Math.round(value / scale)} ${unit}`;
  return `${fmt(dist.p50)} / ${fmt(dist.p95)}`;
}

/** Share of the daily LLM budget spent, clamped to [0, 1]; null without a cap. */
export function budgetShare(stats: Stats): number | null {
  const used = stats.agent.budget_used;
  if (used === null || used === undefined) return null;
  return Math.max(0, Math.min(1, used));
}

export function usd(value: number): string {
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`;
}
