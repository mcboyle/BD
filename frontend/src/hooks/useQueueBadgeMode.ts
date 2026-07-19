import { useCallback, useEffect, useState } from "react";

// Queue tab badge display preference. Per-device (localStorage), not
// synced through global_config — matches the pattern useTheme and
// useCompletionSound use for UI preferences.
//
// Modes:
//   "count"   — original behavior: red dot if any running/waiting > 0.
//               Default for users who never visit Settings.
//   "percent" — show aggregate running progress as "NN%" on the icon.
//               When nothing is running, falls back to the count dot.
//
// Storage: localStorage["bd-queue-badge-mode"].

export type QueueBadgeMode = "count" | "percent";

const STORAGE_KEY = "bd-queue-badge-mode";

function readStored(): QueueBadgeMode {
  if (typeof window === "undefined") return "count";
  try {
    const v = window.localStorage.getItem(STORAGE_KEY);
    if (v === "percent") return "percent";
  } catch {
    /* localStorage blocked */
  }
  return "count";
}

export function useQueueBadgeMode(): {
  mode: QueueBadgeMode;
  setMode: (m: QueueBadgeMode) => void;
} {
  const [mode, setModeState] = useState<QueueBadgeMode>(readStored);

  // Cross-tab sync: when the preference changes in another browser
  // tab, the `storage` event fires here. Keeps the toggle position
  // consistent if the operator changes it on the Settings tab and
  // then opens Queue in a new tab.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: StorageEvent) => {
      if (e.key !== STORAGE_KEY) return;
      setModeState(readStored());
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  const setMode = useCallback((m: QueueBadgeMode) => {
    try {
      window.localStorage.setItem(STORAGE_KEY, m);
    } catch {
      /* ignore */
    }
    setModeState(m);
  }, []);

  return { mode, setMode };
}

// Aggregate progress percentage across all currently-running jobs.
// Used by the BottomTabBar in percent mode. Returns 0 when there's
// nothing running. Caller passes the per-job progress numbers (0-100
// floats); we sum and average. Empty list = 0.
export function aggregateRunningPct(progressList: readonly number[]): number {
  if (progressList.length === 0) return 0;
  const sum = progressList.reduce((acc, p) => acc + (Number.isFinite(p) ? p : 0), 0);
  return Math.max(0, Math.min(100, Math.round(sum / progressList.length)));
}
