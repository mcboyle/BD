// useNeedsReview — F2.3 (v3.66.265) screenshot-triage actions.
//
// The needs_review triage tab (routes/NeedsReview.tsx) reads the stalled
// jobs via the existing useHistory({ status: "needs_review" }) and drives
// three per-site operator actions through endpoints that PREDATE T11
// (behaviour already pinned by test_v3_43_23_quick_wins / test_v3_49_phase2
// / test_d3_u5_queue — F2.3 adds NO new write paths):
//
//   approve -> POST /api/sites/${sid}/bulk_approve {urls:[url]}
//              Operator override: accept the flagged item, bypassing the
//              min_resolution quality gate, and re-download it. A deliberate
//              QUALITY-gate override — it does NOT touch T11's auto-submit /
//              challenge interposition (a separate surface, useApproval.ts).
//   retry   -> POST /api/sites/${sid}/retry_one  {url}
//              Re-queue the stalled job for another attempt.
//   skip    -> POST /api/sites/${sid}/jobs/mark  {url, status:"failed"}
//              Dismiss it — marks failed so the auto-retry scanner stops
//              bumping it back to pending (the runner's "Mark failed"
//              semantic; see api_jobs_mark).
//
// FULL "/api/" literals (scanner credit — gui_parity_inventory flips
// bulk_approve + jobs/mark spa_wired; raw ${sid}, NOT a concatenated base
// var). All three ride apiPost (X-CSRF-Token on mutations), never a raw
// fetch(). Each success invalidates the history query so the cleared row
// drops out of the needs_review list.

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiPost } from "@/lib/api-client";
import type { OkResult } from "@/lib/api-types";

export interface TriageTarget {
  sid: string;
  url: string;
}

export function useNeedsReview() {
  const qc = useQueryClient();
  // useHistory keys are ["history", qs]; a prefix invalidation refetches
  // every history view (partial match is react-query's default).
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["history"] });
  };

  const approve = useMutation<OkResult, Error, TriageTarget>({
    mutationFn: ({ sid, url }) =>
      apiPost<OkResult>(`/api/sites/${sid}/bulk_approve`, { urls: [url] }),
    onSuccess: invalidate,
  });

  const retry = useMutation<OkResult, Error, TriageTarget>({
    mutationFn: ({ sid, url }) =>
      apiPost<OkResult>(`/api/sites/${sid}/retry_one`, { url }),
    onSuccess: invalidate,
  });

  const skip = useMutation<OkResult, Error, TriageTarget>({
    mutationFn: ({ sid, url }) =>
      apiPost<OkResult>(`/api/sites/${sid}/jobs/mark`, {
        url,
        status: "failed",
      }),
    onSuccess: invalidate,
  });

  return { approve, retry, skip };
}
