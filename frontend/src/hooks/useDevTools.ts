// useDevTools — T10 (v3.66.211) developer/diagnostics wiring for the
// existing /settings/advanced route (Advanced). FULL /api/ literals.
//
// Handler-correct shapes re-derived from bulk_downloader/app.py at 210:
//   GET  /api/dev/enabled            {enabled}  ← always available; gates
//                                     whether the Dev console renders.
//   GET  /api/dev/discover           test-file AST inventory (dev-guarded)
//   POST /api/dev/run                {target,kind} → 202 {ok,run_id}
//                                     (dev-guarded, CSRF). B-tier.
//   GET  /api/dev/runs/<run_id>      {state,output,summary} (dev-guarded)
//   GET  /api/plugins/status         loaded-plugin status
//   GET  /api/plugins/events         {events:[...]} (hook-event docs)
//   GET  /api/synthetic_tests/list   {fixtures:[...]}
//   POST /api/synthetic_tests/run_all  → per-site pass/fail (CSRF; safe HAR
//                                     replay, no network). B-tier.
//   GET  /api/i18n/load/<lang>       {lang,strings}

import { useMutation, useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  DevDiscoverResponse,
  DevEnabledResponse,
  DevRunBody,
  DevRunStartResult,
  DevRunStatus,
  I18nLoadResult,
  PluginsEvents,
  PluginsStatus,
  SyntheticFixturesList,
  SyntheticRunAllResult,
} from "@/lib/api-types";

export function useDevEnabled() {
  return useQuery<DevEnabledResponse, Error>({
    queryKey: ["dev", "enabled"],
    staleTime: 300_000,
    queryFn: ({ signal }) => apiGet<DevEnabledResponse>("/api/dev/enabled", signal),
  });
}

export function useDevDiscover(enabled: boolean) {
  return useQuery<DevDiscoverResponse, Error>({
    queryKey: ["dev", "discover"],
    enabled,
    queryFn: ({ signal }) => apiGet<DevDiscoverResponse>("/api/dev/discover", signal),
  });
}

/** Start a dev test run — B-tier confirm. Returns 202 + run_id; poll with
 *  useDevRunStatus. */
export function useDevRun() {
  return useMutation<DevRunStartResult, Error, DevRunBody>({
    mutationFn: (body) => apiPost<DevRunStartResult>("/api/dev/run", body),
  });
}

export function useDevRunStatus(runId: string | null) {
  return useQuery<DevRunStatus, Error>({
    queryKey: ["dev", "runs", runId],
    enabled: !!runId,
    refetchInterval: (q) => {
      const state = (q.state.data as DevRunStatus | undefined)?.state;
      return state && ["done", "error", "cancelled"].includes(state) ? false : 1500;
    },
    queryFn: ({ signal }) =>
      apiGet<DevRunStatus>(`/api/dev/runs/${encodeURIComponent(runId || "")}`, signal),
  });
}

export function usePluginsStatus() {
  return useQuery<PluginsStatus, Error>({
    queryKey: ["plugins", "status"],
    queryFn: ({ signal }) => apiGet<PluginsStatus>("/api/plugins/status", signal),
  });
}

export function usePluginsEvents() {
  return useQuery<PluginsEvents, Error>({
    queryKey: ["plugins", "events"],
    staleTime: 300_000,
    queryFn: ({ signal }) => apiGet<PluginsEvents>("/api/plugins/events", signal),
  });
}

export function useSyntheticFixtures() {
  return useQuery<SyntheticFixturesList, Error>({
    queryKey: ["synthetic_tests", "list"],
    queryFn: ({ signal }) => apiGet<SyntheticFixturesList>("/api/synthetic_tests/list", signal),
  });
}

/** Run every synthetic fixture (safe HAR replay) — B-tier confirm. */
export function useSyntheticRunAll() {
  return useMutation<SyntheticRunAllResult, Error, void>({
    mutationFn: () => apiPost<SyntheticRunAllResult>("/api/synthetic_tests/run_all", {}),
  });
}

export function useI18nLoad(lang: string | null) {
  return useQuery<I18nLoadResult, Error>({
    queryKey: ["i18n", "load", lang],
    enabled: !!lang,
    queryFn: ({ signal }) =>
      apiGet<I18nLoadResult>(`/api/i18n/load/${encodeURIComponent(lang || "")}`, signal),
  });
}
