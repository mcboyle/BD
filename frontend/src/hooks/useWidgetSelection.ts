import { useCallback, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPut } from "@/lib/api-client";
import {
  ALL_WIDGET_IDS,
  DEFAULT_WIDGET_IDS,
} from "@/lib/widgetCatalog";

// v3.64.x — widget catalog restoration.
//
// Manages which KPI widgets are visible on a dashboard. Two modes:
//
//   GLOBAL (default — no siteId argument): persisted in localStorage
//   under "bd-widget-selection". Reconciled against the catalog on
//   read (drops unknown IDs from stale configs). Cross-tab via the
//   `storage` event. This is the v3.64.2 default-behavior path —
//   zero change for the Home tab.
//
//   PER-SITE (siteId argument, v3.64.x B-2): persisted server-side
//   via /api/widgets/<siteId>. Backend endpoint already exists
//   (app_widgets_api.py) — GET returns the resolved list (per-site
//   override OR global fallback), PUT replaces the per-site override,
//   DELETE removes the override (site falls back to global on next
//   read). Per-site selections are NOT cross-tab synced through
//   localStorage — the server is the source of truth and a stale
//   cache will refetch on focus.
//
// Backend shape: server returns {scope, widgets: [{id, size}, ...]}.
// The hook only consumes `id` for selection purposes; `size` is
// preserved on writes so per-tile size preferences (if a future UI
// adds size pickers) round-trip cleanly. Without that round-trip the
// PUT would silently reset everyone's sizes to "sm".

const STORAGE_KEY = "bd-widget-selection";

interface WidgetEntry {
  id: string;
  size?: string;
}

interface WidgetsScopeResponse {
  scope: string;
  widgets: WidgetEntry[];
}

interface WidgetsPutResponse {
  ok: boolean;
  scope: string;
  widgets: WidgetEntry[];
}

function readStored(): string[] {
  if (typeof window === "undefined") return DEFAULT_WIDGET_IDS.slice();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_WIDGET_IDS.slice();
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return DEFAULT_WIDGET_IDS.slice();
    // Reconcile against the catalog — drop any IDs that no longer
    // exist. An empty result (e.g. a future cleanup removed every
    // widget the user had picked) falls back to defaults so the
    // dashboard never looks empty on load.
    const valid = parsed.filter(
      (id): id is string => typeof id === "string" && ALL_WIDGET_IDS.has(id),
    );
    return valid.length > 0 ? valid : DEFAULT_WIDGET_IDS.slice();
  } catch {
    return DEFAULT_WIDGET_IDS.slice();
  }
}

function writeStored(ids: string[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
  } catch {
    /* storage unavailable — non-fatal */
  }
}

function reconcileFromServer(widgets: WidgetEntry[]): string[] {
  // Same shape-tolerant filter as readStored: server might return
  // an ID the SPA's catalog has retired, in which case it drops out
  // silently rather than rendering broken tiles.
  const valid = widgets
    .filter(w => w && typeof w.id === "string" && ALL_WIDGET_IDS.has(w.id))
    .map(w => w.id);
  return valid.length > 0 ? valid : DEFAULT_WIDGET_IDS.slice();
}

export interface UseWidgetSelection {
  /** The current ordered list of widget IDs. */
  ids: string[];
  /** Replace the entire selection. Order is preserved. */
  setIds: (ids: string[]) => void;
  /** Add a widget to the end of the selection (no-op if already present). */
  add: (id: string) => void;
  /** Remove a widget from the selection. */
  remove: (id: string) => void;
  /** Reset to DEFAULT_WIDGET_IDS (per-site: clears the override and
   *  falls back to global on next read). */
  reset: () => void;
  /** Reorder — replace the existing list with `newIds`. The caller is
   *  responsible for ensuring the new order is a permutation. */
  reorder: (newIds: string[]) => void;
  /** Per-site mode: server fetch is in flight. False in global mode. */
  isLoading: boolean;
  /** Per-site mode: last persistence error, if any. */
  error: Error | null;
}

/**
 * Manage the widget selection for either the global scope (no
 * argument) or a specific site (siteId argument). Behavior is
 * identical from the caller's perspective except `isLoading` /
 * `error` are populated only in the per-site path.
 */
export function useWidgetSelection(siteId?: string): UseWidgetSelection {
  const scopeMode = siteId ? "site" : "global";

  // ── Global mode ─────────────────────────────────────────────────
  const [globalIds, setGlobalIds] = useState<string[]>(readStored);

  // Cross-tab sync — only relevant in global mode (per-site uses the
  // server as source of truth). Mounting both branches is harmless
  // since the listener only fires on STORAGE_KEY changes.
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (scopeMode !== "global") return;
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) {
        setGlobalIds(readStored());
      }
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [scopeMode]);

  // ── Per-site mode ───────────────────────────────────────────────
  const qc = useQueryClient();
  const queryKey = ["widgets-scope", siteId ?? "_global"] as const;

  const {
    data: scopeData,
    isLoading: scopeLoading,
    error: scopeError,
  } = useQuery<WidgetsScopeResponse>({
    queryKey,
    queryFn: ({ signal }) =>
      apiGet<WidgetsScopeResponse>(
        `/api/widgets/${encodeURIComponent(siteId ?? "_global")}`,
        signal,
      ),
    refetchOnWindowFocus: false,
    enabled: scopeMode === "site",
    retry: 0,
  });

  // Materialize the per-site widget entries (id+size) so PUTs can
  // round-trip the size field. Falls back to a default-shaped list
  // when the server hasn't responded yet.
  const serverEntries: WidgetEntry[] =
    scopeData?.widgets && Array.isArray(scopeData.widgets)
      ? scopeData.widgets
      : DEFAULT_WIDGET_IDS.map(id => ({ id, size: "sm" }));

  const siteIds: string[] =
    scopeMode === "site"
      ? reconcileFromServer(serverEntries)
      : [];

  const putMutation = useMutation<WidgetsPutResponse, Error, string[]>({
    mutationFn: (newIds) => {
      // Round-trip the size field if the server already had one for
      // each ID; default to "sm" for newly-added IDs.
      const sizeById = new Map(
        serverEntries.map(e => [e.id, e.size || "sm"]),
      );
      const payload = {
        widgets: newIds.map(id => ({ id, size: sizeById.get(id) ?? "sm" })),
      };
      return apiPut<WidgetsPutResponse>(
        `/api/widgets/${encodeURIComponent(siteId ?? "_global")}`,
        payload,
      );
    },
    onSuccess: (resp) => {
      // Reflect the sanitized server-canonical list back into the cache.
      qc.setQueryData<WidgetsScopeResponse>(queryKey, {
        scope: resp.scope,
        widgets: resp.widgets,
      });
    },
  });

  const deleteMutation = useMutation<unknown, Error, void>({
    mutationFn: () =>
      apiDelete(`/api/widgets/${encodeURIComponent(siteId ?? "_global")}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey });
    },
  });

  // ── Unified surface ─────────────────────────────────────────────
  const ids = scopeMode === "site" ? siteIds : globalIds;

  const commitGlobal = useCallback((next: string[]) => {
    writeStored(next);
    setGlobalIds(next);
  }, []);

  const setIds = useCallback(
    (next: string[]) => {
      if (scopeMode === "site") {
        putMutation.mutate(next);
      } else {
        commitGlobal(next);
      }
    },
    [scopeMode, putMutation, commitGlobal],
  );

  const add = useCallback(
    (id: string) => {
      if (!ALL_WIDGET_IDS.has(id)) return;
      if (scopeMode === "site") {
        if (siteIds.includes(id)) return;
        putMutation.mutate([...siteIds, id]);
      } else {
        setGlobalIds(prev => {
          if (prev.includes(id)) return prev;
          const next = [...prev, id];
          writeStored(next);
          return next;
        });
      }
    },
    [scopeMode, siteIds, putMutation],
  );

  const remove = useCallback(
    (id: string) => {
      if (scopeMode === "site") {
        if (!siteIds.includes(id)) return;
        putMutation.mutate(siteIds.filter(x => x !== id));
      } else {
        setGlobalIds(prev => {
          if (!prev.includes(id)) return prev;
          const next = prev.filter(x => x !== id);
          writeStored(next);
          return next;
        });
      }
    },
    [scopeMode, siteIds, putMutation],
  );

  const reset = useCallback(() => {
    if (scopeMode === "site") {
      // Per-site reset = remove the override; backend falls back to global.
      deleteMutation.mutate();
    } else {
      commitGlobal(DEFAULT_WIDGET_IDS.slice());
    }
  }, [scopeMode, deleteMutation, commitGlobal]);

  const reorder = useCallback(
    (newIds: string[]) => {
      if (scopeMode === "site") {
        putMutation.mutate(newIds);
      } else {
        commitGlobal(newIds);
      }
    },
    [scopeMode, putMutation, commitGlobal],
  );

  return {
    ids,
    setIds,
    add,
    remove,
    reset,
    reorder,
    isLoading: scopeMode === "site"
      ? scopeLoading || putMutation.isPending || deleteMutation.isPending
      : false,
    error: scopeMode === "site"
      ? (scopeError as Error | null)
        || putMutation.error
        || deleteMutation.error
      : null,
  };
}
