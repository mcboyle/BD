// useApproval — T11 (v3.66.264) per-site approval gate.
//
// Ports the legacy static/approval_ui.js auto-submit / post-reveal
// approval gate into the SPA. The interposition (the SAFETY invariant):
// a deep_detect candidate carrying bot-defense / CAPTCHA / interactive-
// challenge / honeypot markers is surfaced with approval_status="pending"
// and `do_not_auto_submit` stays CLOSED — nothing auto-submits — until
// the operator explicitly approves (or declines). "Fail-open-into-review":
// an uncertain/marked candidate routes to this gate; it is never silently
// auto-submitted nor silently dropped.
//
// Data source (T11 backend, v3.66.264): the candidates are otherwise
// ephemeral (only the operator's *decisions* used to survive a run), so
// the runner now persists the CURRENT pending set into
// learned.deep_detect.pending_approvals and GET
// /api/sites/<sid>/pending_approvals returns the undecided ones. The read
// SELF-CLEARS: an approve/decline removes its row on the next read with no
// fresh analysis needed.
//
// FULL /api/ literals (scanner credit — gui_parity_inventory flips these
// spa_wired; raw `${sid}`, NOT a concatenated base var):
//   GET  /api/sites/${sid}/pending_approvals
//   POST /api/sites/${sid}/auto_submit_decision   body {key, decision}
//   POST /api/sites/${sid}/post_reveal_decision   body {action_url, decision}
// All three ride apiGet/apiPost (X-CSRF-Token on mutations) — never a raw
// fetch().

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  ApprovalDecisionResult,
  PendingApprovalsResponse,
} from "@/lib/api-types";

export type ApprovalDecision = "approve" | "decline";

export function useApproval(sid: string) {
  const qc = useQueryClient();
  const queryKey = ["pending-approvals", sid];

  const query = useQuery<PendingApprovalsResponse>({
    queryKey,
    queryFn: ({ signal }) =>
      apiGet<PendingApprovalsResponse>(
        `/api/sites/${sid}/pending_approvals`,
        signal,
      ),
    refetchOnWindowFocus: false,
    enabled: Boolean(sid),
  });

  const approveAutoSubmit = useMutation<
    ApprovalDecisionResult,
    Error,
    { key: string; decision: ApprovalDecision }
  >({
    mutationFn: ({ key, decision }) =>
      apiPost<ApprovalDecisionResult>(
        `/api/sites/${sid}/auto_submit_decision`,
        { key, decision },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
    },
  });

  const decideReveal = useMutation<
    ApprovalDecisionResult,
    Error,
    { action_url: string; decision: ApprovalDecision }
  >({
    mutationFn: ({ action_url, decision }) =>
      apiPost<ApprovalDecisionResult>(
        `/api/sites/${sid}/post_reveal_decision`,
        { action_url, decision },
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey });
    },
  });

  return { query, approveAutoSubmit, decideReveal };
}
