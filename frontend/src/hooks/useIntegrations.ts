// useIntegrations — T6 (v3.66.208) external-integrations wiring.
//
// Carries ALL 14 T6 legacy-parity literals (plex_advanced 6 · tpdb 2 ·
// subtitles 1 · thumbnail_sheets 1 · marketplace 1 · jsonapi 1 · ai 2) as
// FULL /api/ literals. Read-mostly tranche; the writes (tpdb apply,
// subtitles fetch, thumbnail sheet generate, marketplace export) are
// one-step-confirm at the page level — never one-click.
//
// Handler-correct shapes (re-derived from bulk_downloader/app.py at 207):
//   GET  /api/plex_advanced/status                {configured, ...}
//   GET  /api/plex_advanced/server_info/<sid>     server info dict | {error}
//   GET  /api/plex_advanced/library_stats/<sid>
//   GET  /api/plex_advanced/recently_added/<sid>?limit
//   GET  /api/plex_advanced/on_deck/<sid>
//   GET  /api/plex_advanced/search/<sid>?q
//   POST /api/tpdb/lookup/<hid>                   {ok, matches?...}
//   POST /api/tpdb/apply/<hid>    body {match}    {ok, ...}
//   POST /api/subtitles/fetch/<hid>               {ok, ...}
//   POST /api/thumbnail_sheets/contact_sheet/<hid> {ok, path?...}
//   POST /api/marketplace/export/<sid>            {ok, ...}
//   POST /api/jsonapi/probe       body {url}      probe result
//   GET  /api/ai/status                           {enabled, ...}
//   POST /api/ai/models                           {ok, models?...}

import { useMutation, useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";

// ── plex_advanced (per-site reads; sid = a configured site id) ────────
export function usePlexStatus() {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["plex_adv", "status"],
    queryFn: ({ signal }) => apiGet<Record<string, unknown>>("/api/plex_advanced/status", signal),
  });
}

export function usePlexServerInfo(sid: string | null) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["plex_adv", "server_info", sid],
    enabled: !!sid,
    queryFn: ({ signal }) =>
      apiGet<Record<string, unknown>>(
        `/api/plex_advanced/server_info/${encodeURIComponent(sid || "")}`,
        signal,
      ),
  });
}

export function usePlexLibraryStats(sid: string | null) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["plex_adv", "library_stats", sid],
    enabled: !!sid,
    queryFn: ({ signal }) =>
      apiGet<Record<string, unknown>>(
        `/api/plex_advanced/library_stats/${encodeURIComponent(sid || "")}`,
        signal,
      ),
  });
}

export function usePlexRecentlyAdded(sid: string | null) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["plex_adv", "recently_added", sid],
    enabled: !!sid,
    queryFn: ({ signal }) =>
      apiGet<Record<string, unknown>>(
        `/api/plex_advanced/recently_added/${encodeURIComponent(sid || "")}`,
        signal,
      ),
  });
}

export function usePlexOnDeck(sid: string | null) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["plex_adv", "on_deck", sid],
    enabled: !!sid,
    queryFn: ({ signal }) =>
      apiGet<Record<string, unknown>>(
        `/api/plex_advanced/on_deck/${encodeURIComponent(sid || "")}`,
        signal,
      ),
  });
}

export function usePlexSearch(sid: string | null, q: string) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["plex_adv", "search", sid, q],
    enabled: !!sid && q.trim().length > 0,
    queryFn: ({ signal }) =>
      apiGet<Record<string, unknown>>(
        `/api/plex_advanced/search/${encodeURIComponent(sid || "")}?q=${encodeURIComponent(q)}`,
        signal,
      ),
  });
}

// ── tpdb (per-history-row metadata; lookup is a read-via-POST returning
//    {ok, result, history_id}; apply WRITES an .nfo sidecar and takes the
//    lookup result back as body.metadata → one-step confirm at the page) ─
export function useTpdbLookup() {
  return useMutation<
    { ok?: boolean; result?: Record<string, unknown>; history_id?: number; error?: string },
    Error,
    { hid: number }
  >({
    mutationFn: ({ hid }) =>
      apiPost<{ ok?: boolean; result?: Record<string, unknown>; history_id?: number; error?: string }>(
        `/api/tpdb/lookup/${hid}`,
        {},
      ),
  });
}

export function useTpdbApply() {
  return useMutation<
    OkResult & { applied?: Record<string, unknown> },
    Error,
    { hid: number; metadata: Record<string, unknown> }
  >({
    mutationFn: ({ hid, metadata }) =>
      apiPost<OkResult & { applied?: Record<string, unknown> }>(`/api/tpdb/apply/${hid}`, { metadata }),
  });
}

// ── subtitles / thumbnail sheets / marketplace (one-step confirm) ─────
export function useSubtitlesFetch() {
  return useMutation<OkResult, Error, { hid: number }>({
    mutationFn: ({ hid }) => apiPost<OkResult>(`/api/subtitles/fetch/${hid}`, {}),
  });
}

export function useContactSheet() {
  return useMutation<OkResult & { path?: string }, Error, { hid: number }>({
    mutationFn: ({ hid }) =>
      apiPost<OkResult & { path?: string }>(`/api/thumbnail_sheets/contact_sheet/${hid}`, {}),
  });
}

export function useMarketplaceExport() {
  return useMutation<OkResult, Error, { sid: string }>({
    mutationFn: ({ sid }) =>
      apiPost<OkResult>(`/api/marketplace/export/${encodeURIComponent(sid)}`, {}),
  });
}

// ── jsonapi probe (read-via-POST) + ai status/models ─────────────────
export function useJsonapiProbe() {
  return useMutation<Record<string, unknown>, Error, { url: string }>({
    // v3.66.743 — the endpoint reads `site_root` (required); this hook sent
    // `{url}` since it shipped, so every probe 400'd "site_root required"
    // while the wiring ledgers scored the control WIRED. Found the moment the
    // body-contract extractor's denominator was fixed (it had never scanned
    // this file). The 724/726 class, one more time.
    mutationFn: ({ url }) => apiPost<Record<string, unknown>>("/api/jsonapi/probe", { site_root: url }),
  });
}

export interface AiBootModelStatus {
  name?: string;
  state?: string;
  resident?: boolean;
  size?: number;
  size_vram?: number;
  gpu_ratio?: number;
}

export interface AiBootReadiness {
  state?: string;
  error_code?: string;
  models?: {
    text?: AiBootModelStatus;
    vision?: AiBootModelStatus;
  };
}

export interface AiStatus extends Record<string, unknown> {
  enabled?: boolean;
  boot_readiness?: AiBootReadiness;
}

export function useAiStatus() {
  return useQuery<AiStatus, Error>({
    queryKey: ["ai", "status"],
    queryFn: ({ signal }) => apiGet<AiStatus>("/api/ai/status", signal),
  });
}

// Cut 7 (7.1): optional draft-endpoint vars so the model list can reflect the
// endpoint being EDITED in Settings (not just the saved one). Called with no
// args it still POSTs {} — back-compat with the existing Integrations consumer.
export interface AiModelsVars {
  provider?: string;
  endpoint?: string;
  api_key?: string;
}

export function useAiModels() {
  return useMutation<Record<string, unknown>, Error, AiModelsVars | void>({
    mutationFn: (vars) =>
      apiPost<Record<string, unknown>>("/api/ai/models", vars ?? {}),
  });
}

// Cut 7 (Track A): read-only, fail-open integration health rollup.
export interface IntegrationsHealth {
  ok: boolean;
  integrations: Record<string, Record<string, unknown>>;
}
export function useIntegrationsHealth() {
  return useQuery<IntegrationsHealth, Error>({
    queryKey: ["integrations", "health"],
    queryFn: ({ signal }) =>
      apiGet<IntegrationsHealth>("/api/integrations/health", signal),
  });
}

// Cut 7 (Track A): secret USAGE map — references by name only, never values.
export interface SecretsUsage {
  ok: boolean;
  stored_keys: string[];
  usage: Record<string, string[]>;
  unreferenced: string[];
}
export function useSecretsUsage() {
  return useQuery<SecretsUsage, Error>({
    queryKey: ["secrets", "usage"],
    queryFn: ({ signal }) => apiGet<SecretsUsage>("/api/secrets/usage", signal),
  });
}
