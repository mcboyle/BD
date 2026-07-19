import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowRight } from "lucide-react";
import { toast } from "sonner";

import { apiPost } from "@/lib/api-client";
import type { AttentionEntry, ResolveResponse } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// V2 — Attention banner restyled as the dashboard hero card.
//
// Mockup signature (home.png, all_tabs.png first phone):
//   • amber-soft surface with hairline amber-tinted border
//   • compact "NEEDS ATTENTION · N" eyebrow at the top with the
//     amber alert-triangle icon
//   • each entry on its own row: left-aligned bold site name,
//     right-aligned label + age (cookie expired · 2h ago)
//   • full-width DARK INK pill button at the bottom: "Resolve →"
//     (was a small inline button pre-V2)
//
// The Resolve button POSTs to /api/dashboard/v2/resolve — this is the
// FROZEN contract (test_d3_u3_attention_banner_uses_resolve_endpoint).
// V2 keeps the POST identical to pre-V2; only the visual treatment
// changes. On success: toast the action detail, refetch dashboard so
// the issue disappears (or drops to a lower-priority kind). On failure:
// toast the error, keep the row visible.
//
// Renders null when attention.length === 0 (unchanged from pre-V2).
//
// A11y (FROZEN by test_d3_u8_polish.py): role="alert" + aria-live.
// V2 keeps both attributes in place.

export interface AttentionBannerProps {
  attention: AttentionEntry[];
}

export function AttentionBanner({ attention }: AttentionBannerProps) {
  const qc = useQueryClient();
  const resolveMut = useMutation<
    ResolveResponse,
    Error,
    { site_id: string; kind: string; name: string }
  >({
    mutationFn: (vars) =>
      apiPost<ResolveResponse>("/api/dashboard/v2/resolve", {
        site_id: vars.site_id,
        kind: vars.kind,
      }),
    onSuccess: (data, vars) => {
      if (data.ok) {
        toast.success(data.detail || `Resolve triggered for ${vars.name}`);
      } else {
        toast.error(data.error || "Resolve failed");
      }
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
    },
    onError: (err, vars) => {
      toast.error(`Resolve ${vars.name}: ${err.message}`);
    },
  });

  if (attention.length === 0) return null;

  const next = attention[0];

  return (
    <section
      className={cn(
        "rounded-lg border bg-amber-soft p-4",
        // 0.5px hairline border, amber-tinted. Matches the mockup's
        // soft-orange surround that distinguishes the hero from
        // ordinary cards.
        "border-amber/30",
      )}
      role="alert"
      aria-live="polite"
      aria-label="Sites needing attention"
    >
      {/* Eyebrow row: icon + label + count badge */}
      <div className="mb-3 flex items-center gap-2">
        <AlertTriangle className="h-4 w-4 text-amber-dim" aria-hidden />
        <h2 className="eyebrow eyebrow-warn">
          Needs attention
        </h2>
        <span
          className={cn(
            "ml-auto rounded-full bg-amber/15 px-2 py-0.5",
            "text-[10px] font-bold tabular-nums text-amber-dim",
          )}
          aria-label={`${attention.length} issues`}
        >
          {attention.length}
        </span>
      </div>

      {/* Entry rows: name left, label + age right */}
      <ul className="mb-3 space-y-2">
        {attention.map((entry) => (
          <li
            key={`${entry.site_id}-${entry.kind}`}
            className="flex items-baseline justify-between gap-3 text-sm"
          >
            <span className="font-semibold text-ink truncate">
              {entry.name}
            </span>
            <span className="shrink-0 text-right text-ink-2">
              <span>{entry.label}</span>
              {entry.age_human && (
                <span className="text-ink-3"> · {entry.age_human}</span>
              )}
            </span>
          </li>
        ))}
      </ul>

      {/* Full-width Resolve button — dark ink bg with arrow on the
       * right. POSTs to /api/dashboard/v2/resolve with the first
       * (highest-precedence) entry's site_id + kind. Backend list is
       * already precedence-sorted per app.py:9147.
       *
       * Note: rendered as a native button (not @/components/ui/button)
       * so we can apply mockup-specific full-width styling without
       * fighting Button's variant defaults. */}
      <button
        type="button"
        disabled={resolveMut.isPending || !next}
        onClick={() => {
          if (next)
            resolveMut.mutate({
              site_id: next.site_id,
              kind: next.kind,
              name: next.name,
            });
        }}
        aria-label={
          next ? `Resolve ${next.name}: ${next.label}` : "Resolve"
        }
        className={cn(
          "w-full rounded-md bg-ink py-3 px-4",
          "text-sm font-semibold text-surface",
          "flex items-center justify-center gap-2",
          "transition-opacity hover:opacity-90 active:opacity-80",
          "disabled:cursor-not-allowed disabled:opacity-50",
        )}
      >
        {resolveMut.isPending ? "Resolving…" : "Resolve"}
        <ArrowRight className="h-4 w-4" aria-hidden />
      </button>
    </section>
  );
}
