import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { apiGet } from "@/lib/api-client";
import type { QueueV2Full } from "@/lib/api-types";
import { useCompletionSound } from "@/hooks/useCompletionSound";
import { useKeyboardNav } from "@/hooks/useKeyboardNav";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import {
  aggregateRunningPct,
  useQueueBadgeMode,
} from "@/hooks/useQueueBadgeMode";
import { BottomTabBar } from "./BottomTabBar";
import { CommandPalette } from "./CommandPalette";
import { DesktopShell } from "./DesktopShell";
import { PageHeader } from "./PageHeader";
import { ShortcutsSheet } from "./ShortcutsSheet";
import type { PageHeaderVariant } from "./PageHeader";

// AppShell — sticky header up top, bottom tab bar at the foot,
// content in the middle. Every /m2 route renders inside this.
//
// We poll /api/queue/v2 here (slowly) so the BottomTabBar can show
// the queue-pending dot regardless of which tab is active. This is
// the one piece of cross-tab state — everything else is page-local.
//
// U6 addition: CommandPalette is mounted here (always rendered, opens
// only when ⌘K is pressed). Mounting at AppShell level — rather than
// per-route — means the palette is reachable from any tab without
// each route needing to import + mount it.
//
// U8 addition: useCompletionSound also lives here so the ping fires
// no matter which tab is active when the queue empties.
//
// v3.64.x D3 follow-up (U3): the queue badge gets a per-device
// preference for count-vs-percent. We type the badge query as
// QueueV2Full so the percent-mode aggregate sees the running[]
// progress values (the old QueueV2 type had waiting/running typed
// looser; QueueV2Full is the proper U5 shape).
//
// v3.64.x desktop-shell follow-up: at lg breakpoint (≥1024px) we
// render via DesktopShell instead — sidebar nav + top utility bar +
// wider content. Mobile and tablet keep the existing chrome.
// CommandPalette is mounted in both branches so ⌘K works on either.
//
// V1: PageHeader now has display/compact variants and accepts a
// `statusLine` slot. AppShell exposes both via new props
// (`headerVariant`, `headerStatusLine`, `headerBelowStatus`). Existing
// callers keep working — compact is the default.
// V1: BottomTabBar is now a floating pill with gutter, so the main
// pad-bottom bumps from pb-24 to pb-28 to clear it.

export interface AppShellProps {
  title: string;
  subtitle?: string;
  trailing?: React.ReactNode;
  children: React.ReactNode;
  /** V1: layout variant for the header — compact (default) or display. */
  headerVariant?: PageHeaderVariant;
  /** V1: display-mode status line ("Idle · 0 queued · 0/4 workers"). */
  headerStatusLine?: React.ReactNode;
  /** V1: optional pills row below the status line in display mode. */
  headerBelowStatus?: React.ReactNode;
  /** 269: drop the content max-w cap (desktop) so a full-bleed page like
   *  the capture workflow fills the main column. Default: false. */
  wide?: boolean;
  /** Cut A — back affordance for drill-in routes (threaded to the header). */
  backTo?: { to: string; label?: string };
  /** Cut A — breadcrumb trail for drill-in routes (threaded to the header). */
  breadcrumb?: React.ReactNode;
}

export function AppShell({
  title,
  subtitle,
  trailing,
  children,
  headerVariant,
  headerStatusLine,
  headerBelowStatus,
  wide = false,
  backTo,
  breadcrumb,
}: AppShellProps) {
  // Cross-tab queue badge — slow poll, fail open (no dot on error).
  const { data: queue } = useQuery<QueueV2Full>({
    queryKey: ["queue-v2-badge"],
    queryFn: ({ signal }) => apiGet<QueueV2Full>("/api/queue/v2", signal),
    refetchInterval: 15_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });

  const queueBadge =
    (queue?.running?.length ?? 0) + (queue?.waiting?.length ?? 0);

  // U3: badge mode + aggregate %. Pure derivation from the same cache
  // entry — no extra network. The hook subscribes to localStorage
  // changes so toggling on Settings is reflected instantly on every
  // mounted AppShell.
  const { mode: queueBadgeMode } = useQueueBadgeMode();
  const queueHasRunning = (queue?.running?.length ?? 0) > 0;
  const queueRunningPct = queueHasRunning
    ? aggregateRunningPct(queue?.running?.map((r) => r.progress) ?? [])
    : 0;

  // U8: completion ping. Internally re-uses the queue-v2-badge query
  // (same key) so this doesn't open a second pipe to the backend.
  useCompletionSound();

  // Cut 2: global keyboard nav layer (g-jumps, `/` filter, `?` help). Mounted
  // once here so it's live on every route. The `?` sheet is owned here; the
  // layer goes inert while the sheet is open so it doesn't re-fire under it.
  // `/` and j/k are page-local concerns the layer only triggers if a route
  // opts in — at the shell level we wire navigation + the help sheet.
  const navigate = useNavigate();
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  useKeyboardNav({
    navigate,
    onShowHelp: () => setShortcutsOpen(true),
    dialogOpen: shortcutsOpen,
  });

  // Viewport branch — Tailwind's `lg:` breakpoint is 1024px. The
  // hook returns false during SSR and the very first paint; this
  // means on first mount the mobile shell renders for ~1 tick on
  // desktop, then swaps. The flash is brief and only happens on
  // page load — preferable to a useLayoutEffect that would block
  // first paint.
  const isDesktop = useMediaQuery("(min-width: 1024px)");

  if (isDesktop) {
    return (
      <>
        <DesktopShell
          title={title}
          subtitle={subtitle}
          trailing={trailing}
          queueBadge={queueBadge}
          // V6: forward V1's header variant props to DesktopShell so
          // display-variant Home renders consistently on desktop —
          // big heading, status line, status pills row underneath.
          // Pre-V6 these were dropped on desktop, leaving the compact
          // title-only header even though Home explicitly requested
          // the display variant.
          variant={headerVariant}
          statusLine={headerStatusLine}
          belowStatus={headerBelowStatus}
          wide={wide}
          backTo={backTo}
          breadcrumb={breadcrumb}
        >
          {children}
        </DesktopShell>
        <CommandPalette />
        <ShortcutsSheet open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      </>
    );
  }

  return (
    <div className="min-h-dvh bg-bg text-ink">
      <PageHeader
        title={title}
        subtitle={subtitle}
        statusLine={headerStatusLine}
        belowStatus={headerBelowStatus}
        trailing={trailing}
        variant={headerVariant}
        backTo={backTo}
        breadcrumb={breadcrumb}
      />
      {/* V1: clear the floating tab bar (gutter + ~64px chrome). Slice 4b:
       * fold the safe-area inset into the scroll content itself (not just the
       * bar wrapper) and add scroll-padding so a focused field / anchored
       * scroll never tucks under the floating bar on notched devices. */}
      <main className="mx-auto max-w-2xl px-4 pt-3 pb-[calc(7rem+env(safe-area-inset-bottom))] scroll-pb-[calc(7rem+env(safe-area-inset-bottom))]">
        {children}
      </main>
      <BottomTabBar
        queueBadge={queueBadge}
        queueBadgeMode={queueBadgeMode}
        queueRunningPct={queueRunningPct}
        queueHasRunning={queueHasRunning}
      />
      <CommandPalette />
      <ShortcutsSheet open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
    </div>
  );
}
