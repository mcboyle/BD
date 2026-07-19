import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity as ActivityIcon, Download, Search, X } from "lucide-react";

import { ActivityRow as ActivityRowComponent } from "@/components/ActivityRow";
import { AppShell } from "@/components/AppShell";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { DensityToggle } from "@/components/ui/DensityToggle";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/input";
import { SkeletonRows } from "@/components/ui/SkeletonRows";
import { SortSelect } from "@/components/ui/SortSelect";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useDensity } from "@/hooks/useDensity";
import { useTableSort } from "@/hooks/useTableSort";
import { useSiteHealth } from "@/hooks/useSiteHealth";
import { apiGet } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import type { ActivityV2, ActivityWindow } from "@/lib/api-types";
import { formatCount, formatDelta } from "@/lib/format";
import { cn } from "@/lib/utils";

// Activity tab. Composition (mockup activity.png):
//   - Header trailing slot: Export CSV button
//   - Window filter tabs: 24h / 7d / 30d / All
//   - Debounced search input
//   - Header card: large count + delta vs previous period
//   - List of ActivityRow

const WINDOWS: ReadonlyArray<{ value: ActivityWindow; label: string }> = [
  { value: "24h", label: "24h" },
  { value: "7d",  label: "7d"  },
  { value: "30d", label: "30d" },
  { value: "all", label: "All" },
];

// P6-1 — sort fields for the Activity feed (a non-tabular <ul>).
const ACT_SORT_OPTS = [
  { key: "when", label: "When" },
  { key: "site", label: "Site" },
  { key: "status", label: "Status" },
  { key: "size", label: "Size" },
];

export function Activity() {
  const [windowVal, setWindowVal] = useState<ActivityWindow>("7d");
  const [query, setQuery] = useState("");
  const debouncedQuery = useDebouncedValue(query, 300);
  const { isCompact } = useDensity();

  // v3.64.x D3 follow-up (U6): per-site daily completion counts for
  // the inline sparkline. One fetch for the whole Activity tab —
  // ActivityRowComponent looks up its row's site_id in the returned
  // by_site map. Window is fixed at 14 days (the operator-facing
  // "last fortnight" feels like the right glance signal regardless
  // of which Activity window tab is selected); the window tab
  // governs the rendered rows, not the sparkline's lookback.
  const siteHealth = useSiteHealth(14, "*");

  const { data, isLoading, isError } = useQuery<ActivityV2>({
    queryKey: ["activity-v2", windowVal, debouncedQuery],
    queryFn: ({ signal }) => {
      const u = new URL("/api/activity/v2", window.location.origin);
      u.searchParams.set("window", windowVal);
      if (debouncedQuery.trim()) u.searchParams.set("q", debouncedQuery.trim());
      return apiGet<ActivityV2>(u.pathname + u.search, signal);
    },
    refetchInterval: (q) =>
      adaptiveInterval<ActivityV2>({
        query: q,
        // Activity refreshes faster when something might be happening
        // (count > 0 in 24h window) and slower when historical.
        isBusy: (d) =>
          (d.window === "24h" && (d.count_current_period ?? 0) > 0),
        fast: 10_000,
        slow: 60_000,
      }),
  });

  const exportCsvUrl = useMemo(() => {
    const u = new URL("/api/activity/v2/export.csv", window.location.origin);
    u.searchParams.set("window", windowVal);
    if (debouncedQuery.trim()) u.searchParams.set("q", debouncedQuery.trim());
    return u.pathname + u.search;
  }, [windowVal, debouncedQuery]);

  // P6-1 — client-side sort over the fetched activity page (the visible cap).
  const items = data?.items ?? [];
  const itemsSort = useTableSort(items, {
    accessors: {
      when: (r) => r.ts,
      site: (r) => r.site_name,
      status: (r) => r.status,
      size: (r) => r.file_size,
    },
  });

  return (
    <AppShell
      title="Activity"
      trailing={
        <Button
          asChild
          size="sm"
          variant="outline"
          aria-label="Export CSV"
        >
          {/* Plain anchor with download attr — the browser navigates
           * to the CSV endpoint, server's Content-Disposition saves
           * the file. No fetch+blob plumbing needed. */}
          <a href={exportCsvUrl} download>
            <Download className="h-3.5 w-3.5" aria-hidden />
            CSV
          </a>
        </Button>
      }
    >
      <div className="space-y-3">
        <Callout tone="info" title="How activity is created">
          Activity appears after captures, queue runs, imports, audits, or operator actions.
        </Callout>
        {/* V5 — window filter strip.
         *
         * Pre-V5 used the Tabs primitive. V5 swaps to StatusPill
         * asToggle pattern for consistency with V3 Sites — these are
         * filter buttons, not navigation tabs, and the role=tablist
         * semantic was without paired tabpanels (the Activity tab
         * has one panel that renders different rows based on window
         * selection, not four sibling panels).
         */}
        <div
          role="group"
          aria-label="Activity time window"
          className="flex flex-wrap items-center gap-1.5"
        >
          {WINDOWS.map((w) => (
            <StatusPill
              key={w.value}
              asToggle
              size="lg"
              tone="neutral"
              selected={windowVal === w.value}
              onClick={() => setWindowVal(w.value)}
              ariaLabel={`Time window: ${w.label}`}
            >
              {w.label}
            </StatusPill>
          ))}
        </div>

        {/* P6-1 — density + sort controls */}
        <div className="flex flex-wrap items-center gap-2">
          {data && data.items.length > 0 && (
            <SortSelect
              options={ACT_SORT_OPTS}
              sortKey={itemsSort.sortKey}
              dir={itemsSort.sortDir}
              onSet={itemsSort.setSort}
            />
          )}
          <div className="ml-auto">
            <DensityToggle />
          </div>
        </div>

        {/* Search */}
        <div className="relative">
          <Search
            className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3"
            aria-hidden
          />
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search filename, URL, or message…"
            className="pl-9"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 rounded-sm p-1 text-ink-3 hover:text-ink"
              aria-label="Clear search"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          )}
        </div>

        {/* Headline card with total + delta */}
        <Card className="p-4">
          <div className="flex items-baseline justify-between">
            <div>
              <div
                className={cn(
                  "text-3xl font-bold tabular tracking-tighter leading-none",
                  // Match V2 KPICard treatment: dim the em-dash empty
                  // state so a still-loading card recedes.
                  !data && "opacity-40",
                )}
              >
                {data ? formatCount(data.count_current_period) : "—"}
              </div>
              <div className="mt-1 eyebrow">
                {windowVal === "all" ? "Total completions" : `Last ${windowVal}`}
                {debouncedQuery && <> · matching</>}
              </div>
            </div>
            {data?.delta_abs != null && (
              <div
                className={cn(
                  "text-xs font-semibold tabular",
                  data.delta_abs > 0
                    ? "text-green"
                    : data.delta_abs < 0
                      ? "text-red"
                      : "text-ink-3",
                )}
              >
                {formatDelta(data.delta_abs, data.delta_pct)}
              </div>
            )}
          </div>
        </Card>

        {isError && (
          <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
            Couldn't load activity.
          </Card>
        )}
        {isLoading && !data && (
          <SkeletonRows count={6} rowClassName="h-14" />
        )}
        {data && data.items.length === 0 && !isLoading && (
          <EmptyState
            icon={ActivityIcon}
            title={debouncedQuery ? "No matches for that search." : "Nothing yet for this window."}
            hint={
              debouncedQuery
                ? "Try a different term or widen the time window."
                : "Activity from your captures and jobs will appear here."
            }
          />
        )}
        {data && data.items.length > 0 && (
          <ul className={isCompact ? "space-y-0.5" : "space-y-1.5"}>
            {itemsSort.sorted.map((r) => (
              <li key={r.id}>
                <ActivityRowComponent
                  row={r}
                  compact={isCompact}
                  sparkline={
                    // Only feed a sparkline once site_health has loaded.
                    // Before then we omit the prop entirely so the slot
                    // doesn't render (avoids a flash of empty baselines
                    // on first paint). After load, lookup returns the
                    // per-site array OR null for sites with no activity
                    // in the 14-day window — ActivityRow renders null as
                    // a quiet baseline.
                    siteHealth.data
                      ? siteHealth.lookup(r.site_id)
                      : undefined
                  }
                />
              </li>
            ))}
            {data.count_current_period > data.items.length && (
              <li className="px-1 py-2 text-center text-[11px] text-ink-3">
                Showing {data.items.length} of {formatCount(data.count_current_period)} —
                use Export CSV for the rest.
              </li>
            )}
          </ul>
        )}
      </div>
    </AppShell>
  );
}
