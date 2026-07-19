import { cn } from "@/lib/utils";

// V1 — Status pill primitive.
//
// Mockup signature element. Appears throughout: Home (running/done/
// failed counts), Queue (the same set), Sites (Active/Paused/Issues
// filter pills), Activity (time-range pills 24h/7d/30d/All), Advanced
// (PASS/OK/WAL status badges).
//
// Tones map to design tokens:
//   - neutral → surface bg, ink text (e.g. "All 6" inactive filter)
//   - primary → primary bg, white text (e.g. active filter dark pill)
//   - amber   → amber-soft bg, amber text (e.g. "0 running")
//   - green   → green-soft bg, green text (e.g. "0 done", "PASS")
//   - red     → red-soft bg, red text (e.g. "0 failed")
//
// Sizing:
//   - sm: text-[11px] padding compact — for tab-bar context
//   - md (default): text-xs — Home/Queue counters
//   - lg: text-sm — filter pills, count headlines
//
// Use the `selected` prop to flip a neutral pill to the dark primary
// treatment (mockup's Sites filter shows "All" with ink bg + white
// text when selected, light surface otherwise).

export type PillTone = "neutral" | "primary" | "amber" | "green" | "red";
export type PillSize = "sm" | "md" | "lg";

export interface StatusPillProps {
  tone?: PillTone;
  size?: PillSize;
  /**
   * Render in the inverse, active-filter treatment (dark ink bg,
   * white text). Overrides `tone` when true.
   */
  selected?: boolean;
  leadingIcon?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /**
   * If set, renders as a button with this handler. Otherwise renders
   * as a non-interactive span.
   */
  onClick?: () => void;
  /**
   * For interactive use as a filter — when set, aria-pressed reflects
   * `selected`.
   */
  asToggle?: boolean;
  ariaLabel?: string;
}

const TONE_CLASSES: Record<PillTone, string> = {
  neutral: "bg-surface text-ink hairline border",
  primary: "bg-primary text-white",
  amber: "bg-amber-soft text-amber-dim",
  green: "bg-green-soft text-green",
  red: "bg-red-soft text-red",
};

const SIZE_CLASSES: Record<PillSize, string> = {
  sm: "px-2 py-0.5 text-[11px]",
  md: "px-2.5 py-1 text-xs",
  lg: "px-3 py-1.5 text-sm",
};

export function StatusPill({
  tone = "neutral",
  size = "md",
  selected = false,
  leadingIcon,
  children,
  className,
  onClick,
  asToggle = false,
  ariaLabel,
}: StatusPillProps) {
  const toneClass = selected
    ? "bg-ink text-surface"
    : TONE_CLASSES[tone];

  const base = cn(
    "inline-flex items-center gap-1 rounded-full font-semibold tabular-nums",
    "tracking-tight transition-colors",
    SIZE_CLASSES[size],
    toneClass,
    onClick && "cursor-pointer hover:opacity-90 active:opacity-80",
    className,
  );

  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        aria-label={ariaLabel}
        aria-pressed={asToggle ? selected : undefined}
        className={base}
      >
        {leadingIcon && <span aria-hidden>{leadingIcon}</span>}
        <span>{children}</span>
      </button>
    );
  }

  return (
    <span className={base} aria-label={ariaLabel}>
      {leadingIcon && <span aria-hidden>{leadingIcon}</span>}
      <span>{children}</span>
    </span>
  );
}
