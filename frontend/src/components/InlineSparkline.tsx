import { useMemo } from "react";

import { cn } from "@/lib/utils";

// Compact per-row SVG sparkline. Renders without Recharts — at one
// per Activity row, multiplied by the rendered count, the per-tile
// cost of Recharts' ResponsiveContainer (ResizeObserver, layout) is
// non-trivial. A 60×16 inline SVG is much cheaper and renders crisp
// at any DPI.
//
// Inputs:
//   values  — array of non-negative numbers, oldest → newest
//   width   — px (default 60)
//   height  — px (default 16)
//   color   — Tailwind text color (line) — default text-ink-3
//
// Behavior:
//   - Empty / all-zero: renders a thin horizontal baseline (faint).
//   - Single value: renders a single dot, centered.
//   - Otherwise: polyline scaled to [0, max(values)] vertically and
//     [0, values.length - 1] horizontally.

export interface InlineSparklineProps {
  values: readonly number[];
  width?: number;
  height?: number;
  className?: string;
  ariaLabel?: string;
}

export function InlineSparkline({
  values,
  width = 60,
  height = 16,
  className,
  ariaLabel,
}: InlineSparklineProps) {
  const path = useMemo(() => {
    if (values.length === 0) return null;
    const max = Math.max(...values, 1); // avoid /0
    const padX = 1;
    const padY = 2;
    const usableW = width - 2 * padX;
    const usableH = height - 2 * padY;
    if (values.length === 1) {
      const cx = width / 2;
      const cy = height - padY - (values[0] / max) * usableH;
      return { type: "dot" as const, cx, cy };
    }
    const step = usableW / (values.length - 1);
    const points = values
      .map((v, i) => {
        const x = padX + i * step;
        const y = height - padY - (v / max) * usableH;
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
    return { type: "line" as const, points };
  }, [values, width, height]);

  const total = values.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);

  // Empty / all-zero: a thin baseline.
  const empty = total === 0;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={
        ariaLabel ??
        `Activity sparkline, ${values.length} day${values.length === 1 ? "" : "s"}, total ${total}`
      }
      className={cn("shrink-0", className)}
    >
      {empty && (
        <line
          x1={1}
          y1={height / 2}
          x2={width - 1}
          y2={height / 2}
          stroke="currentColor"
          strokeWidth={1}
          opacity={0.25}
        />
      )}
      {!empty && path?.type === "dot" && (
        <circle cx={path.cx} cy={path.cy} r={2} fill="currentColor" />
      )}
      {!empty && path?.type === "line" && (
        <polyline
          points={path.points}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.25}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
    </svg>
  );
}
