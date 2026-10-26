"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { ChartData } from "@/lib/chart";
import { SERIES_COLORS } from "@/lib/chart";
import { formatMetric } from "@/lib/format";

type MetricChartProps = {
  title: string;
  description: string;
  unit: string;
  data: ChartData;
  loading?: boolean;
  error?: string | null;
  height?: number;
};

const AXIS_STYLE = { fontSize: 10, fill: "var(--muted-foreground)" } as const;

export function MetricChart({
  title,
  description,
  unit,
  data,
  loading,
  error,
  height = 240,
}: MetricChartProps) {
  const hasData = data.rows.length > 0;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>{title}</CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        <span className="text-[11px] text-muted-foreground">{unit}</span>
      </CardHeader>
      <CardContent>
        {loading && !hasData ? (
          <Skeleton className="w-full" style={{ height }} />
        ) : error && !hasData ? (
          <div
            className="flex items-center justify-center rounded-lg border border-destructive/30 bg-destructive/5 text-xs text-destructive"
            style={{ height }}
          >
            {error}
          </div>
        ) : !hasData ? (
          <div
            className="flex items-center justify-center rounded-lg border border-dashed border-border text-xs text-muted-foreground"
            style={{ height }}
          >
            No samples in this window yet.
          </div>
        ) : (
          <div style={{ height }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.rows} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
                <CartesianGrid stroke="var(--border)" strokeDasharray="3 3" vertical={false} />
                <XAxis
                  dataKey="label"
                  interval="preserveStartEnd"
                  minTickGap={44}
                  tick={AXIS_STYLE}
                  tickLine={false}
                  axisLine={{ stroke: "var(--border)" }}
                />
                <YAxis
                  tick={AXIS_STYLE}
                  tickLine={false}
                  axisLine={false}
                  width={56}
                  tickFormatter={(value: number) => formatMetric(value, unit)}
                />
                <Tooltip
                  isAnimationActive={false}
                  contentStyle={{
                    background: "var(--popover)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    fontSize: 11,
                    color: "var(--popover-foreground)",
                  }}
                  labelFormatter={(value) => String(value)}
                  formatter={(value) => formatMetric(Number(value), unit)}
                />
                <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconType="plainline" />
                {data.series.map((name, index) => (
                  <Line
                    key={name}
                    type="monotone"
                    dataKey={name}
                    stroke={SERIES_COLORS[index % SERIES_COLORS.length]}
                    strokeWidth={1.6}
                    dot={false}
                    activeDot={{ r: 3 }}
                    connectNulls={false}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
