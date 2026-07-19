import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

// V2 — Today tri-stat card (mockup signature).
//
// Three counters in one card: Done / Running / Failed. Mockup uses
// big display-size numbers with tiny uppercase labels underneath,
// color-coded by status (green / amber / red). On the home mockup
// home.png + 04_sleek_dashboard.png, the section header is the small
// "ACTIVITY" eyebrow above the row, not a discrete "Today" header
// (since "TODAY" is a section grouper for several cards now). V2
// removes the "Today" header from this card since the section grouper
// is rendered by Home.tsx instead.
//
// Pre-V2 visual: text-2xl numbers, tight grid.
// V2 visual: text-3xl numbers, more breathing room, ACTIVITY eyebrow
// retained for stand-alone use; Home.tsx now wraps this in a TODAY
// section so the eyebrow plus heading together read as the mockup
// intent.
//
// Zero-value visual: pre-V2 dimmed to ink-3 when value === 0.
// V2 keeps the color but at reduced opacity so the three columns
// still read as a tri-color triplet even when idle (matches the
// "0 0 0" treatment in the home.png mockup).

export interface TodayStatsCardProps {
  done: number;
  running: number;
  failed: number;
  /** If true, hides the ACTIVITY eyebrow so a wrapping section header
   *  can carry the labeling. Used by Home.tsx in v3.65.x. */
  hideEyebrow?: boolean;
}

const STATS: Array<{
  key: "done" | "running" | "failed";
  label: string;
  color: string;
}> = [
  { key: "done",    label: "Done",    color: "text-green" },
  { key: "running", label: "Running", color: "text-amber" },
  { key: "failed",  label: "Failed",  color: "text-red" },
];

export function TodayStatsCard(props: TodayStatsCardProps) {
  return (
    <Card className="p-4">
      {!props.hideEyebrow && (
        <div className="mb-3 eyebrow">
          Activity
        </div>
      )}
      <div className="grid grid-cols-3 gap-2">
        {STATS.map(({ key, label, color }) => {
          const value = props[key];
          const isZero = value === 0;
          return (
            <div key={key} className="flex flex-col items-start gap-1">
              <span
                className={cn(
                  "text-3xl font-bold tabular tracking-tighter leading-none",
                  color,
                  isZero && "opacity-40",
                )}
              >
                {value}
              </span>
              <span className="eyebrow">
                {label}
              </span>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
