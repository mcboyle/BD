import { Link } from "react-router-dom";

import { cn } from "@/lib/utils";

// Cut 6.2 — read-only count tiles. Counts are computed at the call site from
// existing hooks/endpoints and passed in; the strip is purely presentational.
// The review tile links into the Cockpit (the operator-review surface); the
// others link to their SPA routes.

export interface TileCounts {
  queue: number;
  review: number;
  capture: number;
  template: number;
}

const TILES: { key: keyof TileCounts; label: string; to: string }[] = [
  { key: "queue", label: "Queue", to: "/queue" },
  { key: "review", label: "Review", to: "/cockpit/review" },
  { key: "capture", label: "Capture", to: "/capture" },
  { key: "template", label: "Templates", to: "/templates" },
];

export function CountTiles({ counts }: { counts: TileCounts }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {TILES.map((t) => (
        <Link
          key={t.key}
          to={t.to}
          className={cn(
            "rounded-md hairline bg-surface p-3 transition-colors",
            "hover:bg-surface-2 focus-visible:outline focus-visible:outline-2",
          )}
        >
          <span className="block text-2xl font-semibold tabular-nums text-ink">
            {counts[t.key]}
          </span>
          <span className="mt-0.5 block text-xs text-ink-3">{t.label}</span>
        </Link>
      ))}
    </div>
  );
}
