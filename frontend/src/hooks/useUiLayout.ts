import { useCallback, useEffect, useState } from "react";

// Per-device UI layout preferences (localStorage), mirroring the
// useQueueBadgeMode pattern: read-on-mount, cross-tab `storage` sync,
// clamped setters. These are personal layout choices per browser —
// NOT synced through global_config (same rationale as the badge mode
// and the completion-sound preference).
//
// Three knobs, two surfaces:
//   • sidebar collapsed  — DesktopShell nav rail icon-only toggle.
//                          Storage: localStorage["bd-sidebar-collapsed"] ("1"/"0").
//   • sidebar width (px) — DesktopShell nav rail drag-resize (expanded).
//                          Storage: localStorage["bd-sidebar-width"].
//   • capture rail (px)  — CaptureWorkflow "Inspect & refine" drag-resize.
//                          Storage: localStorage["bd-capture-rail-width"].
//
// All numeric values are clamped on both read and write so a hand-edited
// or stale localStorage entry can never push a pane off-screen.

const SIDEBAR_COLLAPSED_KEY = "bd-sidebar-collapsed";
const SIDEBAR_WIDTH_KEY = "bd-sidebar-width";
const CAPTURE_RAIL_KEY = "bd-capture-rail-width";

// Expanded sidebar bounds (px). Collapsed uses a separate fixed
// icon-rail width handled in DesktopShell, independent of this.
export const SIDEBAR_WIDTH_MIN = 180;
export const SIDEBAR_WIDTH_MAX = 360;
export const SIDEBAR_WIDTH_DEFAULT = 224; // == Tailwind w-56 (the pre-269 fixed width)
// Collapsed = an icon-only strip: an h-4 icon centered in a tap target, no slack.
export const SIDEBAR_COLLAPSED_WIDTH = 48;

// Capture "Inspect & refine" rail bounds (px).
export const CAPTURE_RAIL_MIN = 300;
export const CAPTURE_RAIL_MAX = 600;
export const CAPTURE_RAIL_DEFAULT = 400; // == the pre-269 fixed 400px grid track

function clamp(n: number, lo: number, hi: number): number {
  if (!Number.isFinite(n)) return lo;
  return Math.max(lo, Math.min(hi, Math.round(n)));
}

function readBool(key: string): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(key) === "1";
  } catch {
    /* localStorage blocked */
    return false;
  }
}

function readNum(key: string, def: number, lo: number, hi: number): number {
  if (typeof window === "undefined") return def;
  try {
    const v = window.localStorage.getItem(key);
    if (v == null) return def;
    const n = parseInt(v, 10);
    return Number.isFinite(n) ? clamp(n, lo, hi) : def;
  } catch {
    /* localStorage blocked */
    return def;
  }
}

export interface SidebarLayout {
  collapsed: boolean;
  setCollapsed: (c: boolean) => void;
  /** Expanded width in px (ignored while collapsed). Clamped. */
  width: number;
  setWidth: (w: number) => void;
}

export function useSidebarLayout(): SidebarLayout {
  const [collapsed, setCollapsedState] = useState<boolean>(() =>
    readBool(SIDEBAR_COLLAPSED_KEY),
  );
  const [width, setWidthState] = useState<number>(() =>
    readNum(
      SIDEBAR_WIDTH_KEY,
      SIDEBAR_WIDTH_DEFAULT,
      SIDEBAR_WIDTH_MIN,
      SIDEBAR_WIDTH_MAX,
    ),
  );

  // Cross-tab sync: another tab toggling collapse / dragging the rail
  // fires `storage` here so every open tab stays consistent.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: StorageEvent) => {
      if (e.key === SIDEBAR_COLLAPSED_KEY)
        setCollapsedState(readBool(SIDEBAR_COLLAPSED_KEY));
      if (e.key === SIDEBAR_WIDTH_KEY)
        setWidthState(
          readNum(
            SIDEBAR_WIDTH_KEY,
            SIDEBAR_WIDTH_DEFAULT,
            SIDEBAR_WIDTH_MIN,
            SIDEBAR_WIDTH_MAX,
          ),
        );
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  const setCollapsed = useCallback((c: boolean) => {
    try {
      window.localStorage.setItem(SIDEBAR_COLLAPSED_KEY, c ? "1" : "0");
    } catch {
      /* ignore */
    }
    setCollapsedState(c);
  }, []);

  const setWidth = useCallback((w: number) => {
    const c = clamp(w, SIDEBAR_WIDTH_MIN, SIDEBAR_WIDTH_MAX);
    try {
      window.localStorage.setItem(SIDEBAR_WIDTH_KEY, String(c));
    } catch {
      /* ignore */
    }
    setWidthState(c);
  }, []);

  return { collapsed, setCollapsed, width, setWidth };
}

export interface PaneWidth {
  width: number;
  setWidth: (w: number) => void;
}

export function useCaptureRailWidth(): PaneWidth {
  const [width, setWidthState] = useState<number>(() =>
    readNum(
      CAPTURE_RAIL_KEY,
      CAPTURE_RAIL_DEFAULT,
      CAPTURE_RAIL_MIN,
      CAPTURE_RAIL_MAX,
    ),
  );

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: StorageEvent) => {
      if (e.key !== CAPTURE_RAIL_KEY) return;
      setWidthState(
        readNum(
          CAPTURE_RAIL_KEY,
          CAPTURE_RAIL_DEFAULT,
          CAPTURE_RAIL_MIN,
          CAPTURE_RAIL_MAX,
        ),
      );
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  const setWidth = useCallback((w: number) => {
    const c = clamp(w, CAPTURE_RAIL_MIN, CAPTURE_RAIL_MAX);
    try {
      window.localStorage.setItem(CAPTURE_RAIL_KEY, String(c));
    } catch {
      /* ignore */
    }
    setWidthState(c);
  }, []);

  return { width, setWidth };
}
