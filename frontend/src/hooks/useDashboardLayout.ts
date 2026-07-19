import { useCallback, useEffect, useMemo, useState } from "react";
import type { Layout, Layouts } from "react-grid-layout";

import { ALL_WIDGET_IDS as KPI_WIDGET_IDS } from "@/lib/widgetCatalog";

// Dashboard layout persistence + defaults.
//
// Per-device, localStorage. Same scope reasoning as the other UI
// preferences (theme, sound, badge mode): layout is a per-device
// taste, not an account-level setting.
//
// Two responsive breakpoints:
//   - mobile (lg key, single column at <=480px effective): the default
//     a phone gets. 5 widgets stacked vertically, drag-to-reorder only
//     (no resize on a phone — fingers are too imprecise).
//   - desktop (lg key, 4 cols at 12-col grid for >=768px): tablet/desktop
//     can also resize.
//
// We persist BOTH breakpoints' layouts in one blob so the user's
// arrangement on each screen size sticks across sessions. react-grid-
// layout's onLayoutChange fires with both, so we just save what it gives.
//
// Legacy widget IDs (the original U13 5-widget set, custom React
// components, not data-driven):
//   - attention   — AttentionBanner
//   - today       — TodayStatsCard
//   - throughput  — ThroughputSparkline
//   - now-running — NowRunningList
//   - by-site     — BySiteList
//
// v3.64.x Unit B: the dashboard now also supports KPI widgets from
// the catalog at @/lib/widgetCatalog (36 widgets across 7 categories,
// data-driven via /api/widgets/data). Their IDs come from the
// catalog; the layout hook accepts any of them as valid `i` values.
// Unknown IDs are still dropped on reconcile.
//
// B-3 (post-v3.64.5): optional `siteId` argument switches the
// localStorage key from `bd-dashboard-layout` to
// `bd-dashboard-layout-${siteId}`. The scoped key NEVER touches the
// global key — writes, removes, and cross-tab events are routed by
// the resolved key only. Negative isolation is a tested contract
// (frontend/e2e/b3_site_detail.spec.ts).

export const WIDGET_IDS = [
  "attention",
  "today",
  "throughput",
  "now-running",
  "by-site",
] as const;

// v3.64.x Unit B: alias for the legacy 5-widget set, used by Home.tsx
// and the layout reconciler to distinguish "legacy custom React
// widgets" from "KPI catalog widgets." Same array — separate name so
// the intent is explicit at call sites.
export const LEGACY_WIDGET_IDS = WIDGET_IDS;

export type WidgetId = (typeof WIDGET_IDS)[number];

// B-3: the global storage key. Per-site overrides use
// `${GLOBAL_STORAGE_KEY}-${siteId}` — see resolveStorageKey().
const GLOBAL_STORAGE_KEY = "bd-dashboard-layout";

function resolveStorageKey(siteId?: string): string {
  // siteId omitted, undefined, or empty string ⇒ global key.
  // Note: callers ALWAYS read this through resolveStorageKey, so
  // every write/read/remove respects the scope.
  if (!siteId) return GLOBAL_STORAGE_KEY;
  return `${GLOBAL_STORAGE_KEY}-${siteId}`;
}

// Default heights (in row units, where one row = 30px in our grid
// config — see Home.tsx). Tuned to match the natural rendered
// heights so the initial paint doesn't shift.
//
// V2 (D3 visual redesign): heights tuned to the new component
// shapes. AttentionBanner is taller now (hero card with full-width
// Resolve button), TodayStatsCard is slightly taller (bigger numbers).
const DEFAULT_HEIGHTS: Record<WidgetId, number> = {
  attention: 3,
  today: 4,
  throughput: 4,
  "now-running": 6,
  "by-site": 6,
};

// KPI widgets render compact (single metric). On desktop they tile
// 4-up (w3 across the 12-col grid); on mobile 2-up (w6) — see the
// kpiW arg threaded into reconcile().
const KPI_DEFAULT_HEIGHT = 3;
const KPI_DEFAULT_WIDTH_LG = 3;


function defaultMobileLayout(): Layout[] {
  // V2 — mockup reading order on phone (home.png, 04_sleek_dashboard.png):
  //
  //   1. Attention banner (hero, full width)
  //   2. Today tri-stat (full width, the ACTIVITY card)
  //   3. Throughput last hour (full width)
  //   4. Now running (full width, below the fold)
  //   5. By-site (full width, below the fold)
  //
  // KPI widgets (queue_depth, disk_free, etc.) appended below the
  // legacy 5 by the reconcile() function based on extraIds. Their
  // default layout is a 2-up arrangement on phone for the standard
  // KPI cards (queue+disk pair sits between throughput and now-running
  // in the mockup; reconcile + the user's KPI selection determines
  // actual position, but the default positions in reconcile() are
  // tuned to land them roughly where the mockup expects).
  let y = 0;
  return WIDGET_IDS.map((id) => {
    const h = DEFAULT_HEIGHTS[id];
    const item: Layout = {
      i: id,
      x: 0,
      y,
      w: 12,
      h,
      // Mobile: drag only; resize forced off via isResizable=false
      // in the GridLayout config below.
    };
    y += h;
    return item;
  });
}

function defaultDesktopLayout(): Layout[] {
  // V2 — mockup desktop reading order (mockup 6/8 desktop screenshot):
  //
  //   Row 0  (y 0):  attention banner — full width (h=5)
  //   Row 1  (y 5):  today tri-stat (1/2, h=4)  |  throughput (1/2, h=5)
  //   Row 2  (y 10): now-running   (1/2, h=6)  |  by-site (1/2, h=7)
  //
  // The desktop mockup has the throughput chart spanning more horizontal
  // space than a half-card; tablet/desktop users can drag to adjust.
  // The defaults here give a sane starting point; the customizable
  // grid lets the user rearrange. by-site is one row taller than
  // now-running so the column heights are uneven — that's fine on
  // a grid layout (other widgets reflow underneath).
  return [
    { i: "attention",   x: 0, y: 0,  w: 12, h: DEFAULT_HEIGHTS.attention },
    { i: "today",       x: 0, y: 3,  w: 6,  h: DEFAULT_HEIGHTS.today },
    { i: "throughput",  x: 6, y: 3,  w: 6,  h: DEFAULT_HEIGHTS.throughput },
    { i: "now-running", x: 0, y: 7,  w: 6,  h: DEFAULT_HEIGHTS["now-running"] },
    { i: "by-site",     x: 6, y: 7,  w: 6,  h: DEFAULT_HEIGHTS["by-site"] },
  ];
}

export function defaultLayouts(): Layouts {
  return {
    lg: defaultDesktopLayout(),
    sm: defaultMobileLayout(),
  };
}

function readStored(storageKey: string): Layouts | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Validate: must be an object with at least one breakpoint that
    // is an array of {i,x,y,w,h}. If the persisted shape is corrupted
    // (manual edit, version mismatch), discard and use defaults.
    if (!parsed || typeof parsed !== "object") return null;
    const validate = (arr: unknown): arr is Layout[] =>
      Array.isArray(arr) &&
      arr.every(
        (e) =>
          e &&
          typeof e === "object" &&
          typeof (e as Layout).i === "string" &&
          typeof (e as Layout).x === "number" &&
          typeof (e as Layout).y === "number" &&
          typeof (e as Layout).w === "number" &&
          typeof (e as Layout).h === "number",
      );
    const out: Layouts = {};
    if (validate(parsed.lg)) out.lg = parsed.lg;
    if (validate(parsed.sm)) out.sm = parsed.sm;
    if (!out.lg && !out.sm) return null;
    return out;
  } catch {
    return null;
  }
}

// Merge the persisted layout against the canonical widget catalog.
// v3.64.x Unit B: "canonical" now includes both the 5 legacy widgets
// and the 36 KPI widgets from the catalog. Unknown IDs (renamed /
// removed) are dropped; KPI widgets that have been added to the
// selection but aren't in the layout yet get appended at the bottom
// with the default position.
//
// `extraIds` is the live KPI selection passed in by Home.tsx (from
// useWidgetSelection). Without it, KPI widgets would be reconciled
// away every render — defeating the picker.
function reconcile(stored: Layout[], defaults: Layout[], extraIds: string[] = [], kpiW: number = KPI_DEFAULT_WIDTH_LG): Layout[] {
  const known = new Set<string>([
    ...LEGACY_WIDGET_IDS,
    ...KPI_WIDGET_IDS,
  ]);
  // The full set of IDs that SHOULD be in the layout: legacy 5 +
  // whatever's in the current KPI selection. Anything in storage
  // outside this set gets dropped (renamed or removed widgets).
  const desired = new Set<string>([...LEGACY_WIDGET_IDS, ...extraIds]);
  const storedKeys = new Set(stored.map((s) => s.i));
  // Keep stored entries that are still canonical AND still desired.
  const kept = stored.filter((s) => known.has(s.i) && desired.has(s.i));
  // Append defaults / new KPI widgets that aren't in storage yet.
  const maxY = kept.reduce((acc, e) => Math.max(acc, e.y + e.h), 0);

  // Missing = entries that should be visible but aren't in storage.
  // Default layout entries take priority (their positions), then
  // KPI extras stack below.
  const missingLegacy = defaults
    .filter((d) => !storedKeys.has(d.i) && desired.has(d.i));
  const missingKpi = extraIds
    .filter((id) => !storedKeys.has(id) && !LEGACY_WIDGET_IDS.includes(id as WidgetId));

  const appendedLegacy = missingLegacy.map((d, i) => ({
    ...d,
    y: maxY + i * d.h,
  }));
  const legacyAppendY = appendedLegacy.reduce(
    (acc, e) => Math.max(acc, e.y + e.h), maxY,
  );
  const perRow = Math.max(1, Math.floor(12 / kpiW));
  const appendedKpi = missingKpi.map((id, i) => ({
    i: id,
    x: (i % perRow) * kpiW,
    y: legacyAppendY + Math.floor(i / perRow) * KPI_DEFAULT_HEIGHT,
    w: kpiW,
    h: KPI_DEFAULT_HEIGHT,
  } as Layout));

  return [...kept, ...appendedLegacy, ...appendedKpi];
}

export function useDashboardLayout(
  extraIds: string[] = [],
  siteId?: string,
): {
  layouts: Layouts;
  setLayouts: (next: Layouts) => void;
  reset: () => void;
  isCustom: boolean;
} {
  // Resolve the storage key once per render. The key only changes
  // when siteId changes (the Home tab never changes siteId at runtime;
  // SiteDetail unmounts when the route param changes, remounting the
  // hook fresh). useMemo keeps the key stable across rerenders.
  const storageKey = useMemo(() => resolveStorageKey(siteId), [siteId]);

  const [layouts, setLayoutsState] = useState<Layouts>(() => {
    const stored = readStored(storageKey);
    const defaults = defaultLayouts();
    if (!stored) {
      // Even on a fresh install, reconcile against extraIds so any
      // KPI widgets already in the selection get default positions.
      return {
        lg: reconcile(defaults.lg ?? [], defaults.lg ?? [], extraIds),
        sm: reconcile(defaults.sm ?? [], defaults.sm ?? [], extraIds, 6),
      };
    }
    return {
      lg: reconcile(stored.lg ?? [], defaults.lg ?? [], extraIds),
      sm: reconcile(stored.sm ?? [], defaults.sm ?? [], extraIds, 6),
    };
  });

  // When the operator adds/removes KPI widgets via the picker, the
  // extraIds prop changes. Re-reconcile so new widgets get default
  // positions and removed widgets disappear from the grid. We DON'T
  // persist this reconcile (it'd race with onLayoutChange); the next
  // drag-end will save the new positions.
  useEffect(() => {
    setLayoutsState((cur) => {
      const defaults = defaultLayouts();
      return {
        lg: reconcile(cur.lg ?? [], defaults.lg ?? [], extraIds),
        sm: reconcile(cur.sm ?? [], defaults.sm ?? [], extraIds, 6),
      };
    });
    // extraIds.join() identity stable across same-content arrays.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [extraIds.join("|")]);

  // Cross-tab sync — like the badge mode hook. If the operator
  // resets layout on one tab, the other tabs pick up the change.
  // B-3: listener filters by the RESOLVED key (scoped or global), so
  // a per-site reset on one tab can't cascade into the global key
  // listener on another tab.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: StorageEvent) => {
      if (e.key !== storageKey) return;
      const stored = readStored(storageKey);
      if (stored) {
        const defaults = defaultLayouts();
        setLayoutsState({
          lg: reconcile(stored.lg ?? [], defaults.lg ?? [], extraIds),
          sm: reconcile(stored.sm ?? [], defaults.sm ?? [], extraIds, 6),
        });
      } else {
        // Cleared in another tab.
        setLayoutsState(defaultLayouts());
      }
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, [extraIds, storageKey]);

  const setLayouts = useCallback((next: Layouts) => {
    setLayoutsState(next);
    try {
      // B-3: writes go to the RESOLVED key only. The global key is
      // never touched from a per-site context, and vice versa.
      window.localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* ignore — react state still updates */
    }
  }, [storageKey]);

  const reset = useCallback(() => {
    try {
      // B-3: remove the RESOLVED key only. On a per-site reset, this
      // removes `bd-dashboard-layout-${siteId}` and leaves the global
      // `bd-dashboard-layout` key untouched (negative-isolation
      // assertion in the e2e spec depends on this).
      window.localStorage.removeItem(storageKey);
    } catch {
      /* ignore */
    }
    // Reset == clear stored layout, but the KPI selection lives in a
    // SEPARATE storage key (bd-widget-selection). So a layout reset
    // shouldn't yank the user's chosen KPI widgets from the grid —
    // it should restore the default positions for legacy 5 + place
    // the current KPI selection in default positions below.
    const defaults = defaultLayouts();
    setLayoutsState({
      lg: reconcile(defaults.lg ?? [], defaults.lg ?? [], extraIds),
      sm: reconcile(defaults.sm ?? [], defaults.sm ?? [], extraIds, 6),
    });
  }, [extraIds, storageKey]);

  // "Custom" = the user has saved something. We compare the layout
  // shape (sorted by i, with each entry stringified) to the defaults.
  // Cheap O(n) check that runs once per re-render of the consumer.
  // B-3: lookup uses the RESOLVED key, so a global-key value doesn't
  // make a per-site instance look "custom" (and vice versa).
  const stored = (() => {
    try {
      return window.localStorage?.getItem(storageKey);
    } catch {
      return null;
    }
  })();
  const isCustom = stored !== null;

  return { layouts, setLayouts, reset, isCustom };
}
