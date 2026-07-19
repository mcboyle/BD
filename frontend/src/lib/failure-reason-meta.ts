import type { PillTone } from "@/components/StatusPill";

// Presentation copy for a job's failure reason_code (Cut 4).
//
// The AUTHORITATIVE classification lives server-side in
// bulk_downloader/failure_reasons.py — the runner persists the stable
// `reason_code` on job_runs and `/api/runs?status=failed` surfaces it. This map
// is display-only: it turns that stable code into a title, a one-line operator
// action, a retry posture, and a pill tone. Keep the codes in sync with the
// backend `_REASONS` keys (transient | rate_limited | auth | permanent).

export interface ReasonMeta {
  title: string;
  action: string;
  retryable: boolean;
  tone: PillTone;
}

const REASON_META: Record<string, ReasonMeta> = {
  transient: {
    title: "Temporary network/CDN error",
    action: "Usually clears on its own — it will auto-retry.",
    retryable: true,
    tone: "amber",
  },
  rate_limited: {
    title: "Rate limited by the site",
    action: "Backing off and auto-retrying. Lower this site's concurrency if it persists.",
    retryable: true,
    tone: "amber",
  },
  auth: {
    title: "Authentication failed",
    action: "Re-login or refresh this site's credentials — it won't auto-retry until login works.",
    retryable: false,
    tone: "red",
  },
  permanent: {
    title: "Permanent error",
    action: "This URL is gone or blocked — it won't auto-retry. Remove or replace it.",
    retryable: false,
    tone: "red",
  },
};

const UNKNOWN: ReasonMeta = {
  title: "Unclassified failure",
  action: "See the event log below for details.",
  retryable: false,
  tone: "neutral",
};

/** Look up display metadata for a reason_code. Null/unknown -> a safe default. */
export function reasonMeta(reasonCode?: string | null): ReasonMeta {
  if (!reasonCode) return UNKNOWN;
  return REASON_META[reasonCode] ?? UNKNOWN;
}
