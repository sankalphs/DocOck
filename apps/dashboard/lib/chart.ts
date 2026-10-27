import type { MetricSeries } from "@/lib/api/telemetry";

export type ChartRow = {
  t: number;
  /** Local HH:MM:SS, used as the categorical x-axis label. */
  label: string;
  [series: string]: number | null | string;
};

export type ChartData = {
  rows: ChartRow[];
  series: string[];
};

/**
 * Recharts wants one row per timestamp with a column per series. Prometheus
 * returns one series object per label set, so this merges them and keeps the
 * top-N by latest value — 18 lines is noise, not information.
 */
export function toChartData(series: MetricSeries[], limit = 6): ChartData {
  if (series.length === 0) return { rows: [], series: [] };

  const ranked = series
    .map((item) => {
      const points = item.points ?? [];
      return {
        name: labelFor(item),
        points,
        latest: points.length ? points[points.length - 1].v : 0,
      };
    })
    .filter((item) => item.points.length > 0)
    .sort((a, b) => b.latest - a.latest)
    .slice(0, limit);

  const names = ranked.map((item) => item.name);
  const byTime = new Map<number, ChartRow>();

  for (const item of ranked) {
    for (const point of item.points) {
      const key = new Date(point.t).getTime();
      const row = byTime.get(key) ?? {
        t: key,
        label: new Date(key).toLocaleTimeString([], { hour12: false }),
      };
      row[item.name] = Number.isFinite(point.v) ? point.v : null;
      byTime.set(key, row);
    }
  }

  const rows = [...byTime.values()].sort((a, b) => a.t - b.t);
  for (const row of rows) {
    for (const name of names) {
      if (!(name in row)) row[name] = null;
    }
  }

  return { rows, series: names };
}

export function labelFor(item: MetricSeries): string {
  return item.labels?.service_name ?? item.labels?.container_name ?? "unknown";
}

export function latestValue(item: MetricSeries): number | null {
  const points = item.points ?? [];
  if (points.length === 0) return null;
  return points[points.length - 1].v;
}

/** Throughput-weighted aggregate error rate across services. */
export function aggregateErrorRate(
  errorSeries: MetricSeries[],
  throughputSeries: MetricSeries[],
): number | null {
  const throughput = new Map(
    throughputSeries.map((item) => [labelFor(item), latestValue(item) ?? 0]),
  );
  let weighted = 0;
  let total = 0;
  for (const item of errorSeries) {
    const rate = latestValue(item);
    const weight = throughput.get(labelFor(item)) ?? 0;
    if (rate === null || weight <= 0) continue;
    weighted += rate * weight;
    total += weight;
  }
  return total > 0 ? weighted / total : null;
}

export function sumLatest(series: MetricSeries[]): number | null {
  const values = series.map(latestValue).filter((value): value is number => value !== null);
  return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
}

export function maxLatest(series: MetricSeries[]): number | null {
  const values = series.map(latestValue).filter((value): value is number => value !== null);
  return values.length ? Math.max(...values) : null;
}

export function worstService(series: MetricSeries[]): string | null {
  let worst: { name: string; value: number } | null = null;
  for (const item of series) {
    const value = latestValue(item);
    if (value === null) continue;
    if (!worst || value > worst.value) worst = { name: labelFor(item), value };
  }
  return worst?.name ?? null;
}

/** Chart colour palette, stable per series index. */
export const SERIES_COLORS = [
  "#4f9a3a",
  "#d9506c",
  "#2a8c8c",
  "#c7891a",
  "#4f78a8",
  "#9a6b3f",
  "#a8445e",
  "#7c8f2a",
];
