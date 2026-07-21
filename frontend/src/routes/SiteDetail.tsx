import { useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Check,
  GripVertical,
  Plus,
  RotateCcw,
  Settings2,
  Trash2,
} from "lucide-react";
import { Responsive, WidthProvider, type Layouts } from "react-grid-layout";
import { toast } from "sonner";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Callout } from "@/components/ui/Callout";
import { Skeleton } from "@/components/ui/skeleton";
import { KPICard } from "@/components/KPICard";
import { SiteTemplateCard } from "@/components/SiteTemplateCard";
import { ApprovalGate } from "@/components/ApprovalGate";
import { ProfileCard } from "@/components/ProfileCard";
import { ReadinessBadge } from "@/components/ReadinessBadge";
import { WidgetPicker } from "@/components/WidgetPicker";
import {
  useDashboardLayout,
  LEGACY_WIDGET_IDS,
  kpiLayoutId,
} from "@/hooks/useDashboardLayout";
import { useWidgetData } from "@/hooks/useWidgetData";
import { useWidgetSelection } from "@/hooks/useWidgetSelection";
import { apiDelete, apiGet } from "@/lib/api-client";
import { WIDGETS_BY_ID } from "@/lib/widgetCatalog";
import type { SiteEntryV2, SitesV2, TemplateStatus } from "@/lib/api-types";
import { SiteTemplateBadge } from "@/components/SiteTemplateBadge";
import { cn } from "@/lib/utils";

// B-3 — per-site widget dashboard at /sites/:siteId.
//
// Companion to Home's customizable dashboard. Same composition
// (legacy widgets + KPI catalog widgets, edit mode, drag/resize via
// react-grid-layout) but every persistence channel is scoped to the
// site:
//
//   - layout      → localStorage `bd-dashboard-layout-${siteId}`
//                   (via useDashboardLayout(extraIds, siteId))
//   - selection   → server-side per-site override
//                   /api/widgets/<siteId> (via useWidgetSelection(siteId))
//
// Negative isolation is a tested contract (b3_site_detail.spec.ts):
// touching the per-site layout MUST NOT modify the global
// `bd-dashboard-layout` key. The layout hook handles this — every
// localStorage write/remove on this route goes to the scoped key.
//
// Not-found state: if the site_id from the URL doesn't appear in
// /api/sites/v2, render a role="alert" Card with the "Site not
// found" message plus a "Back to sites" recovery link. We choose
// this over a 404 redirect so a stale URL (deleted site) still
// gives the operator a clear recovery affordance instead of
// silently bouncing them back to /sites.
//
// Legacy widgets render with empty/null shapes here (we don't pull
// site-scoped versions of AttentionBanner/TodayStatsCard/etc — those
// are global). The per-site value-add for this iteration is the KPI
// catalog widgets, which the operator can tune per site. Legacy tiles
// are kept in the grid so the layout shape matches Home (less code
// drift, simpler reconciler) but render compact placeholder content.

const ResponsiveGridLayout = WidthProvider(Responsive);

const BREAKPOINTS = { lg: 768, sm: 0 };
const COLS = { lg: 12, sm: 12 };

export function SiteDetail() {
  const { siteId = "" } = useParams<{ siteId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();

  // We don't have a dedicated /api/sites/v2/{id} endpoint that
  // returns just one site; the cheapest route is to look up the list
  // (already cached when arriving from /sites) and find the entry.
  const { data, isLoading, isError } = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
    refetchOnWindowFocus: false,
  });

  const site: SiteEntryV2 | undefined = useMemo(
    () => data?.sites?.find((s) => s.site_id === siteId),
    [data, siteId],
  );

  // Cut 6.7 — at-a-glance template badge in the header. Reuses the same query
  // key as SiteTemplateCard so this adds no extra fetch (react-query dedupes).
  const { data: tplStatus } = useQuery<TemplateStatus>({
    queryKey: ["template-status", siteId],
    queryFn: ({ signal }) =>
      apiGet<TemplateStatus>(
        `/api/sites/${encodeURIComponent(siteId)}/template_status`,
        signal,
      ),
    enabled: !!siteId,
    refetchOnWindowFocus: false,
  });
  const templateBadgeName = tplStatus?.template?.enabled
    ? tplStatus.label || tplStatus.template.host || "reviewed template"
    : null;

  const widgetSelection = useWidgetSelection(siteId);
  const { layouts, setLayouts, reset } = useDashboardLayout(
    widgetSelection.ids,
    siteId,
  );
  const [editMode, setEditMode] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const { data: kpiData } = useWidgetData();

  // Delete: actual mutation lives here, but the e2e only asserts the
  // affordance is present and enabled. We don't auto-confirm on click
  // (delete is destructive); operator gets a sonner toast with an
  // explicit Delete action button, same UX shape as the Sites
  // row-level delete path.
  const deleteMut = useMutation<unknown, Error, { site: SiteEntryV2 }>({
    mutationFn: ({ site: s }) =>
      // v3.66.208: was a raw fetch() DELETE with no X-CSRF-Token — 403'd
      // on any cookie-session deployment. apiDelete carries the token.
      apiDelete<unknown>(`/api/sites/${encodeURIComponent(s.site_id)}`),
    onSuccess: (_, vars) => {
      toast.success(`Deleted ${vars.site.name}`);
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
      // Per the B-3 spec: delete navigates back to /sites on success.
      navigate("/sites");
    },
    onError: (err, vars) => {
      toast.error(`Couldn't delete ${vars.site.name}: ${err.message}`);
    },
  });

  const onLayoutChange = (_current: unknown, allLayouts: Layouts) => {
    // Mirrors Home: only persist while in edit mode so the initial
    // mount's computed positions don't overwrite stored state.
    if (!editMode) return;
    setLayouts(allLayouts);
  };

  // ─── Not found state ─────────────────────────────────────────────
  // We render not-found IF the sites list has loaded successfully and
  // the requested site_id isn't in it. While the list is loading we
  // render the loading skeleton (premature not-found would flash on
  // every navigation). On API error we render an error card.
  if (!isLoading && data && !site) {
    return (
      <AppShell title="Site not found">
        <Card
          className="rounded-lg border border-amber/30 bg-amber-soft p-4 text-sm text-ink"
          role="alert"
        >
          <div className="mb-1 flex items-center gap-2">
            <span className="eyebrow eyebrow-warn">
              Site not found
            </span>
          </div>
          <div className="text-ink-3">
            We couldn't find a site with that ID. It may have been deleted.
          </div>
          <div className="mt-3">
            <Link
              to="/sites"
              className="inline-flex items-center gap-1.5 rounded-md bg-ink px-3 py-1.5 text-xs font-semibold text-surface transition-opacity hover:opacity-90"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              Back to sites
            </Link>
          </div>
        </Card>
      </AppShell>
    );
  }

  // ─── Loading or API error ────────────────────────────────────────
  if (isError) {
    return (
      <AppShell title="Site">
        <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
          Couldn't load site data.
        </Card>
      </AppShell>
    );
  }

  if (isLoading || !site) {
    return (
      <AppShell title="…">
        <div className="space-y-3">
          <Skeleton className="h-8 w-1/3" />
          <Skeleton className="h-24 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </AppShell>
    );
  }

  // ─── Rendered content ────────────────────────────────────────────
  // Build the union of tiles: legacy 5 + selected KPI widgets. Legacy
  // tiles render compact placeholders here (the per-site dashboard's
  // value-add is the KPI catalog, not the global legacy widgets).
  const widgets = buildWidgets(editMode, widgetSelection.ids, kpiData);

  return (
    <AppShell
      title={site.name}
      backTo={{ to: "/sites", label: "Back to sites" }}
      breadcrumb={`Sites › ${site.name}`}
      trailing={
        <div className="flex items-center gap-1.5">
          <SiteTemplateBadge templateName={templateBadgeName} />
          {editMode && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setPickerOpen(true)}
                aria-label="Open widget library for this site"
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                Add widget
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  reset();
                  widgetSelection.reset();
                }}
                // Reset is always available in edit mode on a per-site
                // dashboard. Both underlying operations are idempotent:
                // the layout removeItem is a no-op if the scoped key
                // wasn't there, and the widget-selection reset DELETEs
                // the per-site override (server returns success even
                // when no override existed). We don't have a clean
                // "is anything customized?" signal that combines both
                // signals without changing useWidgetSelection's
                // contract, and exposing a useless-but-harmless button
                // beats the hook surface change for this iteration.
                aria-label="Reset this site's dashboard"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                Reset
              </Button>
            </>
          )}
          <Link
            to={`/sites/${siteId}/settings`}
            className="inline-flex h-8 items-center rounded-md border border-input bg-background px-3 text-sm font-medium hover:bg-accent"
            aria-label="Edit this site's settings"
          >
            Settings
          </Link>
          <Button
            size="sm"
            variant={editMode ? "default" : "outline"}
            onClick={() => setEditMode((v) => !v)}
            aria-label={editMode ? "Exit dashboard edit mode" : "Customize dashboard"}
            aria-pressed={editMode}
          >
            {editMode ? (
              <>
                <Check className="h-3.5 w-3.5" aria-hidden />
                Done
              </>
            ) : (
              <>
                <Settings2 className="h-3.5 w-3.5" aria-hidden />
                Edit
              </>
            )}
          </Button>
        </div>
      }
    >
      <div className="space-y-3">
        {/* In-content back link — visible regardless of edit mode. */}
        <div className="flex items-center justify-between">
          <Link
            to="/sites"
            className="inline-flex items-center gap-1.5 text-xs text-ink-3 hover:text-ink hover:underline"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
            Back to sites
          </Link>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => promptDelete(site, deleteMut.mutate)}
            disabled={deleteMut.isPending}
            aria-label={`Delete ${site.name}`}
            className="text-red hover:bg-red-soft hover:text-red"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden />
            Delete
          </Button>
        </div>

        {/* Cut 4: composite readiness for this site (green/amber/red + fixes). */}
        <ReadinessBadge siteId={siteId} expanded />

        {/* Slice helper card (UI convergence #4) — what this page shows +
            the next safe action. References only existing routes/controls. */}
        <Callout tone="info" title="What this page shows">
          A per-site overview: reviewed-template status, stored profile, recent
          activity, and the dashboard widgets you choose. Use{" "}
          <span className="text-ink">Edit</span> to customize widgets, the
          <span className="text-ink"> Settings</span> tab to change per-site
          options, or <span className="text-ink">Inspect</span> to dry-run a
          template against this site before anything is applied.
        </Callout>

        {/* T11 (v3.66.264) — per-site approval gate. Renders only when a
            deep_detect run surfaced a pending challenge-marked candidate;
            nothing auto-submits until the operator approves it here. */}
        <ApprovalGate siteId={siteId} />

        {/* v3.66.144 — reviewed-template status + manual onboarding. */}
        <SiteTemplateCard siteId={siteId} />

        {/* v3.66.149 — local profile storage status + worker seed. */}
        <ProfileCard siteId={siteId} />

        {/* F3.4 — advisory learned honeypot drop-threshold. Surfacing
            only; rendered only when the site has enough trap evidence.
            Does NOT change drop behaviour (still the opt-in env path). */}
        {site.honeypot_threshold_suggested != null && (
          <div className="flex items-center justify-between rounded-md border bg-surface px-3 py-2 hairline">
            <span className="text-sm text-ink">Suggested honeypot drop threshold</span>
            <span className="text-xs text-ink-3">
              {site.honeypot_threshold_suggested.toFixed(2)} · from{" "}
              {site.honeypot_threshold_samples ?? 0} trap(s) · advisory
            </span>
          </div>
        )}

        {/* v3.66.149 — dry-run candidate / template inspector. */}
        <Link
          to={`/sites/${encodeURIComponent(siteId)}/inspect`}
          className="flex items-center justify-between rounded-md border bg-surface px-3 py-2 hairline hover:bg-surface-2"
        >
          <span className="text-sm text-ink">Candidate inspector</span>
          <span className="text-xs text-ink-3">dry-run →</span>
        </Link>

        {/* v3.66.200 nav consolidation — SiteActions / SitePayloadActions
            shipped in the parity waves with no inbound link (typed-URL
            only). See docs/NAV_CONSOLIDATION.md. */}
        <Link
          to={`/sites/${encodeURIComponent(siteId)}/actions`}
          className="flex items-center justify-between rounded-md border bg-surface px-3 py-2 hairline hover:bg-surface-2"
        >
          <span className="text-sm text-ink">Site actions</span>
          <span className="text-xs text-ink-3">gated →</span>
        </Link>
        <Link
          to={`/sites/${encodeURIComponent(siteId)}/payload-actions`}
          className="flex items-center justify-between rounded-md border bg-surface px-3 py-2 hairline hover:bg-surface-2"
        >
          <span className="text-sm text-ink">Payload actions</span>
          <span className="text-xs text-ink-3">gated →</span>
        </Link>

        <div className={cn("dashboard-grid", editMode && "dashboard-grid-edit")}>
          <ResponsiveGridLayout
            layouts={layouts}
            breakpoints={BREAKPOINTS}
            cols={COLS}
            rowHeight={30}
            margin={[12, 12]}
            containerPadding={[0, 0]}
            isDraggable={editMode}
            isResizable={editMode}
            draggableHandle=".dashboard-tile-handle"
            onLayoutChange={onLayoutChange}
            compactType="vertical"
            preventCollision={false}
          >
            {widgets}
          </ResponsiveGridLayout>
        </div>
      </div>

      <WidgetPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        siteId={siteId}
      />
    </AppShell>
  );
}

// ─── Helpers ─────────────────────────────────────────────────────────

function promptDelete(
  site: SiteEntryV2,
  mutate: (vars: { site: SiteEntryV2 }) => void,
): void {
  // Confirmation toast — same shape as Sites row delete. The button
  // itself is enabled (the e2e only checks for that), but a tap goes
  // through the confirmation, not straight to DELETE.
  toast(`Delete ${site.name}?`, {
    action: {
      label: "Delete",
      onClick: () => mutate({ site }),
    },
    cancel: {
      label: "Keep",
      onClick: () => {},
    },
    duration: 6000,
  });
}

function buildWidgets(
  editMode: boolean,
  extraIds: string[],
  kpiData: import("@/lib/widgetCatalog").WidgetData,
) {
  // Legacy placeholders. On a per-site dashboard we don't try to
  // recompute attention/today/throughput/now-running/by-site scoped
  // to the site here — those need site-filtered server endpoints we
  // haven't added yet. We render an empty-shaped tile so the grid
  // layout has a stable child per slot.
  const legacyTiles = LEGACY_WIDGET_IDS.map((id) => (
    <div key={id} className="dashboard-tile-wrap h-full">
      <DashboardTile id={id} editMode={editMode}>
        <Card className="hairline grid h-full place-items-center border bg-surface p-3 text-center text-xs text-ink-3">
          <span>{labelForLegacy(id)}</span>
        </Card>
      </DashboardTile>
    </div>
  ));

  const kpiTiles = extraIds
    .filter((id) => WIDGETS_BY_ID[id] !== undefined)
    .map((id) => {
      const def = WIDGETS_BY_ID[id];
      const spec = def.spec(kpiData);
      const layoutId = kpiLayoutId(id);
      return (
        <div key={layoutId} className="dashboard-tile-wrap h-full">
          <DashboardTile id={layoutId} editMode={editMode}>
            <div className="hairline h-full overflow-hidden rounded-md border bg-surface">
              <KPICard spec={spec} />
            </div>
          </DashboardTile>
        </div>
      );
    });

  return [...legacyTiles, ...kpiTiles];
}

function labelForLegacy(id: string): string {
  switch (id) {
    case "attention": return "Site alerts";
    case "today": return "Today's stats";
    case "throughput": return "Throughput";
    case "now-running": return "Active jobs";
    case "by-site": return "Site overview";
    default: return id;
  }
}

function DashboardTile({
  id,
  editMode,
  children,
}: {
  id: string;
  editMode: boolean;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "relative h-full overflow-hidden",
        editMode &&
          "rounded-md ring-2 ring-dashed ring-primary/40 transition-shadow",
      )}
    >
      {editMode && (
        <div
          className="dashboard-tile-handle absolute right-1 top-1 z-10 grid h-6 w-6 cursor-grab place-items-center rounded-sm bg-surface/90 text-ink-3 backdrop-blur transition-colors hover:bg-surface-2 hover:text-ink active:cursor-grabbing"
          aria-label={`Drag to reorder ${id} tile`}
        >
          <GripVertical className="h-3.5 w-3.5" aria-hidden />
        </div>
      )}
      {children}
    </div>
  );
}
