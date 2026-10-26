import { describe, expect, it } from "vitest";

import {
  aggregateErrorRate,
  latestValue,
  maxLatest,
  sumLatest,
  toChartData,
  worstService,
} from "@/lib/chart";
import type { MetricSeries } from "@/lib/api/telemetry";

function series(name: string, values: number[], start = 0): MetricSeries {
  return {
    labels: { service_name: name },
    points: values.map((v, index) => ({
      t: new Date(Date.UTC(2026, 9, 24, 6, 0, start + index * 30)).toISOString(),
      v,
    })),
  };
}

describe("toChartData", () => {
  it("merges series into one row per timestamp", () => {
    const data = toChartData([series("payment", [1, 2], 0), series("cart", [3, 4], 0)]);

    expect(data.series.sort()).toEqual(["cart", "payment"]);
    expect(data.rows).toHaveLength(2);
    expect(data.rows[0].payment).toBe(1);
    expect(data.rows[1].cart).toBe(4);
  });

  it("keeps the top N by latest value", () => {
    const data = toChartData(
      [series("a", [1]), series("b", [5]), series("c", [3]), series("d", [9])],
      2,
    );

    expect(data.series).toEqual(["d", "b"]);
  });

  it("fills missing timestamps with null instead of undefined", () => {
    const data = toChartData([series("a", [1, 2], 0), series("b", [9], 30)]);

    expect(data.rows).toHaveLength(2);
    expect(data.rows[0].b).toBeNull();
    expect(data.rows[1].b).toBe(9);
  });

  it("handles an empty series list", () => {
    expect(toChartData([])).toEqual({ rows: [], series: [] });
  });

  it("uses container_name when there is no service_name", () => {
    const data = toChartData([
      { labels: { container_name: "payment" }, points: [{ t: "2026-10-24T06:00:00Z", v: 1 }] },
    ]);
    expect(data.series).toEqual(["payment"]);
  });
});

describe("aggregates", () => {
  const error = [series("payment", [0.5]), series("cart", [0.1]), series("quiet", [0])];
  const throughput = [series("payment", [1]), series("cart", [9]), series("quiet", [0])];

  it("weights the error rate by throughput", () => {
    // (0.5*1 + 0.1*9) / (1 + 9) = 0.14
    expect(aggregateErrorRate(error, throughput)).toBeCloseTo(0.14, 5);
  });

  it("returns null when there is no traffic", () => {
    expect(aggregateErrorRate(error, [])).toBeNull();
  });

  it("sums and maxes latest values", () => {
    expect(sumLatest(throughput)).toBe(10);
    expect(maxLatest(error)).toBe(0.5);
    expect(worstService(error)).toBe("payment");
    expect(latestValue(series("x", [1, 7]))).toBe(7);
    expect(latestValue(series("x", []))).toBeNull();
  });
});
