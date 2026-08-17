// useLive — T9a (v3.66.212) live-recorder wiring: status · recordings ·
// watch · unwatch. Replaces the legacy static/live_recorder.js pill/panel.
//
// Carries all four live endpoint families as full /api/ literals so
// gui_parity_inventory.py sees the SPA consumers. Handler-correct shapes
// re-derived from bulk_downloader/app_live_recorder.py at 211:
//
//   GET  /api/live/status      {ok,available,preferred_backend,backends,
//                               active_count,max_active,counts,tunables}.
//                               Cheap poll → useQuery w/ refetchInterval (the
//                               legacy panel polled 5s open / 20s closed).
//   GET  /api/live/recordings  ?include_finished  {ok,recordings:[...]}.
//   POST /api/live/watch       {url,output_dir,site_override?,room_override?}
//                              → {ok,recording_id} | 400 url/dir required |
//                               409 already_watching | 503 no_backend/
//                               too_many_active. CSRF. Arms a recording that
//                               consumes one of max_active slots → the section
//                               gates it B-tier (confirm, never one-click).
//   POST /api/live/unwatch     {recording_id} → {ok} | 404. CSRF. Cancels an
//                               active recording → B-tier confirm.
//
// (The 5th T9a literal, POST /api/stream/token/<int:hid>, already lives in
// routes/Library.tsx where history rows are; T9a only literalized its concat.)

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  LiveRecordingsResponse,
  LiveStatus,
  LiveParseUrlResult,
  LiveUnwatchResult,
  LiveWatchBody,
  LiveWatchResult,
} from "@/lib/api-types";

export function useLiveStatus() {
  return useQuery<LiveStatus, Error>({
    queryKey: ["live", "status"],
    refetchInterval: 5_000,
    queryFn: ({ signal }) => apiGet<LiveStatus>("/api/live/status", signal),
  });
}

export function useLiveRecordings() {
  return useQuery<LiveRecordingsResponse, Error>({
    queryKey: ["live", "recordings"],
    queryFn: ({ signal }) =>
      apiGet<LiveRecordingsResponse>("/api/live/recordings", signal),
  });
}

/** Arms a resource-bounded recording — B-tier confirm at the section. */
export function useLiveWatch() {
  const qc = useQueryClient();
  return useMutation<LiveWatchResult, Error, LiveWatchBody>({
    mutationFn: (body) => apiPost<LiveWatchResult>("/api/live/watch", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["live", "status"] });
      qc.invalidateQueries({ queryKey: ["live", "recordings"] });
    },
  });
}

/**
 * Checks whether a URL is recognized as a live-cam URL WITHOUT arming a watch.
 * v3.66.754c -- wires the previously-dark POST /api/live/parse_url so the operator
 * can validate a URL before committing to a watch. Read-only server-side (no state
 * change), so no cache invalidation.
 */
export function useLiveParseUrl() {
  return useMutation<LiveParseUrlResult, Error, { url: string }>({
    mutationFn: (body) =>
      apiPost<LiveParseUrlResult>("/api/live/parse_url", body),
  });
}

/** Cancels an active recording — B-tier confirm at the section. */
export function useLiveUnwatch() {
  const qc = useQueryClient();
  return useMutation<LiveUnwatchResult, Error, { recording_id: string }>({
    mutationFn: (body) => apiPost<LiveUnwatchResult>("/api/live/unwatch", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["live", "status"] });
      qc.invalidateQueries({ queryKey: ["live", "recordings"] });
    },
  });
}
