import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  AppriseSettingsResponse,
  AppriseSettingsPatch,
  AppriseValidateResult,
  AppriseTestResult,
  TgStatusResponse,
  TgSettingsResponse,
  TgSettingsPatch,
  TgTestResult,
  ActiveAlertsResponse,
} from "@/lib/api-types";

// ── T7 notifications tranche (v3.66.210) ────────────────────────────
//
// Ports the 7 legacy-only notify/tg/alerts families into the SPA
// /notifications route.
// Every call uses the FULL "/api/…" string literal (NOT a concatenated
// base var) so gui_parity_inventory.py sees the SPA endpoint consumer
// (ratchet 41 → 34).
//
// SECRETS ARE WRITE-ONLY ((R) rule): the apprise URLs and the tg bot
// token are POSTed but NEVER read back — the GETs surface only a
// *_set flag + count. The matching capture-body redaction (apprise
// URL tokens, tg token, and the code/k analytics keys) lands in
// capture_redact.py in the SAME cut, so a wired secret input can never
// outrun its redaction.

const SLOW = 30_000;

/** GET /api/notify/apprise/settings — masked (urls write-only). */
export function useAppriseSettings() {
  return useQuery<AppriseSettingsResponse>({
    queryKey: ["notify", "apprise", "settings"],
    queryFn: ({ signal }) =>
      apiGet<AppriseSettingsResponse>("/api/notify/apprise/settings", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/notify/apprise/settings — write-only URLs + flags. */
export function useSaveAppriseSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: AppriseSettingsPatch) =>
      apiPost<AppriseSettingsResponse>("/api/notify/apprise/settings", patch),
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["notify", "apprise", "settings"] }),
  });
}

/** POST /api/notify/apprise/validate — validate URLs without sending. */
export function useAppriseValidate() {
  return useMutation({
    mutationFn: (urls: string) =>
      apiPost<AppriseValidateResult>("/api/notify/apprise/validate", { urls }),
  });
}

/** POST /api/notify/apprise/test — send a test notification. */
export function useAppriseTest() {
  return useMutation({
    mutationFn: (args: { title?: string; body?: string }) =>
      apiPost<AppriseTestResult>("/api/notify/apprise/test", args),
  });
}

/** GET /api/tg/status — bot health snapshot. */
export function useTgStatus() {
  return useQuery<TgStatusResponse>({
    queryKey: ["tg", "status"],
    queryFn: ({ signal }) => apiGet<TgStatusResponse>("/api/tg/status", signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** GET /api/tg/settings — masked (token write-only). */
export function useTgSettings() {
  return useQuery<TgSettingsResponse>({
    queryKey: ["tg", "settings"],
    queryFn: ({ signal }) =>
      apiGet<TgSettingsResponse>("/api/tg/settings", signal),
    refetchOnWindowFocus: false,
    retry: 0,
  });
}

/** POST /api/tg/settings — token write-only (only sent if non-empty). */
export function useSaveTgSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: TgSettingsPatch) =>
      apiPost<TgSettingsResponse>("/api/tg/settings", patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tg", "settings"] });
      qc.invalidateQueries({ queryKey: ["tg", "status"] });
    },
  });
}

/** POST /api/tg/test — send a manual test to allowlisted chats. */
export function useTgTest() {
  return useMutation({
    mutationFn: (message?: string) =>
      apiPost<TgTestResult>("/api/tg/test", { message }),
  });
}

/** GET /api/alerts/active — currently firing alerts (bell badge). */
export function useActiveAlerts(hours = 24) {
  return useQuery<ActiveAlertsResponse>({
    queryKey: ["alerts", "active", hours],
    queryFn: ({ signal }) =>
      apiGet<ActiveAlertsResponse>(`/api/alerts/active?hours=${hours}`, signal),
    refetchInterval: SLOW,
    refetchOnWindowFocus: false,
    retry: 0,
  });
}
