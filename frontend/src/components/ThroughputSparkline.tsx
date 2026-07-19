import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, ResponsiveContainer, Tooltip } from "recharts";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import type { SparklineV2 } from "@/lib/api-types";
import { formatRate } from "@/lib/format";

// Throughput sparkline — Recharts AreaChart of the last 60 samples
// of bytes/sec across all sites. Polled separately from the main
// dashboard at a faster cadence (2s busy, 10s idle) since this is
// the most live-feeling element on the page.
//
// U8 a11y: the chart is wrapped with role="img" + an aria-label that
// summarises the data ("Throughput last hour, currently 4.2 MB/s,
// 60 samples"). Without this, screen readers see the inner SVG and
// announce nothing useful. The current rate is also rendered as
// plain text adjacent to the chart, which gives a redundant
// text-only path for the same info.

export function ThroughputSparkline() {
  const { data, isLoading } = useQuery<SparklineV2>({
    queryKey: ["sparkline-v2"],
    queryFn: ({ signal }) => apiGet<SparklineV2>("/api/dashboard/v2/sparkline", signal),
    refetchInterval: (query) =>
      adaptiveInterval<SparklineV2>({
        query,
        isBusy: (d) => d.current > 0,
        fast: 2000,
        slow: 10_000,
      }),
  });

  if (isLoading || !data) {
    return (
      <Card className="p-4" aria-busy="true" aria-label="Loading throughput chart">
        <div className="mb-2 flex items-baseline justify-between">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-16" />
        </div>
        <Skeleton className="h-16 w-full" />
      </Card>
    );
  }

  const samples = data.history ?? [];
  const hasData = samples.length > 1;
  const a11yLabel = hasData
    ? `Throughput sparkline, currently ${formatRate(data.current)}, ${samples.length} samples`
    : "Throughput sparkline, no activity yet";

  return (
    <Card className="p-4">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="eyebrow">
          Throughput · last hour
        </span>
        <span className="text-sm font-semibold tabular text-ink-2">
          {formatRate(data.current)}
        </span>
      </div>
      {hasData ? (
        <div
          className="h-16 w-full"
          role="img"
          aria-label={a11yLabel}
        >
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart
              data={samples}
              margin={{ top: 4, right: 0, bottom: 0, left: 0 }}
            >
              <defs>
                <linearGradient id="thruFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--primary)" stopOpacity={0.4} />
                  <stop offset="100%" stopColor="var(--primary)" stopOpacity={0} />
                </linearGradient>
              </defs>
              <Tooltip
                cursor={false}
                contentStyle={{
                  background: "var(--surface)",
                  border: "0.5px solid var(--hairline)",
                  borderRadius: "10px",
                  fontSize: 11,
                  padding: "4px 8px",
                }}
                formatter={(value: number) => [formatRate(value), "rate"]}
                labelFormatter={() => ""}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="var(--primary)"
                strokeWidth={1.5}
                fill="url(#thruFill)"
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="py-3 text-sm text-ink-3">No activity yet</p>
      )}
    </Card>
  );
}
