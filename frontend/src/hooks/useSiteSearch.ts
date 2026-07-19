import { useMutation, useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";

// v3.66.743 — the search CONTROL cluster (live-site search).
//
//   GET  /api/search/sites_available          -> {ok, available[], count, total_sites}
//   POST /api/search/site  {site_id, query, max_results?}
//   POST /api/search/all   {query, max_results_per_site?, sites?}
//
// ============================================================================
// THE FACT THIS FILE EXISTS TO GET RIGHT:
//
//   Both search endpoints DEGRADE AT HTTP 200. On a box without the
//   search_extractor module, every POST returns {"ok": false, "error":
//   "search_extractor unavailable"} — status 200. The capability signal lives
//   in the READ: /api/search/sites_available carries the same guard. The
//   panel consumes that read and DISABLES; it never fires a doomed POST.
//
// SHADOW GUARD: /api/search (GET, q in request.args) is HISTORY full-text
// search — a different job, already wired on History.tsx. Nothing in this
// file may call it. `q`/`query` for THIS family rides the POST body.
// ============================================================================

export interface SearchableSite {
  site_id: string;
  name: string;
}

export interface SitesAvailable {
  ok: boolean;
  error?: string;
  available?: SearchableSite[];
  count?: number;
  total_sites?: number;
}

export interface SearchHit {
  url?: string;
  title?: string;
  [k: string]: unknown;
}

export interface SiteSearchResult {
  ok: boolean;
  error?: string;
  site_id?: string;
  query?: string;
  hits?: SearchHit[];
  [k: string]: unknown;
}

export interface AllSearchResult {
  ok: boolean;
  error?: string;
  query?: string;
  results?: Record<string, SiteSearchResult>;
  stats?: Record<string, unknown>;
  note?: string;
}

/** The capability read. ok:false means "search extractor not installed" —
 *  the panel renders a disabled state off this, never a doomed POST. */
export function useSitesAvailable() {
  return useQuery<SitesAvailable>({
    queryKey: ["search", "sites_available"],
    queryFn: ({ signal }) =>
      apiGet<SitesAvailable>("/api/search/sites_available", signal),
    staleTime: 60_000,
  });
}

export function useSearchSite() {
  return useMutation<SiteSearchResult, Error, { site_id: string; query: string; max_results?: number }>({
    mutationFn: (vars) =>
      apiPost<SiteSearchResult>("/api/search/site", vars),
  });
}

export function useSearchAll() {
  return useMutation<AllSearchResult, Error, { query: string; max_results_per_site?: number; sites?: string[] }>({
    mutationFn: (vars) => {
      if (vars.sites !== undefined && !Array.isArray(vars.sites)) {
        // the endpoint 400s "'sites' must be a list"; never send anything else
        throw new Error("'sites' must be a list");
      }
      return apiPost<AllSearchResult>("/api/search/all", vars);
    },
  });
}
