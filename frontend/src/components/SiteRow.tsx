import { Check, ChevronRight, KeyRound, Shield } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { SiteAvatar } from "@/components/SiteAvatar";
import { ReadinessBadge } from "@/components/ReadinessBadge";
import type { SiteEntryV2 } from "@/lib/api-types";
import { cn } from "@/lib/utils";

// One row in the Sites tab list. Mockup signature:
//   - colored avatar
//   - bold site name
//   - status line below it: state + chips for auth/captcha issues
//   - chevron on the right (taps open the edit drawer)
//
// v3.64.x D3 follow-up: optional bulk-select mode. When the parent
// passes `onToggleSelect`, the row enters selection mode:
//   - A checkmark slot replaces the avatar's leading position (avatar
//     stays, the check overlays it when selected).
//   - The whole row toggles selection on tap (no chevron action).
//   - The chevron is suppressed (the row is no longer a "drill into
//     detail" affordance while a selection is in progress).
// When `onToggleSelect` is absent the row behaves exactly as before.

export interface SiteRowProps {
  site: SiteEntryV2;
  onClick?: (site: SiteEntryV2) => void;
  selected?: boolean;
  onToggleSelect?: (site: SiteEntryV2) => void;
}

export function SiteRow({
  site,
  onClick,
  selected = false,
  onToggleSelect,
}: SiteRowProps) {
  const isActive = site.state === "running";
  const hasIssue = site.captcha_pending || site.auth_state === "expired";
  const selectionMode = !!onToggleSelect;

  return (
    <button
      type="button"
      className={cn(
        "hairline w-full rounded-md p-3 text-left",
        "flex items-center gap-3 transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
        // V3 — mockup signature: rows with auth/captcha issues get
        // an amber-soft surface tint so the issue is visible at a
        // glance from the list (matches sites.png screen). Selected
        // rows take priority and switch to primary-soft + ring (so
        // the bulk-select state always wins). Active sites keep the
        // ordinary surface; only the green status dot signals active
        // — the row tint is reserved for things that need user action.
        selected
          ? "bg-primary-soft ring-2 ring-primary"
          : hasIssue
            ? "bg-amber-soft hover:bg-amber-soft/80 border-amber/20"
            : "bg-surface hover:bg-surface-2",
      )}
      onClick={() => {
        if (selectionMode) onToggleSelect?.(site);
        else onClick?.(site);
      }}
      aria-label={
        selectionMode
          ? `${selected ? "Deselect" : "Select"} ${site.name}`
          : `Open ${site.name}`
      }
      aria-pressed={selectionMode ? selected : undefined}
    >
      <div className="relative shrink-0">
        <SiteAvatar name={site.name} color={site.avatar_color} />
        {selectionMode && selected && (
          <span
            aria-hidden
            className="absolute -right-1 -bottom-1 grid h-5 w-5 place-items-center rounded-full bg-primary text-white ring-2 ring-surface"
          >
            <Check className="h-3 w-3" strokeWidth={3} />
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-ink">
          {site.name}
        </div>
        <div className="mt-0.5 flex items-center gap-1.5 text-xs text-ink-3">
          <span
            aria-hidden
            className={cn(
              "h-1.5 w-1.5 rounded-full shrink-0",
              isActive ? "bg-green" : "bg-ink-3/40",
            )}
          />
          <span className="truncate">
            {isActive ? "Active" : "Idle"}
            {site.downloaded_total > 0 && (
              <>
                {" · "}
                <span className="tabular">{site.downloaded_total}</span>{" "}
                downloaded
              </>
            )}
            {site.last_event_age && !hasIssue && (
              <>
                {" · "}
                {site.last_event_age}
              </>
            )}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-1.5 shrink-0">
        {/* Cut 4: composite readiness at a glance (browse mode only). */}
        {!selectionMode && <ReadinessBadge siteId={site.site_id} />}
        {site.captcha_pending && (
          <Badge variant="warning" className="gap-1">
            <Shield className="h-3 w-3" aria-hidden />
            Captcha
          </Badge>
        )}
        {site.auth_state === "expired" && !site.captcha_pending && (
          <Badge variant="warning" className="gap-1">
            <KeyRound className="h-3 w-3" aria-hidden />
            Login expired
          </Badge>
        )}
        {!selectionMode && (
          <ChevronRight className="h-4 w-4 text-ink-3" aria-hidden />
        )}
      </div>
    </button>
  );
}
