import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  BulkSitesResult,
  ConcurrentResult,
  CrashOrphansResult,
  CrashOpResult,
  FileRevealResult,
  RateLimitStatus,
  RetryPolicyResult,
  RunnersAllResult,
} from "@/lib/api-types";

// ── T4 operational-controls tranche (v3.66.207, batched with T3) ────
//
// Ports the 11 legacy-only sites-bulk/runners/concurrent/rate_limit/
// retry_policy/crash_recovery/file families into the EXISTING SPA
// routes (Maintenance gains the Operations sections; ImportsCenter
// gains bulk site import), as required by the current operations contract /
// T4. FULL "/api/…" literals throughout; the crash-recovery action and
// concurrency setter use inline `${…}` path params, which normalise to
// the same parameterised endpoints the legacy baseline carries.
//
// Risk model: pause_all / resume_all and crash-recovery delete are
// dangerous-selection class → TYPED confirm. Crash ignore/resume,
// concurrency set, file reveal, and bulk site import are one-step
// confirms. Reads (scan, rate-limit, retry-policy) are ungated. No
// secrets in this tranche.

/** POST /api/runners/pause_all — stop every runner dequeueing (typed-confirm). */
export function usePauseAll() {
  const qc = useQueryClient();
  return useMutation<RunnersAllResult, Error, void>({
    mutationFn: () => apiPost<RunnersAllResult>("/api/runners/pause_all", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["queue"] }),
  });
}

/** POST /api/runners/resume_all — restart all paused runners (typed-confirm). */
export function useResumeAll() {
  const qc = useQueryClient();
  return useMutation<RunnersAllResult, Error, void>({
    mutationFn: () => apiPost<RunnersAllResult>("/api/runners/resume_all", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["queue"] }),
  });
}

/** POST /api/concurrent/{sid} — set a site's max_concurrent (1–20). */
export function useSetConcurrent() {
  return useMutation<ConcurrentResult, Error, { sid: string; n: number }>({
    mutationFn: ({ sid, n }) =>
      apiPost<ConcurrentResult>(`/api/concurrent/${encodeURIComponent(sid)}`, { n }),
  });
}

/** GET /api/rate_limit/status — live limiter snapshot. */
export function useRateLimitStatus() {
  return useQuery<RateLimitStatus>({
    queryKey: ["rate_limit", "status"],
    queryFn: ({ signal }) => apiGet<RateLimitStatus>("/api/rate_limit/status", signal),
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/retry_policy — configured backoff curves per failure class. */
export function useRetryPolicy() {
  return useQuery<RetryPolicyResult>({
    queryKey: ["retry_policy"],
    queryFn: ({ signal }) => apiGet<RetryPolicyResult>("/api/retry_policy", signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/crash_recovery/scan — orphan .part files across all sites. */
export function useCrashScan() {
  return useQuery<CrashOrphansResult>({
    queryKey: ["crash_recovery", "scan"],
    queryFn: ({ signal }) => apiGet<CrashOrphansResult>("/api/crash_recovery/scan", signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

export type CrashAction = "delete" | "ignore" | "resume";

/** POST /api/crash_recovery/{action} — act on one orphan .part. The action is
 *  a closed union (delete | ignore | resume); delete is typed-confirm, the
 *  other two one-step. The inline template normalises to the baseline's
 *  parameterised /api/crash_recovery/{x} endpoint. */
export function useCrashAction() {
  const qc = useQueryClient();
  return useMutation<CrashOpResult, Error, { action: CrashAction; path: string }>({
    mutationFn: ({ action, path }) =>
      apiPost<CrashOpResult>(`/api/crash_recovery/${action}`, { path }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["crash_recovery"] }),
  });
}

/** POST /api/file/reveal — open the OS file manager at a path (one-step). */
export function useFileReveal() {
  return useMutation<FileRevealResult, Error, { path: string }>({
    mutationFn: (body) => apiPost<FileRevealResult>("/api/file/reveal", body),
  });
}

/** POST /api/sites/bulk_csv — bulk-create sites from pasted CSV text
 *  (one-step confirm; per-row results come back for review). */
export function useBulkSitesCsv() {
  const qc = useQueryClient();
  return useMutation<BulkSitesResult, Error, { csv: string }>({
    mutationFn: (body) => apiPost<BulkSitesResult>("/api/sites/bulk_csv", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sites"] }),
  });
}

/** Template downloads for bulk site import — browser-native GET downloads
 *  of /api/sites/csv_template and /api/sites/xlsx_template. */
export function downloadSitesTemplate(kind: "csv" | "xlsx"): void {
  const a = document.createElement("a");
  a.href = kind === "csv" ? "/api/sites/csv_template" : "/api/sites/xlsx_template";
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}
