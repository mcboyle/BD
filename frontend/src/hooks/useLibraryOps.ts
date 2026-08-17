import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPost } from "@/lib/api-client";
import type {
  LibraryAuditResult,
  LibraryOrphansResult,
  LibraryStatsResult,
  RegenNfosResult,
  SceneScoreList,
  StorageInventoryResult,
  TagOpResult,
  TagRowsResult,
  TagSuggestResult,
  TagsForManyResult,
} from "@/lib/api-types";

// ── T3 library/tags tranche (v3.66.207, with T4 in the same cut) ─────
//
// Ports the 12 legacy-only library/tags/scene_score/storage_rebalance
// families into the EXISTING SPA /library + /rebalance routes, per
// the current library route contract
// batch). Every call is a FULL "/api/…" string literal (inline `${…}`
// template params are fine — they normalise to the same parameterised
// endpoint; a concatenated base var is NOT credited), so
// gui_parity_inventory.py observes each SPA endpoint consumer.
//
// Write gating (program contract — never one-click): the bulk tag
// writes (add/remove/rename) and a non-dry NFO regen arm through the
// route's typed-confirm dialog. Audit/orphans/inventory are
// compute-only POSTs (no state change) and read freely. No endpoint in
// this tranche carries secrets.

/** GET /api/library/stats — aggregate disk usage by dimension. */
export function useLibraryStats() {
  return useQuery<LibraryStatsResult>({
    queryKey: ["library", "stats"],
    queryFn: ({ signal }) => apiGet<LibraryStatsResult>("/api/library/stats", signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/library/audit — full audit of a download dir (compute-only). */
export function useLibraryAudit() {
  return useMutation<LibraryAuditResult, Error, { download_dir: string; site_id?: string }>({
    mutationFn: (body) => apiPost<LibraryAuditResult>("/api/library/audit", body),
  });
}

/** POST /api/library/orphans — files on disk not in history (compute-only). */
export function useLibraryOrphans() {
  return useMutation<LibraryOrphansResult, Error, { download_dir: string; site_id?: string }>({
    mutationFn: (body) => apiPost<LibraryOrphansResult>("/api/library/orphans", body),
  });
}

/** POST /api/library/regen_nfos — regenerate NFO sidecars. dry_run=true is the
 *  preview path (read-safe); a real run (dry_run=false) is confirm-gated by
 *  the route. */
export function useRegenNfos() {
  return useMutation<
    RegenNfosResult,
    Error,
    { site_id?: string; overwrite?: boolean; dry_run: boolean }
  >({
    mutationFn: (body) => apiPost<RegenNfosResult>("/api/library/regen_nfos", body),
  });
}

/** POST /api/tags/for_many — batch tag lookup for a set of history ids. */
export function useTagsForMany() {
  return useMutation<TagsForManyResult, Error, { history_ids: number[] }>({
    mutationFn: (body) => apiPost<TagsForManyResult>("/api/tags/for_many", body),
  });
}

/** POST /api/tags/add — bulk-add one tag to many history rows (confirm-gated). */
export function useTagAdd() {
  const qc = useQueryClient();
  return useMutation<TagOpResult, Error, { history_ids: number[]; tag: string }>({
    mutationFn: (body) => apiPost<TagOpResult>("/api/tags/add", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

/** POST /api/tags/remove — bulk-remove one tag from many rows (confirm-gated). */
export function useTagRemove() {
  const qc = useQueryClient();
  return useMutation<TagOpResult, Error, { history_ids: number[]; tag: string }>({
    mutationFn: (body) => apiPost<TagOpResult>("/api/tags/remove", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

/** POST /api/tags/rename — rename a tag everywhere; merges into an existing
 *  tag of the new name (confirm-gated — the merge is not reversible). */
export function useTagRename() {
  const qc = useQueryClient();
  return useMutation<TagOpResult, Error, { old: string; new: string }>({
    mutationFn: (body) => apiPost<TagOpResult>("/api/tags/rename", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

/** GET /api/tags/rows/{tag} — rows carrying a tag. */
export function useTagRows(tag: string, limit = 100) {
  return useQuery<TagRowsResult>({
    queryKey: ["tags", "rows", tag, limit],
    queryFn: ({ signal }) =>
      apiGet<TagRowsResult>(`/api/tags/rows/${encodeURIComponent(tag)}?limit=${limit}`, signal),
    enabled: tag.length > 0,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/tags/suggest/{hid} — tag-inference suggestions for one row. */
export function useTagSuggest(hid: number | null) {
  return useQuery<TagSuggestResult>({
    queryKey: ["tags", "suggest", hid],
    queryFn: ({ signal }) => apiGet<TagSuggestResult>(`/api/tags/suggest/${hid}`, signal),
    enabled: hid !== null,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/scene_score/bottom — lowest-scored files (review candidates). */
export function useSceneScoreBottom(limit = 20) {
  return useQuery<SceneScoreList>({
    queryKey: ["scene_score", "bottom", limit],
    queryFn: ({ signal }) =>
      apiGet<SceneScoreList>(`/api/scene_score/bottom?limit=${limit}`, signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/storage_rebalance/inventory — per-disk usage inventory for a
 *  set of paths (compute-only; pairs with the plan→execute panel). */
export function useStorageInventory() {
  return useMutation<StorageInventoryResult, Error, { paths: string[] }>({
    mutationFn: (body) => apiPost<StorageInventoryResult>("/api/storage_rebalance/inventory", body),
  });
}

// ── A5 / LIB-1 (v3.66.324): per-item library metadata ops. Ports the legacy
// item rating / watched / per-item tag controls onto the existing-but-phantom
// POST /api/library/<id>/{rating,watched,tags} (+ DELETE .../tags/<tag>) routes.
// Full template literals so the parity scanner credits spa_wired; each
// invalidates the "library" query family so the browse list reflects the change.
type LibraryItemOpResult = {
  ok: boolean;
  error?: string;
  rating?: number | null;
  watched?: boolean;
};

/** POST /api/library/<id>/rating — set or clear (null) an item's rating. */
export function useLibrarySetRating() {
  const qc = useQueryClient();
  return useMutation<LibraryItemOpResult, Error, { id: number | string; rating: number | null }>({
    mutationFn: ({ id, rating }) =>
      apiPost<LibraryItemOpResult>(`/api/library/${id}/rating`, { rating }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

/** POST /api/library/<id>/watched — mark an item watched/unwatched. */
export function useLibrarySetWatched() {
  const qc = useQueryClient();
  return useMutation<LibraryItemOpResult, Error, { id: number | string; watched: boolean }>({
    mutationFn: ({ id, watched }) =>
      apiPost<LibraryItemOpResult>(`/api/library/${id}/watched`, { watched }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

/** POST /api/library/<id>/tags — attach a tag (creating it if needed). */
export function useLibraryAddTag() {
  const qc = useQueryClient();
  return useMutation<LibraryItemOpResult, Error, { id: number | string; tag: string }>({
    mutationFn: ({ id, tag }) =>
      apiPost<LibraryItemOpResult>(`/api/library/${id}/tags`, { tag }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}

/** DELETE /api/library/<id>/tags/<tag> — detach a tag from an item. */
export function useLibraryRemoveTag() {
  const qc = useQueryClient();
  return useMutation<LibraryItemOpResult, Error, { id: number | string; tag: string }>({
    mutationFn: ({ id, tag }) =>
      apiDelete<LibraryItemOpResult>(`/api/library/${id}/tags/${encodeURIComponent(tag)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["library"] }),
  });
}
