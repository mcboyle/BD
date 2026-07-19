import { useCallback, useEffect, useState } from "react";

import { resolveTheme, themeFor, isThemeMode, THEMES } from "@/lib/themes";
import type { ThemeId, ThemeMode } from "@/lib/themes";

// v3.64.x — themes restoration.
//
// Previously the SPA shipped three modes (system/light/dark). The
// legacy `/` UI had 31 named themes — Dracula, Tokyo Night, Gruvbox,
// Catppuccin, etc. — that didn't make the v3.64.0 redesign. This
// module brings them back as the SPA's only theme mechanism.
//
// API contract (unchanged shape):
//   mode      : "system" | "<theme-id>"
//   setMode() : persist + apply
//   resolved  : "<theme-id>" — what's actually rendered
//
// `mode === "system"` resolves to "dark" or "light" via the
// prefers-color-scheme media query. Any other value names a concrete
// theme from THEMES.
//
// Storage: localStorage["bd-theme"]. Backwards-compatible — old
// stored values "system"/"light"/"dark" still work because those are
// valid ThemeIds in the new catalog.
//
// Application: writes the theme's CSS vars to <html style="--bg: ...">
// inline (overrides whatever index.css declared) AND toggles the
// `.dark` Tailwind class for utility classes that key off it.

const STORAGE_KEY = "bd-theme";

function readStored(): ThemeMode {
  if (typeof window === "undefined") return "system";
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v && isThemeMode(v)) return v;
  } catch {
    /* localStorage blocked (private mode w/ quotas, etc.) */
  }
  return "system";
}

function systemPrefersDark(): boolean {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyTheme(mode: ThemeMode): ThemeId {
  if (typeof document === "undefined") {
    return resolveTheme(mode, false);
  }
  const id = resolveTheme(mode, systemPrefersDark());
  const def = themeFor(id);
  const root = document.documentElement;

  // Write each CSS var to the root style — overrides index.css.
  // Setting them all on one element keeps the cascade obvious; any
  // var the theme doesn't define falls back to the index.css default.
  for (const [k, v] of Object.entries(def.vars)) {
    root.style.setProperty(`--${k}`, v);
  }

  // Tailwind `dark:` variant still keys off the class. Set/unset
  // based on the theme's isDark flag — a "named theme" like Dracula
  // is conceptually dark even though it's no longer the "dark" id.
  if (def.isDark) {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }

  // iOS status bar tint. Use the theme's `bg` directly — clamped to
  // a string so the meta API is happy.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) {
    meta.setAttribute("content", def.vars.bg || (def.isDark ? "#08090c" : "#f0f0f5"));
  }

  return id;
}

export function useTheme(): {
  mode: ThemeMode;
  setMode: (m: ThemeMode) => void;
  resolved: ThemeId;
  /** The full catalog, for the picker UI. */
  themes: typeof THEMES;
} {
  const [mode, setModeState] = useState<ThemeMode>(readStored);
  const [resolved, setResolved] = useState<ThemeId>(() =>
    resolveTheme(mode, systemPrefersDark()),
  );

  // Apply on mount + whenever mode changes.
  useEffect(() => {
    setResolved(applyTheme(mode));
  }, [mode]);

  // Listen for system theme changes when in "system" mode. Without
  // this, toggling the OS dark mode wouldn't update the SPA until
  // page reload.
  useEffect(() => {
    if (mode !== "system" || typeof window === "undefined" || !window.matchMedia)
      return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => setResolved(applyTheme("system"));
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [mode]);

  const setMode = useCallback((m: ThemeMode) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, m);
    } catch {
      /* ignore — applyTheme still runs */
    }
    setModeState(m);
  }, []);

  return { mode, setMode, resolved, themes: THEMES };
}

// Side-effecting helper: apply the persisted theme during initial app
// boot, BEFORE the first React render. Called from main.tsx so the
// first paint is already in the right palette (no flash of light
// content when dark/named-theme is stored).
export function applyStoredThemeOnBoot(): void {
  applyTheme(readStored());
}
