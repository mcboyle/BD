import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type { GlobalConfigSubset } from "@/lib/api-types";

// v3.64.3 — completion-sound preference with opt-in cross-device sync.
//
// Background: v3.64.2 and earlier kept sound on/off in localStorage only
// (key "bd-sound-on-complete"). This was intentional — per-device so
// silencing one device didn't silence others. v3.64.3 adds an OPT-IN
// sync, with localStorage-only remaining the default.
//
// Design:
//
//   "syncMode" is itself per-device (localStorage["bd-sound-sync"]).
//   If sync were synced, you couldn't opt out from a single device.
//
//   When syncMode === "off" (default), the hook behaves like the
//   v3.64.2 path: read/write localStorage["bd-sound-on-complete"], no
//   network. Zero behavior change for anyone who doesn't touch the new
//   toggle.
//
//   When syncMode === "on", the hook reads /api/global_config field
//   `sound_on_complete` and writes back to it. localStorage is updated
//   too as a side effect, so flipping sync OFF later doesn't lose state.
//
//   Adopting sync (off → on): we POST the device's CURRENT localStorage
//   value up to the server immediately, so a device that already has
//   sound enabled doesn't lose its setting on opt-in. (If the server
//   already has a non-null value, the user can either accept the
//   server's value or flip the switch — either way they're in control.)
//
//   The hook returns { enabled, setEnabled, syncMode, setSyncMode,
//   isLoading } so callers (Settings, useCompletionSound) can render
//   a coherent UI even during the initial /api/global_config fetch.

const ENABLED_KEY = "bd-sound-on-complete";
const SYNC_KEY = "bd-sound-sync";

export type SyncMode = "off" | "on";

function readEnabledLocal(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(ENABLED_KEY) === "1";
  } catch {
    return false;
  }
}

function writeEnabledLocal(v: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ENABLED_KEY, v ? "1" : "0");
  } catch {
    /* ignore */
  }
}

function readSyncModeLocal(): SyncMode {
  if (typeof window === "undefined") return "off";
  try {
    return window.localStorage.getItem(SYNC_KEY) === "1" ? "on" : "off";
  } catch {
    return "off";
  }
}

function writeSyncModeLocal(m: SyncMode): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(SYNC_KEY, m === "on" ? "1" : "0");
  } catch {
    /* ignore */
  }
}

export interface UseSyncedSoundPref {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
  syncMode: SyncMode;
  setSyncMode: (m: SyncMode) => void;
  isLoading: boolean;
}

export function useSyncedSoundPref(): UseSyncedSoundPref {
  const qc = useQueryClient();
  const [syncMode, setSyncModeState] = useState<SyncMode>(readSyncModeLocal);
  const [localEnabled, setLocalEnabled] = useState<boolean>(readEnabledLocal);

  // Cross-tab sync for syncMode + localEnabled. Keeps two open tabs
  // consistent if the operator toggles in one and not the other.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const handler = (e: StorageEvent) => {
      if (e.key === SYNC_KEY) setSyncModeState(readSyncModeLocal());
      if (e.key === ENABLED_KEY) setLocalEnabled(readEnabledLocal());
    };
    window.addEventListener("storage", handler);
    return () => window.removeEventListener("storage", handler);
  }, []);

  // Server-side value, only fetched when sync is on. Reuse the
  // existing global-config query so we don't open a second pipe.
  const { data: serverCfg, isLoading: serverLoading } = useQuery<GlobalConfigSubset>({
    queryKey: ["global-config"],
    queryFn: ({ signal }) => apiGet<GlobalConfigSubset>("/api/global_config", signal),
    refetchOnWindowFocus: false,
    enabled: syncMode === "on",
  });

  const enabled = syncMode === "on"
    ? Boolean(serverCfg?.sound_on_complete)
    : localEnabled;

  const setEnabled = useCallback(
    (v: boolean) => {
      // Always update localStorage too — preserves state if sync is
      // later turned off.
      writeEnabledLocal(v);
      setLocalEnabled(v);
      if (syncMode === "on") {
        // Fire and forget; the global-config query will re-fetch on
        // invalidation. We don't await — UI is optimistic.
        apiPost<GlobalConfigSubset>("/api/global_config", {
          sound_on_complete: v,
        })
          .then(() => qc.invalidateQueries({ queryKey: ["global-config"] }))
          .catch(() => {
            /* swallow — ambient feature, no user-facing error */
          });
      }
    },
    [syncMode, qc],
  );

  const setSyncMode = useCallback(
    (m: SyncMode) => {
      writeSyncModeLocal(m);
      setSyncModeState(m);
      if (m === "on") {
        // Adoption: push the device's current localStorage value to
        // the server. This means a sound-on user flipping sync ON
        // sees no change in behavior — the server adopts their value.
        // If the server already had a value, this overwrites it
        // (the operator who just clicked the toggle has the
        // freshest intent).
        const current = readEnabledLocal();
        apiPost<GlobalConfigSubset>("/api/global_config", {
          sound_sync_enabled: true,
          sound_on_complete: current,
        })
          .then(() => qc.invalidateQueries({ queryKey: ["global-config"] }))
          .catch(() => {
            /* swallow */
          });
      } else {
        // Flipping OFF: just clear the server flag. The localStorage
        // value remains; we don't blow it away.
        apiPost<GlobalConfigSubset>("/api/global_config", {
          sound_sync_enabled: false,
        })
          .then(() => qc.invalidateQueries({ queryKey: ["global-config"] }))
          .catch(() => {
            /* swallow */
          });
      }
    },
    [qc],
  );

  return {
    enabled,
    setEnabled,
    syncMode,
    setSyncMode,
    isLoading: syncMode === "on" && serverLoading,
  };
}
