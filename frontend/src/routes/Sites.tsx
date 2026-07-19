import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckSquare, Pause, Play, Plus, Search, Square, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import { AddSiteWizard } from "@/components/AddSiteWizard";
import { BulkActionBar, BulkActionButton } from "@/components/BulkActionBar";
import { SiteRow } from "@/components/SiteRow";
import { StatusPill } from "@/components/StatusPill";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useBulkSelection } from "@/hooks/useBulkSelection";
import { apiGet, apiPost } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import type { SiteEntryV2, SitesV2 } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Sites tab. Composition (matches mockup sites.png):
//   - Header with search + Add Site button (trailing slot)
//   - Filter tabs: All / Active / Paused / Issues
//   - Search input (sticky-ish — collapses if not focused & empty)
//   - List of SiteRow
//   - Add Site wizard mounted, opened by the trailing button
//
// v3.64.x D3 follow-up: bulk select. Operator taps "Select" in the
// header trailing slot to enter selection mode; rows then toggle
// selection on tap and a sticky BulkActionBar exposes Pause / Resume
// / Delete. Exiting selection mode (X on the bar) clears the set.
//
// B-3 (post-v3.64.5): row tap (outside selection mode) navigates to
// /sites/:siteId (the per-site widget dashboard). The single-site
// Delete affordance moves to SiteDetail; the row-tap toast that used
// to surface it is gone. Bulk delete from the BulkActionBar is
// unchanged.
//
// State: filter + search query are URL-free for U4 (sticky-by-default
// per the design narrative). Selection mode is also URL-free — it's
// an in-page mode toggle, not navigation.

type Filter = "all" | "active" | "paused" | "issues";

function filterMatches(filter: Filter, site: SiteEntryV2): boolean {
  if (filter === "all") return true;
  if (filter === "active") return site.state === "running";
  if (filter === "paused") return site.state !== "running" && !siteHasIssue(site);
  if (filter === "issues") return siteHasIssue(site);
  return true;
}

function siteHasIssue(site: SiteEntryV2): boolean {
  return site.captcha_pending || site.auth_state === "expired";
}

export function Sites() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [wizardOpen, setWizardOpen] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);

  const { data, isLoading, isError } = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
    refetchInterval: (q) =>
      adaptiveInterval<SitesV2>({
        query: q,
        isBusy: (d) =>
          (d.sites?.some((s) => s.captcha_pending || s.auth_state === "expired") ??
            false) ||
          (d.sites?.some((s) => s.state === "running") ?? false),
        fast: 4000,
        slow: 15_000,
      }),
  });

  // B-3: row tap navigates to the per-site dashboard. Single-site
  // delete now lives on SiteDetail (Trash2 button next to the back
  // link). Bulk delete still works from selection mode.
  const handleRowClick = (site: SiteEntryV2) => {
    navigate(`/sites/${encodeURIComponent(site.site_id)}`);
  };

  const allSites = data?.sites ?? [];
  const filtered = allSites
    .filter((s) => filterMatches(filter, s))
    .filter((s) =>
      query.trim()
        ? s.name.toLowerCase().includes(query.trim().toLowerCase())
        : true,
    );

  const visibleIds = filtered.map((s) => s.site_id);
  const selection = useBulkSelection(visibleIds);

  // Prune dropped sites from the selection whenever the data refreshes
  // (a site might have been deleted server-side or filtered out of
  // the visible list — acting on a stale selection would 404).
  useEffect(() => {
    selection.pruneTo(visibleIds);
    // We intentionally omit `selection` from deps — only the visible
    // list changing should trigger a prune.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleIds.join(",")]);

  const exitSelectionMode = () => {
    selection.clear();
    setSelectionMode(false);
  };

  // Bulk action mutation. Single mutation for all four actions —
  // the server distinguishes by `action` in the body.
  type BulkResponse = {
    ok: boolean;
    applied_to: number;
    total: number;
    errors: { site_id: string; error: string }[];
  };
  const bulkMut = useMutation<BulkResponse, Error, { action: string }>({
    mutationFn: ({ action }) =>
      apiPost<BulkResponse>("/api/sites/v2/bulk", {
        action,
        site_ids: selection.ids,
      }),
    onSuccess: (res, vars) => {
      const verb =
        vars.action === "delete" ? "Deleted" :
        vars.action === "pause" ? "Paused" :
        vars.action === "resume" ? "Resumed" :
        vars.action === "start" ? "Started" : "Updated";
      if (res.applied_to === res.total && res.errors.length === 0) {
        toast.success(`${verb} ${res.applied_to} site${res.applied_to === 1 ? "" : "s"}`);
      } else {
        toast.warning(
          `${verb} ${res.applied_to} of ${res.total}; ${res.errors.length} failed`,
        );
      }
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      exitSelectionMode();
    },
    onError: (err) => {
      toast.error(`Bulk action failed: ${err.message}`);
    },
  });

  const promptBulkDelete = () => {
    const n = selection.size;
    if (n === 0) return;
    toast(`Delete ${n} site${n === 1 ? "" : "s"}?`, {
      action: {
        label: "Delete",
        onClick: () => bulkMut.mutate({ action: "delete" }),
      },
      cancel: { label: "Keep", onClick: () => {} },
      duration: 6000,
    });
  };

  const counts = {
    all: allSites.length,
    active: allSites.filter((s) => s.state === "running").length,
    paused: allSites.filter((s) => s.state !== "running" && !siteHasIssue(s)).length,
    issues: allSites.filter(siteHasIssue).length,
  };

  return (
    <AppShell
      title="Sites"
      trailing={
        selectionMode ? (
          <Button
            size="sm"
            variant="ghost"
            onClick={exitSelectionMode}
            aria-label="Exit selection mode"
          >
            <X className="h-4 w-4" aria-hidden />
            Done
          </Button>
        ) : (
          <div className="flex items-center gap-1.5">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setSelectionMode(true)}
              aria-label="Select sites for bulk actions"
              disabled={allSites.length === 0}
            >
              <CheckSquare className="h-4 w-4" aria-hidden />
              Select
            </Button>
            <Button
              size="sm"
              onClick={() => setWizardOpen(true)}
              aria-label="Add site"
            >
              <Plus className="h-4 w-4" aria-hidden />
              Add
            </Button>
          </div>
        )
      }
    >
      <div className="space-y-3">
        {/* V3 — filter strip + search.
         *
         * Pre-V3 used the Tabs primitive (TabsList/TabsTrigger) which
         * renders semantic role=tablist. V3 swaps to a StatusPill
         * toggle row — these are filter buttons, not navigation tabs,
         * and rendering them as role=tablist was a minor a11y lie
         * (the panels they "selected" weren't paired tabpanel
         * elements). Each pill is an asToggle StatusPill that
         * reports aria-pressed for state.
         *
         * The pill emphasis: "Issues" goes amber when there are open
         * issues (mockup signature), all others use the dark-ink
         * selected treatment.
         */}
        <div className="space-y-2">
          <div
            role="group"
            aria-label="Filter sites"
            className="flex flex-wrap items-center gap-1.5"
          >
            <FilterPill
              label="All"
              count={counts.all}
              selected={filter === "all"}
              onClick={() => setFilter("all")}
            />
            <FilterPill
              label="Active"
              count={counts.active}
              selected={filter === "active"}
              onClick={() => setFilter("active")}
            />
            <FilterPill
              label="Paused"
              count={counts.paused}
              selected={filter === "paused"}
              onClick={() => setFilter("paused")}
            />
            <FilterPill
              label="Issues"
              count={counts.issues}
              selected={filter === "issues"}
              onClick={() => setFilter("issues")}
              tone={counts.issues > 0 ? "amber" : "neutral"}
            />
          </div>
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3"
              aria-hidden
            />
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search sites…"
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
          {selectionMode && filtered.length > 0 && (
            <div className="flex items-center justify-between px-1 text-xs text-ink-3">
              <button
                type="button"
                onClick={() =>
                  selection.isAllSelected ? selection.clear() : selection.selectAll()
                }
                className="flex items-center gap-1.5 rounded-sm py-1 text-primary hover:underline"
              >
                {selection.isAllSelected ? (
                  <Square className="h-3.5 w-3.5" aria-hidden />
                ) : (
                  <CheckSquare className="h-3.5 w-3.5" aria-hidden />
                )}
                {selection.isAllSelected ? "Deselect all" : "Select all visible"}
              </button>
              <span className="tabular-nums">
                {selection.size} / {filtered.length}
              </span>
            </div>
          )}
        </div>

        {/* Error / loading / empty / list */}
        {isError && (
          <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
            Couldn’t load sites.
          </Card>
        )}
        {isLoading && !data && (
          <>
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </>
        )}
        {!isLoading && filtered.length === 0 &&
          (allSites.length === 0 ? (
            <EmptyState
              title="No sites yet"
              hint="Add your first site to start a guided capture workflow."
              action={{
                label: "Add site",
                onClick: () => setWizardOpen(true),
                variant: "default",
              }}
            />
          ) : (
            <EmptyState
              icon={Search}
              title="No sites match the current filter"
              hint="Try a different tab above, or clear the search to see all sites."
            />
          ))}
        {filtered.length > 0 && (
          <ul className="space-y-2">
            {filtered.map((site) => (
              <li key={site.site_id}>
                <SiteRow
                  site={site}
                  onClick={handleRowClick}
                  selected={selectionMode ? selection.has(site.site_id) : false}
                  onToggleSelect={
                    selectionMode
                      ? (s) => selection.toggle(s.site_id)
                      : undefined
                  }
                />
              </li>
            ))}
          </ul>
        )}

        {/* V3 — "Add another site" dashed footer card. Appears under
         * the list when there's at least one site visible (the empty-
         * state has its own CTA, no need to double up). Subtle dashed
         * border so it doesn't compete with real rows for attention;
         * tapping anywhere opens the wizard. Hidden during selection
         * mode — adding while selecting would be confusing. */}
        {!selectionMode && allSites.length > 0 && (
          <button
            type="button"
            onClick={() => setWizardOpen(true)}
            aria-label="Add another site"
            className={cn(
              "w-full rounded-md border border-dashed border-ink-3/30 p-4",
              "flex flex-col items-center justify-center gap-1",
              "text-ink-3 transition-colors",
              "hover:border-primary/50 hover:bg-surface hover:text-ink-2",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
            )}
          >
            <div className="flex items-center gap-1.5 text-sm font-medium">
              <Plus className="h-4 w-4" aria-hidden />
              Add another site
            </div>
            <div className="text-[11px] text-ink-3">
              Quick Setup · Templates · Full editor
            </div>
          </button>
        )}

        <BulkActionBar
          count={selection.size}
          onClear={exitSelectionMode}
          actions={
            <>
              <BulkActionButton
                onClick={() => bulkMut.mutate({ action: "pause" })}
                disabled={bulkMut.isPending}
                ariaLabel="Pause selected sites"
              >
                <Pause className="h-3.5 w-3.5" aria-hidden />
                Pause
              </BulkActionButton>
              <BulkActionButton
                onClick={() => bulkMut.mutate({ action: "resume" })}
                disabled={bulkMut.isPending}
                ariaLabel="Resume selected sites"
              >
                <Play className="h-3.5 w-3.5" aria-hidden />
                Resume
              </BulkActionButton>
              <BulkActionButton
                onClick={promptBulkDelete}
                disabled={bulkMut.isPending}
                variant="destructive"
                ariaLabel="Delete selected sites"
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                Delete
              </BulkActionButton>
            </>
          }
        />
      </div>

      <AddSiteWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </AppShell>
  );
}

function FilterPill({
  label,
  count,
  selected,
  onClick,
  tone = "neutral",
}: {
  label: string;
  count: number;
  selected: boolean;
  onClick: () => void;
  /** When selected=false AND the filter has emphasis (e.g. Issues
   *  with count > 0), the pill picks up that tone. Selected always
   *  wins and uses the dark-ink treatment. */
  tone?: "neutral" | "amber";
}) {
  // V3 — filter pill. Uses StatusPill in asToggle mode so the
  // aria-pressed semantics come from the primitive. When selected,
  // StatusPill ignores the `tone` prop and uses the dark-ink
  // treatment (primary inverse). When not selected, the tone is
  // applied — "Issues" pill goes amber when there are open issues,
  // even when not the active filter, so the user sees the warning
  // signal in the row of pills.
  return (
    <StatusPill
      asToggle
      size="lg"
      tone={tone === "amber" ? "amber" : "neutral"}
      selected={selected}
      onClick={onClick}
      ariaLabel={`Filter: ${label} (${count})`}
    >
      <span>{label}</span>
      {count > 0 && (
        <span className="ml-1.5 tabular-nums opacity-70">{count}</span>
      )}
    </StatusPill>
  );
}
