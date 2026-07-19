import { useEffect } from "react";
import { useMutation, useQuery, useInfiniteQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPatch, apiPost } from "@/lib/api-client";
import { isStreamConnected } from "./useEventStream";
import type {
  HistoryRow,
  HistoryPage,
  SessionHistoryResponse,
  EventsAllResponse,
  LogsTailResponse,
  LogsClearResult,
  SearchResponse,
  SavedSearchesList,
  SavedSearchAddResult,
  SavedSearchDigest,
  SavedSearchRunResult,
  UiEventsIngestResult,
  OkResult,
} from "@/lib/api-types";

// ── T2 history/logs/search tranche (v3.66.206) ──────────────────────
//
// Ports the 12 legacy-only history/logs/search families into the SPA
// /history route, per docs/LEGACY_MIGRATION_PLAN.md Phase 2 / T2. Each
// call uses the FULL "/api/…" string literal (NOT a concatenated base
// var) so tools/legacy_parity.py + gui_parity_inventory.py credit the
// endpoint spa_wired and it drops out of the legacy-only baseline.
//
// First confirm-gated writes of Phase 2 (log clear, history vacuum,
// saved-search add/delete): the route arms them through the typed/
// one-step confirm dialog (Maintenance.tsx pattern) — never one-click.
// None of these endpoints carry secrets, so the (R) redaction pairing
// rule does not bite this tranche (that arrives at T7 notify).

const SLOW = 30_000;
// F4.5: slow safety poll while the shared SSE stream is live.
const STREAM_SAFETY = 60_000;

/** GET /api/history — recent history rows with optional filters. */
export function useHistory(filters: {
  site_id?: string;
  status?: string;
  q?: string;
  limit?: number;
}) {
  const params = new URLSearchParams();
  if (filters.site_id) params.set("site_id", filters.site_id);
  if (filters.status) params.set("status", filters.status);
  if (filters.q) params.set("q", filters.q);
  params.set("limit", String(filters.limit ?? 200));
  const qs = params.toString();
  return useQuery<HistoryRow[]>({
    queryKey: ["history", qs],
    queryFn: ({ signal }) => apiGet<HistoryRow[]>(`/api/history?${qs}`, signal),
    // F4.5: back off to a slow safety poll while the shared SSE stream is
    // live (History invalidates on queue_change pushes); poll resumes when
    // the stream drops.
    refetchInterval: () => (isStreamConnected() ? STREAM_SAFETY : SLOW),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/**
 * F4.4 (v3.66.219): cursor pagination over /api/history.
 * Uses ?paginate=1 for the first page and ?cursor=<id> for older pages,
 * consuming the {rows, next_cursor} envelope (db_search_cursor). The plain
 * useHistory() bare-array hook above is unchanged. Full "/api/history" literal
 * so tools/legacy_parity.py + gui_parity_inventory.py credit it spa_wired.
 */
export function useHistoryPaginated(filters: {
  site_id?: string;
  status?: string;
  q?: string;
  limit?: number;
}) {
  const base = new URLSearchParams();
  if (filters.site_id) base.set("site_id", filters.site_id);
  if (filters.status) base.set("status", filters.status);
  if (filters.q) base.set("q", filters.q);
  base.set("limit", String(filters.limit ?? 100));
  const baseQs = base.toString();
  return useInfiniteQuery<HistoryPage>({
    queryKey: ["history_paginated", baseQs],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) => {
      const p = new URLSearchParams(baseQs);
      if (pageParam != null) p.set("cursor", String(pageParam));
      else p.set("paginate", "1");
      return apiGet<HistoryPage>(`/api/history?${p.toString()}`, signal);
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/history/vacuum — compact the history database (confirm-gated). */
export function useHistoryVacuum() {
  const qc = useQueryClient();
  return useMutation<OkResult, Error, void>({
    mutationFn: () => apiPost<OkResult>("/api/history/vacuum", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["history"] }),
  });
}

/** GET /api/session_history — recent session keeper events. */
export function useSessionHistory(limit = 100) {
  return useQuery<SessionHistoryResponse>({
    queryKey: ["session_history", limit],
    queryFn: ({ signal }) =>
      apiGet<SessionHistoryResponse>(
        `/api/session_history?limit=${limit}`,
        signal,
      ),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/events_all — merged cross-site event feed (JSON cursor). */
export function useEventsAll(limit = 200) {
  return useQuery<EventsAllResponse>({
    queryKey: ["events_all", limit],
    queryFn: ({ signal }) =>
      apiGet<EventsAllResponse>(`/api/events_all?limit=${limit}`, signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/logs/tail — last N lines of the app log (windowed read). */
export function useLogsTail(lines = 200) {
  return useQuery<LogsTailResponse>({
    queryKey: ["logs_tail", lines],
    queryFn: ({ signal }) =>
      apiGet<LogsTailResponse>(`/api/logs/tail?lines=${lines}`, signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/logs/clear — truncate the active log + sweep rotated
 *  archives (confirm-gated; typed token). */
export function useLogsClear() {
  const qc = useQueryClient();
  return useMutation<LogsClearResult, Error, void>({
    mutationFn: () => apiPost<LogsClearResult>("/api/logs/clear", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["logs_tail"] }),
  });
}

/** GET /api/search — FTS over history with <mark> snippets. Disabled
 *  until the query is non-empty (the handler returns [] for empty q). */
export function useSearch(q: string, limit = 100) {
  const query = q.trim();
  return useQuery<SearchResponse>({
    queryKey: ["search", query, limit],
    queryFn: ({ signal }) =>
      apiGet<SearchResponse>(
        `/api/search?q=${encodeURIComponent(query)}&limit=${limit}`,
        signal,
      ),
    enabled: query.length > 0,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/saved_searches — list saved searches, newest first. */
export function useSavedSearches() {
  return useQuery<SavedSearchesList>({
    queryKey: ["saved_searches"],
    queryFn: ({ signal }) =>
      apiGet<SavedSearchesList>("/api/saved_searches", signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/saved_searches — add a saved search (confirm-gated write). */
export function useSavedSearchAdd() {
  const qc = useQueryClient();
  return useMutation<
    SavedSearchAddResult,
    Error,
    { name: string; query: string; site_id?: string; status?: string }
  >({
    mutationFn: (body) =>
      apiPost<SavedSearchAddResult>("/api/saved_searches", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved_searches"] }),
  });
}

/** DELETE /api/saved_searches/{id} — remove (confirm-gated; typed token). */
export function useSavedSearchDelete() {
  const qc = useQueryClient();
  return useMutation<OkResult, Error, number>({
    mutationFn: (id) => apiDelete<OkResult>(`/api/saved_searches/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved_searches"] }),
  });
}

/** PATCH /api/saved_searches/{id} — partial update (F3.1 action lane).
 *  Body carries the subset of fields to change; the backend validates and
 *  drops an out-of-range action rather than coercing it. */
export function useSavedSearchUpdate() {
  const qc = useQueryClient();
  return useMutation<OkResult, Error, { id: number; fields: Record<string, unknown> }>({
    mutationFn: ({ id, fields }) =>
      apiPatch<OkResult>(`/api/saved_searches/${id}`, fields),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["saved_searches"] }),
  });
}

/** POST /api/saved_searches/{id}/run — run one saved search now. Already
 *  classified outside the legacy-only baseline; wired here for functional
 *  parity of the panel (one-step confirm). */
export function useSavedSearchRun() {
  const qc = useQueryClient();
  return useMutation<SavedSearchRunResult, Error, number>({
    mutationFn: (id) =>
      apiPost<SavedSearchRunResult>(`/api/saved_searches/${id}/run`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["saved_searches"] });
      qc.invalidateQueries({ queryKey: ["saved_search_digest"] });
    },
  });
}

/** GET /api/saved_searches/digest — "what's new since" match counts. */
export function useSavedSearchDigest(hoursBack = 168) {
  return useQuery<SavedSearchDigest>({
    queryKey: ["saved_search_digest", hoursBack],
    queryFn: ({ signal }) =>
      apiGet<SavedSearchDigest>(
        `/api/saved_searches/digest?hours_back=${hoursBack}`,
        signal,
      ),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/ui_events — the frontend telemetry ingest. The SPA logs a
 *  real (tiny) page-view event when the History route mounts, making the
 *  SPA a genuine client of the ingest pipeline (server-side tier gating
 *  still applies — basic-tier events only). Fire-and-forget. */
export function useUiEventPageView(routeName: string) {
  useEffect(() => {
    apiPost<UiEventsIngestResult>("/api/ui_events", {
      events: [
        {
          ts: Date.now(),
          category: "nav",
          event: "page_view",
          data: { route: routeName },
        },
      ],
    }).catch(() => {
      /* telemetry is best-effort — never surface an error for it */
    });
  }, [routeName]);
}

/** GET /api/ui_events/download — stream today's ui_events.log as a file.
 *  fetch + blob (not window.open) so the CSRF-free GET still rides the
 *  session cookie and errors surface as JSON. */
export async function downloadUiEventsLog(): Promise<void> {
  const r = await fetch("/api/ui_events/download", {
    credentials: "same-origin",
  });
  if (!r.ok) {
    let msg = `download failed (${r.status})`;
    try {
      const body = await r.json();
      if (body?.error) msg = body.error;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const m = /filename="?([^"]+)"?/.exec(cd);
  const name = m ? m[1] : "ui_events.log";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
