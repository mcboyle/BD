import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, CheckCircle2, GripVertical, ListPlus, Plus, RotateCcw, Settings2, Wand2 } from "lucide-react";
import { Responsive, WidthProvider, type Layouts } from "react-grid-layout";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

import { AddSiteWizard } from "@/components/AddSiteWizard";
import { AppShell } from "@/components/AppShell";
import { AttentionBanner } from "@/components/AttentionBanner";
import { BySiteList } from "@/components/BySiteList";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/EmptyState";
import { KPICard } from "@/components/KPICard";
import { NowRunningList } from "@/components/NowRunningList";
import { NeedsAttentionRollup } from "@/components/NeedsAttentionRollup";
import { StatusPill } from "@/components/StatusPill";
import { ThroughputSparkline } from "@/components/ThroughputSparkline";
import { TodayStatsCard } from "@/components/TodayStatsCard";
import { WidgetPicker } from "@/components/WidgetPicker";
import {
  useDashboardLayout,
  LEGACY_WIDGET_IDS,
  kpiLayoutId,
  type WidgetId,
} from "@/hooks/useDashboardLayout";
import { useWidgetData } from "@/hooks/useWidgetData";
import { useWidgetSelection } from "@/hooks/useWidgetSelection";
import { apiGet } from "@/lib/api-client";
import { adaptiveInterval } from "@/lib/polling";
import { WIDGETS_BY_ID } from "@/lib/widgetCatalog";
import type { DashboardV2 } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// Home / Dashboard tab.
//
// Composition (matches mockup 04_sleek_dashboard.png):
//   - AttentionBanner (only if attention.length > 0)
//   - TodayStatsCard
//   - ThroughputSparkline
//   - NowRunningList
//   - BySiteList
//
// v3.64.x D3 follow-up (U4): react-grid-layout drives a customizable
// grid. The default mobile layout (single column) is identical to the
// pre-U4 fixed layout, so users who never tap "Edit" see no change.
// Tablet/desktop unlocks a 4-up arrangement that the user can rearrange
// + resize. Layout persists per-device to localStorage.
//
// Edit mode discipline:
//   - Default = view mode. Drag handles hidden. No accidental drags
//     while scrolling on touch devices.
//   - Edit button (trailing slot) → drag handles visible, tiles
//     get a subtle ring + grip icon, react-grid-layout enables
//     dragging (+ resizing on lg).
//   - "Reset Layout" button visible only in edit mode.
//   - "Done" exits edit mode without an explicit save — every drag
//     end persists via the layout hook.
//
// Bundle: react-grid-layout + react-resizable add ~60KB gzip on
// top of the existing bundle (per BUNDLE_AUDIT.md soft ceiling
// of 155-180KB, this pushes ~215-240KB). Accepted as the cost of
// the feature; deferred to U4 specifically because customizable
// dashboard is a low-frequency-use feature and the bundle hit is
// concentrated on a single route.

const ResponsiveGridLayout = WidthProvider(Responsive);

// Breakpoint config. Widths in px below which the named breakpoint
// applies. Two breakpoints because two real layouts: phone (sm) and
// everything else (lg). Tablets use lg with 4-col reshuffle ability.
const BREAKPOINTS = { lg: 768, sm: 0 };
const COLS = { lg: 12, sm: 12 };

export function Home() {
  // v3.64.x Unit B: KPI widget catalog. The selection lives in its
  // own localStorage key (bd-widget-selection) and is independent
  // of the layout (bd-dashboard-layout). Passing the selected IDs
  // into useDashboardLayout means the grid hook reconciles against
  // them and gives new widgets default positions on first add.
  const widgetSelection = useWidgetSelection();
  const { layouts, setLayouts, reset, isCustom } = useDashboardLayout(
    widgetSelection.ids,
  );
  const [editMode, setEditMode] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  // Slice 3: first-run "Add your first site" opens the wizard inline (same
  // self-contained dialog Sites.tsx mounts), no route hop.
  const [wizardOpen, setWizardOpen] = useState(false);
  const { data: kpiData } = useWidgetData();

  const { data, isLoading, isError, error } = useQuery<DashboardV2>({
    queryKey: ["dashboard-v2"],
    queryFn: ({ signal }) => apiGet<DashboardV2>("/api/dashboard/v2", signal),
    refetchInterval: (query) =>
      adaptiveInterval<DashboardV2>({
        query,
        isBusy: (d) =>
          (d.attention?.length ?? 0) > 0 ||
          (d.today?.running ?? 0) > 0 ||
          (d.by_site?.some((s) => s.queued > 0 || s.running > 0) ?? false),
        fast: 3000,
        slow: 12_000,
      }),
  });

  // Cut 2 — first-run onboarding gate. A GENUINELY fresh instance shows a
  // guided panel instead of the dashboard grid. "Fresh" requires BOTH no sites
  // loaded AND no run history ever — a user who cleared their sites but has
  // history is experienced, not first-run, so they keep the normal dashboard.
  // History is probed with a cheap limit=1 read of the existing /api/history
  // (bare array; [] when empty). No new backend.
  const { data: historyProbe } = useQuery<unknown[]>({
    queryKey: ["history-probe", "onboarding"],
    queryFn: ({ signal }) =>
      apiGet<unknown[]>("/api/history?limit=1", signal),
    staleTime: 60_000,
  });
  const noSites = (data?.by_site?.length ?? 0) === 0;
  const noHistory = Array.isArray(historyProbe) && historyProbe.length === 0;
  // Only claim "fresh" once both signals have resolved (data + probe present),
  // so we never flash onboarding before history is known.
  const isFreshInstance =
    !!data && historyProbe !== undefined && noSites && noHistory;

  const onLayoutChange = (_current: unknown, allLayouts: Layouts) => {
    // Only persist while in edit mode. Without this gate the initial
    // mount can fire onLayoutChange with computed positions that
    // would overwrite the stored layout with a transient state.
    if (!editMode) return;
    setLayouts(allLayouts);
  };

  // Cut 6.5 — needs-attention rollup. Aggregate existing counts into linked
  // rows above the grid (renders null when there's nothing, so a clean instance
  // shows nothing). Distinct from the per-site AttentionBanner widget below.
  const attentionRollup = useMemo(() => {
    if (!data) return [];
    const rows: { kind: string; label: string; count: number; href: string }[] = [];
    const failed = data.today?.failed ?? 0;
    if (failed > 0)
      rows.push({ kind: "failed", label: "Failed runs", count: failed, href: "/queue?status=failed" });
    const attn = data.attention?.length ?? 0;
    if (attn > 0)
      rows.push({ kind: "review", label: "Needs review", count: attn, href: "/cockpit/review" });
    return rows;
  }, [data]);

  // Build the rendered children. We render every widget slot every
  // time so react-grid-layout has stable children-to-layout matching;
  // empty conditional widgets render an empty <div /> in their slot
  // (AttentionBanner with 0 items returns null itself; we wrap it
  // here so the grid still places its tile predictably).
  //
  // v3.64.x Unit B: also renders KPI widgets from the user selection.
  // We render the union (legacy 5 + selected KPIs) so the layout grid
  // has a child for every entry it expects.
  const widgets = useMemo(
    () => data ? buildWidgets(data, editMode, widgetSelection.ids, kpiData, () => setWizardOpen(true)) : null,
    [data, editMode, widgetSelection.ids, kpiData],
  );

  // V2: derive the mockup status line + pill data from the dashboard
  // payload. Status line reads "Idle · N queued · A of B workers".
  //
  // V2.1: `/api/dashboard/v2` now emits `workers_active` (sum of
  // active_worker_count() across runners) and `workers_total` (sum
  // of max_concurrent; null on empty fleet — DANGER_MAP no-magic-16
  // contract). Pre-V2.1 backends won't emit these — both fields are
  // optional in the type, so we fall back gracefully:
  //   • workers_total = null  → empty fleet → render "{active} workers"
  //                              (the mockup's "of N" is omitted when
  //                              there's no honest N to display)
  //   • workers_total = undef → pre-V2.1 backend → same fallback
  //   • workers_total > 0     → "{active} of {total} workers"
  // We DO NOT substitute the legacy active_workers field (which counts
  // sites-with-running-state, a different metric) — that would be a
  // misleading render.
  const workersActive = data?.workers_active ?? 0;
  const workersTotal = data?.workers_total ?? null;
  // Queued count = sum of per-site queued.
  const queuedCount =
    data?.by_site?.reduce((acc, s) => acc + (s.queued ?? 0), 0) ?? 0;
  const todayRunning = data?.today?.running ?? 0;
  const todayDone = data?.today?.done ?? 0;
  const todayFailed = data?.today?.failed ?? 0;
  const isRunning = todayRunning > 0 || workersActive > 0;

  const workersStr =
    workersTotal != null && workersTotal > 0
      ? `${workersActive} of ${workersTotal} workers`
      : `${workersActive} ${workersActive === 1 ? "worker" : "workers"} active`;

  const headerStatusLine = data ? (
    <span className="tabular">
      <span className="font-medium text-ink-2">
        {isRunning ? "Running" : "Idle"}
      </span>
      {" · "}
      {queuedCount} queued
      {" · "}
      {workersStr}
    </span>
  ) : undefined;

  const headerBelowStatus = data ? (
    <>
      <StatusPill tone="amber" size="sm" leadingIcon="▸">
        {todayRunning} running
      </StatusPill>
      <StatusPill tone="green" size="sm" leadingIcon="✓">
        {todayDone} done
      </StatusPill>
      <StatusPill tone="red" size="sm" leadingIcon="✕">
        {todayFailed} failed
      </StatusPill>
    </>
  ) : undefined;

  return (
    <AppShell
      title="BulkDL"
      headerVariant="display"
      headerStatusLine={headerStatusLine}
      headerBelowStatus={headerBelowStatus}
      trailing={
        <div className="flex items-center gap-1.5">
          {editMode && (
            <>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setPickerOpen(true)}
                aria-label="Open widget library"
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
                disabled={!isCustom && widgetSelection.ids.length === 4}
                aria-label="Reset dashboard layout and widget selection to defaults"
              >
                <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                Reset
              </Button>
            </>
          )}
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
      {isError && (
        <Card className="border-red bg-red-soft p-3 text-sm text-red" role="alert">
          Couldn’t load dashboard: {(error as Error)?.message ?? "unknown error"}
        </Card>
      )}

      {!isFreshInstance && attentionRollup.length > 0 ? (
        <div className="mb-4">
          <NeedsAttentionRollup entries={attentionRollup} />
        </div>
      ) : null}

      {isLoading && !data ? (
        <DashboardSkeleton />
      ) : isFreshInstance ? (
        <FirstRunOnboarding onAddSite={() => setWizardOpen(true)} />
      ) : data && widgets ? (
        // react-grid-layout's CSS expects the container to have a known
        // width; WidthProvider injects it via ResizeObserver. The grid
        // CSS does its own margins; we strip our outer padding to let
        // the grid breathe correctly.
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
            // On mobile, even in edit mode, disable resize — touch
            // resize on a small screen is too finicky to be useful.
            // The Responsive grid passes a `breakpoint` flag; we
            // gate via draggableHandle below to keep things simple.
            draggableHandle=".dashboard-tile-handle"
            onLayoutChange={onLayoutChange}
            compactType="vertical"
            preventCollision={false}
          >
            {widgets}
          </ResponsiveGridLayout>
        </div>
      ) : null}
      <WidgetPicker open={pickerOpen} onOpenChange={setPickerOpen} />
      <AddSiteWizard open={wizardOpen} onOpenChange={setWizardOpen} />
    </AppShell>
  );
}

// Cut 2 — first-run onboarding. Shown only on a genuinely fresh instance (no
// sites + no run history). Lays out the guided three-step path: add a site ->
// run a capture -> watch the queue, using existing routes only. The add-site
// step opens the same inline AddSiteWizard the dashboard uses; the later steps
// are links (a fresh user can't do them yet, but the path is visible).
function FirstRunOnboarding({ onAddSite }: { onAddSite: () => void }) {
  return (
    <Card className="mx-auto max-w-xl p-6">
      <h2 className="text-lg font-semibold text-ink-1">
        Get started with your first capture
      </h2>
      <p className="mt-1 text-sm text-ink-3">
        Three steps to your first download. You can always change things later.
      </p>

      <ol className="mt-5 space-y-4">
        <li className="flex items-start gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-primary-soft text-xs font-semibold text-primary">
            1
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium text-ink-1">
              <Plus className="h-4 w-4 text-ink-3" aria-hidden />
              Add a site
            </div>
            <p className="mt-0.5 text-xs text-ink-3">
              Tell BulkDL where to download from.
            </p>
            <Button size="sm" className="mt-2" onClick={onAddSite}>
              Add your first site
            </Button>
          </div>
        </li>

        <li className="flex items-start gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-semibold text-ink-2">
            2
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium text-ink-1">
              <Wand2 className="h-4 w-4 text-ink-3" aria-hidden />
              Run a capture
            </div>
            <p className="mt-0.5 text-xs text-ink-3">
              Open the capture workflow to teach BulkDL the site, then queue
              downloads.{" "}
              <Link to="/capture" className="text-primary hover:underline">
                Capture workflow
              </Link>
            </p>
          </div>
        </li>

        <li className="flex items-start gap-3">
          <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-surface-2 text-xs font-semibold text-ink-2">
            3
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 text-sm font-medium text-ink-1">
              <ListPlus className="h-4 w-4 text-ink-3" aria-hidden />
              Watch the queue
            </div>
            <p className="mt-0.5 text-xs text-ink-3">
              Track progress as downloads run.{" "}
              <Link to="/queue" className="text-primary hover:underline">
                Open the queue
              </Link>
            </p>
          </div>
        </li>
      </ol>
    </Card>
  );
}

// Build the array of rendered widget elements with their data-grid keys.
// Each is wrapped in a tile so edit mode gets a uniform drag-handle UX.
//
// 1b stopgap — fills a grid cell whose widget would otherwise render
// null on an empty instance (attention with no items, by-site with no
// sites), so the dashboard has no grey holes. Slice 3 uses the shared
// EmptyState (compact) pattern; the by-site empty carries the first-run CTA.

// v3.64.x Unit B: now also renders KPI widgets from the catalog. The
// `extraIds` are appended after the legacy 5; each renders via
// `<KPICard>` driven by the catalog spec + live data envelope.
function buildWidgets(
  data: DashboardV2,
  editMode: boolean,
  extraIds: string[],
  kpiData: import("@/lib/widgetCatalog").WidgetData,
  onAddSite: () => void,
) {
  const legacyRenderers: Record<WidgetId, React.ReactNode> = {
    attention: data.attention?.length ? (
      <AttentionBanner attention={data.attention} />
    ) : (
      <EmptyState compact icon={CheckCircle2} title="All clear" hint="Nothing needs attention right now." />
    ),
    today: (
      <TodayStatsCard
        done={data.today?.done ?? 0}
        running={data.today?.running ?? 0}
        failed={data.today?.failed ?? 0}
      />
    ),
    throughput: <ThroughputSparkline />,
    "now-running": <NowRunningList />,
    "by-site": data.by_site?.length ? (
      <BySiteList bySite={data.by_site} />
    ) : (
      <EmptyState
        compact
        icon={Plus}
        title="No sites yet"
        hint="Add a site to start a capture."
        action={{ label: "Add your first site", onClick: onAddSite }}
      />
    ),
  };

  const legacyTiles = LEGACY_WIDGET_IDS.map((id) => (
    <div key={id} className="dashboard-tile-wrap h-full">
      <DashboardTile id={id} editMode={editMode}>
        {legacyRenderers[id]}
      </DashboardTile>
    </div>
  ));

  // Filter extras to catalog IDs. Layout-key collisions with legacy tiles
  // are resolved by kpiLayoutId while the selection keeps the catalog ID.
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

// Tile wrapper. In view mode the wrapper is invisible — just renders
// its children. In edit mode, overlays a grip icon (the drag handle —
// matches the class in `draggableHandle`) and a subtle dashed ring.
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
          // The handle class is what react-grid-layout watches via
          // draggableHandle=".dashboard-tile-handle". Putting it on
          // a small grip icon (not the whole tile) means tapping the
          // body of a tile during edit mode doesn't start a drag —
          // only the explicit grip does. Matches the U2 dnd-kit
          // pattern (grip on a dedicated handle, not the row body).
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

// Initial-load skeleton — three blocks to telegraph the layout
// without committing pixel-exact placement (real components nudge
// height differently once data arrives).
function DashboardSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-28 w-full" />
    </div>
  );
}
