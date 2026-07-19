import { cn } from "@/lib/utils";
import type { KPISpec } from "@/lib/widgetCatalog";

// v3.64.x — generic renderer for KPI widgets. One component covers all
// 36 catalog widgets; the catalog returns a `KPISpec` object and this
// component renders it.
//
// Visual contract (matches legacy widgets.css):
//   • Big value (24px, weight 500) at the top
//   • Optional delta pill below (+12 / -3 / flat color-coded)
//   • Optional 4px meter bar
//   • Optional inline SVG sparkline
//   • Optional second-line "extra" text in ink-3
//
// valueKind drives a color tint on the value text:
//   ok=green   warn=amber   alert=red   action=primary

export interface KPICardProps {
  spec: KPISpec;
  /** If true, render a smaller variant. Used when the picker preview
   *  shows a half-size widget. */
  compact?: boolean;
}

export function KPICard({ spec, compact = false }: KPICardProps) {
  const value = spec.value == null || spec.value === "" ? "—" : spec.value;
  const valueColor =
    spec.valueKind === "ok" ? "text-green"
    : spec.valueKind === "warn" ? "text-amber"
    : spec.valueKind === "alert" ? "text-red"
    : spec.valueKind === "action" ? "text-primary"
    : "text-ink";

  const deltaSign = computeDeltaSign(spec.delta);
  const deltaColor =
    deltaSign === "up" ? "text-green"
    : deltaSign === "down" ? "text-red"
    : "text-ink-3";

  const meterColor =
    spec.meterKind === "ok" ? "bg-green"
    : spec.meterKind === "warn" ? "bg-amber"
    : spec.meterKind === "alert" ? "bg-red"
    : "bg-primary";
  const meterWidth = clampPct(spec.meter);

  return (
    <div className="flex h-full flex-col gap-1.5 p-3">
      <div className="eyebrow">
        {spec.label}
      </div>
      <div
        className={cn(
          "font-bold tabular leading-none tracking-tighter",
          compact ? "text-lg" : "text-2xl",
          valueColor,
          // Mockup signature: when the value is the em-dash placeholder
          // (i.e. data not yet available), dim it so a dashboard of
          // empty-state cards visually recedes rather than competing
          // for attention with real values.
          (spec.value == null || spec.value === "") && "opacity-40",
        )}
      >
        {value}
      </div>
      {spec.delta && (
        <div className={cn("text-[11px] font-medium tabular", deltaColor)}>
          {spec.delta}
        </div>
      )}
      {spec.meter != null && (
        <div className="mt-0.5 h-1 w-full overflow-hidden rounded-full bg-surface-2">
          <div
            className={cn("h-full transition-all", meterColor)}
            style={{ width: `${meterWidth}%` }}
            aria-hidden
          />
        </div>
      )}
      {spec.spark && spec.spark.length >= 2 && (
        <Sparkline values={spec.spark} />
      )}
      {spec.extra && (
        <div className="truncate text-[11px] text-ink-3">{spec.extra}</div>
      )}
    </div>
  );
}

// ── Helpers ────────────────────────────────────────────────────────

function computeDeltaSign(delta?: string | null): "up" | "down" | "flat" {
  if (!delta) return "flat";
  const trimmed = delta.trim();
  if (trimmed === "" || trimmed === "0" || trimmed === "flat") return "flat";
  if (trimmed.startsWith("-")) return "down";
  if (trimmed.startsWith("0")) return "flat";
  return "up";
}

function clampPct(v?: number | null): number {
  if (v == null) return 0;
  return Math.max(0, Math.min(100, Number(v)));
}

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) return null;
  const max = Math.max(...values) || 1;
  const w = 100;
  const h = 16;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * w;
    const y = h - (v / max) * h;
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  const line = `M ${pts.join(" L ")}`;
  return (
    <svg
      viewBox={`0 0 ${w} ${h}`}
      preserveAspectRatio="none"
      className="h-4 w-full"
      aria-hidden
    >
      <path
        d={line}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.25"
        className="text-primary"
      />
    </svg>
  );
}
