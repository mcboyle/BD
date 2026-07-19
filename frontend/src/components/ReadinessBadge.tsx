import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";

import { apiGet } from "@/lib/api-client";
import type {
  SiteReadinessResponse,
  ReadinessLevel,
  OiCheckStatus,
} from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Cut 4 — per-site readiness badge.
//
// GET /api/sites/<id>/readiness rolls the per-site signals (auth health,
// selector drift, config completeness, download-dir writability) into a single
// green/amber/red level with "fix this" hints. Read-only.
//
//   - compact (default): just the dot + level — for the Sites list rows.
//   - expanded: dot + level + the per-check list + fixes — for SiteDetail.

const DOT: Record<ReadinessLevel, string> = {
  green: "bg-green",
  amber: "bg-amber",
  red: "bg-red",
};
const TEXT: Record<ReadinessLevel, string> = {
  green: "text-green",
  amber: "text-amber-dim",
  red: "text-red",
};
const LABEL: Record<ReadinessLevel, string> = {
  green: "Ready",
  amber: "Needs attention",
  red: "Not ready",
};
const CHECK_DOT: Record<OiCheckStatus, string> = {
  ok: "bg-green",
  warn: "bg-amber",
  fail: "bg-red",
};

export interface ReadinessBadgeProps {
  siteId: string;
  expanded?: boolean;
  className?: string;
}

export function ReadinessBadge({
  siteId,
  expanded = false,
  className,
}: ReadinessBadgeProps) {
  const { data, isLoading, isError } = useQuery<SiteReadinessResponse>({
    queryKey: ["site-readiness", siteId],
    queryFn: ({ signal }) =>
      apiGet<SiteReadinessResponse>(
        `/api/sites/${encodeURIComponent(siteId)}/readiness`,
        signal,
      ),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });

  if (isLoading) {
    return (
      <span
        className={cn("inline-flex items-center gap-1 text-xs text-ink-3", className)}
        aria-busy
      >
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        {expanded && "Checking…"}
      </span>
    );
  }

  if (isError || !data || !data.ok) {
    return (
      <span className={cn("inline-flex items-center gap-1 text-xs text-ink-3", className)}>
        <span className="h-2 w-2 rounded-full bg-ink-3" aria-hidden />
        {expanded && "Readiness unavailable"}
      </span>
    );
  }

  const level = data.level;

  if (!expanded) {
    return (
      <span
        className={cn("inline-flex items-center gap-1.5 text-xs font-medium", TEXT[level], className)}
        aria-label={`Readiness: ${LABEL[level]}`}
      >
        <span className={cn("h-2 w-2 rounded-full", DOT[level])} aria-hidden />
        {LABEL[level]}
      </span>
    );
  }

  return (
    <section
      aria-label="Site readiness"
      className={cn("hairline rounded-lg p-3", className)}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className={cn("h-2.5 w-2.5 rounded-full", DOT[level])} aria-hidden />
        <span className={cn("text-sm font-semibold", TEXT[level])}>
          {LABEL[level]}
        </span>
      </div>
      <ul className="space-y-1">
        {data.checks.map((c) => (
          <li key={c.key} className="flex items-center gap-2 text-xs">
            <span
              className={cn("h-1.5 w-1.5 shrink-0 rounded-full", CHECK_DOT[c.status])}
              aria-hidden
            />
            <span className="text-ink-2">{c.label}</span>
            {c.detail && <span className="text-ink-3">— {c.detail}</span>}
          </li>
        ))}
      </ul>
      {data.fixes.length > 0 && (
        <div className="mt-2 border-t border-hairline pt-2">
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-3">
            Suggested fixes
          </div>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-ink-2">
            {data.fixes.map((f, i) => (
              <li key={i}>{f}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
