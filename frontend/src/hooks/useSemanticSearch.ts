import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";

// v3.66.743 — the semantic CONTROL cluster (recall over captures + templates).
//
//   GET  /api/semantic/status            -> {ok, enabled, indexed, dims, has_sqlite_vec}
//   POST /api/semantic/search  {query, k?}   (k clamped 1..50 server-side;
//                                             empty query is a REAL 400)
//   POST /api/semantic/reindex {}            (rebuilds from the live corpus —
//                                             potentially expensive, not
//                                             destructive; single-fire UI)
//
// ============================================================================
// THE FACT THIS FILE EXISTS TO GET RIGHT:
//
//   search and reindex run happily against an EMPTY index and return ok:true
//   with zero hits — indistinguishable in the UI from "no matches" unless
//   `indexed` from status is rendered. The status read is the control's
//   meaning, not decoration: a reindex button with no indexed-count readout
//   is unknown laundered into OK.
// ============================================================================

export interface SemanticStatus {
  ok: boolean;
  error?: string;
  enabled?: boolean;
  indexed?: number;
  dims?: number;
  has_sqlite_vec?: boolean;
}

export interface SemanticHit {
  score?: number;
  kind?: string;
  ref?: string;
  [k: string]: unknown;
}

export interface SemanticSearchResult {
  ok: boolean;
  error?: string;
  hits?: SemanticHit[];
  k?: number;
  [k: string]: unknown;
}

export interface ReindexResult {
  ok: boolean;
  error?: string;
  indexed?: number;
  [k: string]: unknown;
}

export function useSemanticStatus() {
  return useQuery<SemanticStatus>({
    queryKey: ["semantic", "status"],
    queryFn: ({ signal }) => apiGet<SemanticStatus>("/api/semantic/status", signal),
    staleTime: 30_000,
  });
}

export function useSemanticSearch() {
  return useMutation<SemanticSearchResult, Error, { query: string; k?: number }>({
    mutationFn: (vars) => apiPost<SemanticSearchResult>("/api/semantic/search", vars),
  });
}

export function useSemanticReindex() {
  const qc = useQueryClient();
  return useMutation<ReindexResult, Error, void>({
    mutationFn: () => apiPost<ReindexResult>("/api/semantic/reindex", {}),
    onSuccess: () => {
      // the indexed count just changed; the readout must follow
      void qc.invalidateQueries({ queryKey: ["semantic", "status"] });
    },
  });
}
