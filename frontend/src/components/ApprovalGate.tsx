// ApprovalGate — T11 (v3.66.264). The per-site approval gate, ported
// from legacy static/approval_ui.js. Renders ONLY when the site has >=1
// pending auto-submit / post-reveal candidate (deep_detect surfaced a
// bot-defense / CAPTCHA / challenge / honeypot marker). While pending,
// nothing auto-submits — the operator must explicitly approve or decline
// for THIS site ("stays gated until you approve it for this site").
//
// Confirm tier: approve/decline is a reversible, single-tap B-tier
// confirm (records a re-promptable decision) per the v3.66.209 tier
// model — a sonner-toast confirm with an explicit action button, the
// same UX shape as the SiteDetail delete affordance. NOT a typed token,
// and NOT a zero-confirm one-click (it's a safety decision).

import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useApproval, type ApprovalDecision } from "@/hooks/useApproval";
import type { PendingApproval } from "@/lib/api-types";

function surfaceLabel(p: PendingApproval): string {
  return p.surface === "post_reveal"
    ? "Two-step POST reveal"
    : "Login form / page blocker";
}

export function ApprovalGate({ siteId }: { siteId: string }) {
  const { query, approveAutoSubmit, decideReveal } = useApproval(siteId);
  const pending = query.data?.pending ?? [];
  if (query.isLoading || query.isError || pending.length === 0) {
    // Read-only gate: render nothing when there's nothing to action.
    // (An errored/loading read simply shows no gate — it never blocks
    //  the page, matching fail-open-into-review on the read side.)
    return null;
  }

  const busy = approveAutoSubmit.isPending || decideReveal.isPending;

  function decide(p: PendingApproval, decision: ApprovalDecision) {
    const verb = decision === "approve" ? "Approve" : "Decline";
    // Single-tap confirm (reversible; re-promptable) — same shape as the
    // delete affordance. The actual decision only fires from the toast's
    // action button, so a stray click never silently records a safety
    // decision.
    toast(`${verb} this surface for ${siteId}?`, {
      description:
        decision === "approve"
          ? "Auto-submit will be allowed for this surface on this site."
          : "This surface stays gated; it will not auto-submit.",
      action: {
        label: verb,
        onClick: () => {
          if (p.surface === "post_reveal") {
            decideReveal.mutate(
              { action_url: p.key, decision },
              {
                onSuccess: () => toast.success(`${verb}d — ${p.kind}`),
                onError: (e) => toast.error(`Could not record: ${String(e)}`),
              },
            );
          } else {
            approveAutoSubmit.mutate(
              { key: p.key, decision },
              {
                onSuccess: () => toast.success(`${verb}d — ${p.kind}`),
                onError: (e) => toast.error(`Could not record: ${String(e)}`),
              },
            );
          }
        },
      },
    });
  }

  return (
    <Card className="border-amber/40 bg-amber-soft/40 p-3">
      <div className="mb-2 flex items-center gap-2">
        <ShieldAlert className="h-4 w-4 text-amber" aria-hidden />
        <span className="text-sm font-medium text-ink">
          Approval needed ({pending.length})
        </span>
      </div>
      <p className="mb-3 text-xs text-ink-3">
        A challenge-marked candidate was detected. It stays gated — nothing
        auto-submits — until you approve it for this site.
      </p>
      <ul className="space-y-2">
        {pending.map((p) => (
          <li
            key={`${p.surface}:${p.key}`}
            className="flex items-center justify-between gap-3 rounded-md border bg-surface px-3 py-2 hairline"
          >
            <div className="min-w-0">
              <div className="truncate text-sm text-ink">
                {surfaceLabel(p)} · {p.kind}
              </div>
              <div className="truncate text-xs text-ink-3" title={p.key}>
                {p.why}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => decide(p, "approve")}
                aria-label={`Approve ${surfaceLabel(p)} for this site`}
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => decide(p, "decline")}
                aria-label={`Decline ${surfaceLabel(p)} for this site`}
                className="text-red hover:bg-red-soft hover:text-red"
              >
                Decline
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
