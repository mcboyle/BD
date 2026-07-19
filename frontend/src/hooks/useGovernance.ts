// useGovernance — T5 (v3.66.208) governance/data-lifecycle wiring.
//
// Carries ALL 12 T5 legacy-parity literals (retention 3 · rights 3 ·
// scheduled_exports 4 · diagnostics_bundle 2) as FULL /api/ literals so
// tools/legacy_parity.py credits them spa_wired. Inline ${x} only on true
// path params (normalises to the baseline's {x}).
//
// Handler-correct shapes (re-derived from bulk_downloader/app.py at 207):
//   GET  /api/retention/preview/<sid>     {site_id, candidate_count, total_bytes,
//                                          candidates[<=200], retention_days,
//                                          retention_max_gb, retention_keep_tagged_with}
//   POST /api/retention/apply             body {dry_run} (DEFAULT TRUE server-side);
//                                          returns retention module summary
//   GET  /api/retention/audit?limit&dry_run  {audit:[{deleted_at, dry_run, site_id,
//                                          file_path, reason}]}
//   GET  /api/rights/blocklist            {blocks:[...]}
//   POST /api/rights/remove/<bid>         {ok, ...}
//   GET  /api/rights/audit?limit&kind     {entries:[...]}
//   GET  /api/scheduled_exports/list      {schedules:[...]}
//   POST /api/scheduled_exports/add       body {label, format, destination,
//                                          cadence_hours, filter_dict?, retention_count?}
//                                          → {ok, id} | 400 {ok:false,error}
//   POST /api/scheduled_exports/remove/<sid>  {ok}
//   POST /api/scheduled_exports/run_now   run_due_exports summary
//   GET  /api/diagnostics_bundle/preview  full bundle JSON
//   GET  /api/diagnostics_bundle/download zip stream (Content-Disposition)
//
// Gating contract (preview-first UX ported VERBATIM from legacy
// static/app.js openRetention): retention is dry-run-by-default — the
// REAL apply (dry_run=false, deletes files) is typed-confirm at the page
// level ("APPLY RETENTION"); dry-run apply, previews, audits and lists are
// ungated reads/simulations. rights remove + sched remove/run_now are
// one-step confirm at the page level. Nothing here is one-click.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  OkResult,
  RetentionApplyResult,
  RetentionAuditResult,
  RetentionPreview,
  RightsAuditResult,
  RightsBlocklist,
  SchedExportAddResult,
  SchedExportRunNowResult,
  SchedExportsList,
} from "@/lib/api-types";

export function useRetentionPreview(siteId: string | null) {
  return useQuery<RetentionPreview, Error>({
    queryKey: ["retention", "preview", siteId],
    enabled: !!siteId,
    queryFn: ({ signal }) =>
      apiGet<RetentionPreview>(
        `/api/retention/preview/${encodeURIComponent(siteId || "")}`,
        signal,
      ),
  });
}

export function useRetentionAudit(limit = 50) {
  return useQuery<RetentionAuditResult, Error>({
    queryKey: ["retention", "audit", limit],
    queryFn: ({ signal }) =>
      apiGet<RetentionAuditResult>(`/api/retention/audit?limit=${limit}`, signal),
  });
}

/** dry_run=true is a safe simulation; dry_run=false DELETES FILES and is
 *  typed-confirm-gated by the caller (Maintenance "APPLY RETENTION").
 *  Preview-verbatim (F4.2): pass confirmIds (the ids from the preceding
 *  preview) and siteId so the server deletes only the intersection of
 *  those ids with the live candidates — apply can never delete more than
 *  the preview disclosed. Omitting confirmIds runs the legacy unbound
 *  all-sites sweep. */
export function useRetentionApply() {
  const qc = useQueryClient();
  return useMutation<
    RetentionApplyResult,
    Error,
    { dryRun: boolean; confirmIds?: number[]; siteId?: string }
  >({
    mutationFn: ({ dryRun, confirmIds, siteId }) => {
      const body: Record<string, unknown> = { dry_run: dryRun };
      if (confirmIds !== undefined) body.confirm_ids = confirmIds;
      if (siteId !== undefined) body.site_id = siteId;
      return apiPost<RetentionApplyResult>("/api/retention/apply", body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["retention", "audit"] });
    },
  });
}

export function useRightsBlocklist() {
  return useQuery<RightsBlocklist, Error>({
    queryKey: ["rights", "blocklist"],
    queryFn: ({ signal }) => apiGet<RightsBlocklist>("/api/rights/blocklist", signal),
  });
}

export function useRightsAudit(limit = 100) {
  return useQuery<RightsAuditResult, Error>({
    queryKey: ["rights", "audit", limit],
    queryFn: ({ signal }) =>
      apiGet<RightsAuditResult>(`/api/rights/audit?limit=${limit}`, signal),
  });
}

export function useRightsRemove() {
  const qc = useQueryClient();
  return useMutation<OkResult, Error, { bid: number }>({
    mutationFn: ({ bid }) => apiPost<OkResult>(`/api/rights/remove/${bid}`, {}),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["rights", "blocklist"] });
      qc.invalidateQueries({ queryKey: ["rights", "audit"] });
    },
  });
}

export function useSchedExports() {
  return useQuery<SchedExportsList, Error>({
    queryKey: ["sched_exports", "list"],
    queryFn: ({ signal }) => apiGet<SchedExportsList>("/api/scheduled_exports/list", signal),
  });
}

export interface SchedExportAddBody {
  label: string;
  format: string; // eol/csv/json/ndjson/m3u — server validates
  destination: string;
  cadence_hours: number;
  retention_count?: number;
}

export function useSchedExportAdd() {
  const qc = useQueryClient();
  return useMutation<SchedExportAddResult, Error, SchedExportAddBody>({
    mutationFn: (body) => apiPost<SchedExportAddResult>("/api/scheduled_exports/add", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sched_exports", "list"] }),
  });
}

export function useSchedExportRemove() {
  const qc = useQueryClient();
  return useMutation<OkResult, Error, { id: number }>({
    mutationFn: ({ id }) => apiPost<OkResult>(`/api/scheduled_exports/remove/${id}`, {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sched_exports", "list"] }),
  });
}

export function useSchedExportRunNow() {
  const qc = useQueryClient();
  return useMutation<SchedExportRunNowResult, Error, void>({
    mutationFn: () => apiPost<SchedExportRunNowResult>("/api/scheduled_exports/run_now", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sched_exports", "list"] }),
  });
}

/** Diagnostics bundle inline preview — the full JSON bundle (can be large). */
export function useDiagBundlePreview(enabled: boolean) {
  return useQuery<Record<string, unknown>, Error>({
    queryKey: ["diag_bundle", "preview"],
    enabled,
    staleTime: 60_000,
    queryFn: ({ signal }) =>
      apiGet<Record<string, unknown>>("/api/diagnostics_bundle/preview", signal),
  });
}

/** GET file download (CSRF-exempt GET; fetch+blob so the session cookie
 *  rides along and errors surface as JSON — same pattern as
 *  downloadUiEventsLog). */
export async function downloadDiagnosticsBundle(): Promise<void> {
  const r = await fetch("/api/diagnostics_bundle/download", {
    credentials: "same-origin",
  });
  if (!r.ok) {
    let msg = `download failed (${r.status})`;
    try {
      const body = (await r.json()) as { error?: string };
      if (body?.error) msg = body.error;
    } catch {
      /* ignore */
    }
    throw new Error(msg);
  }
  const blob = await r.blob();
  const cd = r.headers.get("Content-Disposition") || "";
  const m = /filename="?([^"]+)"?/.exec(cd);
  const name = m ? m[1] : "bd-diagnostics.zip";
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
