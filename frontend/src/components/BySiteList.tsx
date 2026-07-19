import { Card } from "@/components/ui/card";
import { SiteAvatar } from "@/components/SiteAvatar";
import type { BySiteEntry } from "@/lib/api-types";

// By-site card — at-a-glance list of every loaded site with its
// running/queued/today counts. Mockup signature: tiny, dense, one
// row per site, monospace tabular numerics so the columns line up.

export interface BySiteListProps {
  bySite: BySiteEntry[];
}

export function BySiteList({ bySite }: BySiteListProps) {
  if (bySite.length === 0) {
    return null;
  }
  return (
    <Card className="p-4">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="eyebrow">
          By site
        </span>
        <span className="text-[10px] uppercase tracking-wide text-ink-3">
          run · queue · today
        </span>
      </div>
      <ul className="divide-y divide-hairline">
        {bySite.map((entry) => (
          <li
            key={entry.site_id}
            className="flex items-center gap-2 py-2 text-sm first:pt-0 last:pb-0"
          >
            <SiteAvatar
              name={entry.name}
              color={entry.avatar_color}
              size="sm"
            />
            <span className="min-w-0 flex-1 truncate font-medium">
              {entry.name}
            </span>
            <span className="flex items-center gap-2 tabular text-xs text-ink-2">
              <span className={entry.running > 0 ? "text-green" : "text-ink-3"}>
                {entry.running}
              </span>
              <span className="text-ink-3">·</span>
              <span className={entry.queued > 0 ? "text-amber" : "text-ink-3"}>
                {entry.queued}
              </span>
              <span className="text-ink-3">·</span>
              <span className="text-ink-3">{entry.today_done}</span>
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
