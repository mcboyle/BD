import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";

// Cut 6.5 — Needs-attention Home rollup. Aggregation happens at the call site
// (it folds together existing counts: failed runs, review backlog, expired
// cookies, drift); this component just renders the resulting entries as linked
// rows. Renders null when there is nothing to surface.

export interface AttentionRollupEntry {
  kind: string;
  label: string;
  count: number;
  href: string;
}

export function NeedsAttentionRollup({
  entries,
}: {
  entries: AttentionRollupEntry[];
}) {
  if (!entries.length) return null;

  return (
    <section
      className="rounded-md hairline bg-amber-soft/40 p-3"
      aria-label="Needs attention"
    >
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-amber-dim">
        <AlertTriangle aria-hidden className="h-4 w-4" />
        Needs attention
      </div>
      <ul className="space-y-1">
        {entries.map((e) => (
          <li key={e.kind}>
            <Link
              to={e.href}
              className="flex items-center justify-between gap-2 rounded px-2 py-1.5 text-sm text-ink hover:bg-surface-2"
            >
              <span className="flex items-center gap-2">
                <Badge variant="warning">{e.count}</Badge>
                {e.label}
              </span>
              <ArrowRight aria-hidden className="h-4 w-4 text-ink-3" />
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
