import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import {
  Home as HomeIcon,
  Globe,
  ListOrdered,
  Activity,
  Settings,
  MoreHorizontal,
  X,
  ExternalLink,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { NAV_GROUPS, isPathInGroup } from "@/lib/navGroups";

// V1 — Bottom tab bar restyled to the floating-pill mockup signature.
// (history retained in git; see prior versions for the V1 mockup notes.)
//
// Frozen contract (test_d3_u3_bottom_tab_bar_has_all_5_tabs): the
// 5 `to:` paths must remain `/`, `/sites`, `/queue`, `/activity`,
// `/settings`. V1 keeps all five.
//
// Slice 4b — a 6th cell "More" (a button, NOT a route, so the frozen
// contract is untouched) opens a bottom-sheet drawer exposing the grouped
// nav (the long tail that was ⌘K-only on mobile). The drawer is rendered as
// a SIBLING of the floating bar — never a descendant of the backdrop-blur
// nav — because backdrop-filter creates a containing block that would trap a
// position:fixed/absolute drawer to the bar instead of the viewport
// (active_footgun[2]).

interface Tab {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
}

const TABS: Tab[] = [
  { to: "/",         label: "Home",     icon: HomeIcon,    end: true },
  { to: "/sites",    label: "Sites",    icon: Globe },
  { to: "/queue",    label: "Queue",    icon: ListOrdered },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/settings", label: "Settings", icon: Settings },
];

export type QueueBadgeMode = "count" | "percent";

export interface BottomTabBarProps {
  queueBadge?: number;
  queueBadgeMode?: QueueBadgeMode;
  queueRunningPct?: number;
  queueHasRunning?: boolean;
}

export function BottomTabBar({
  queueBadge = 0,
  queueBadgeMode = "count",
  queueRunningPct = 0,
  queueHasRunning = false,
}: BottomTabBarProps) {
  const showPercent =
    queueBadgeMode === "percent" && queueHasRunning && queueBadge > 0;

  const [moreOpen, setMoreOpen] = useState(false);
  const { pathname } = useLocation();
  // "More" is active when the drawer is open or the current route lives in a
  // group (so the operator can see the long-tail page is reachable there).
  const moreActive =
    moreOpen || NAV_GROUPS.some((g) => isPathInGroup(g, pathname));

  // Escape closes the drawer (mirrors the ⌘K palette + desktop behavior).
  useEffect(() => {
    if (!moreOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMoreOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [moreOpen]);

  return (
    <>
      {/* Drawer — sibling of the bar (NOT inside the blurred nav, footgun[2]).
          The overlay is fixed inset-0 with no filter/transform, so the sheet's
          positioning resolves to the viewport. */}
      {moreOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="More navigation">
          <button
            type="button"
            aria-label="Close menu"
            onClick={() => setMoreOpen(false)}
            className="absolute inset-0 bg-ink/40"
          />
          <div
            className={cn(
              "hairline absolute inset-x-0 bottom-0 max-h-[70vh] overflow-y-auto rounded-t-2xl border-t bg-surface",
              "px-4 pt-3 pb-[max(env(safe-area-inset-bottom),1rem)]",
              "shadow-[0_-8px_24px_-8px_rgba(10,10,15,0.18)]",
            )}
          >
            <div className="mb-2 flex items-center justify-between">
              <span className="text-sm font-semibold tracking-tight">More</span>
              <button
                type="button"
                onClick={() => setMoreOpen(false)}
                aria-label="Close menu"
                className="grid h-8 w-8 place-items-center rounded-md text-ink-3 hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
              >
                <X className="h-4 w-4" aria-hidden />
              </button>
            </div>
            {NAV_GROUPS.map((group) => (
              <div key={group.label} className="mb-3 last:mb-0">
                <div className="px-1 pb-1 eyebrow">
                  {group.label}
                </div>
                <div className="grid grid-cols-2 gap-1">
                  {group.items.map(({ to, label, icon: Icon, external }) =>
                    external ? (
                      <a
                        key={to}
                        href={to}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={() => setMoreOpen(false)}
                        className={cn(
                          "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm",
                          "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                          "text-ink-2 hover:bg-surface-2 hover:text-ink",
                        )}
                      >
                        <Icon className="h-4 w-4 shrink-0" aria-hidden />
                        <span className="flex-1 truncate">{label}</span>
                        <ExternalLink className="h-3 w-3 shrink-0 opacity-60" aria-hidden />
                      </a>
                    ) : (
                      <NavLink
                        key={to}
                        to={to}
                        onClick={() => setMoreOpen(false)}
                        className={({ isActive }) =>
                          cn(
                            "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm",
                            "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                            isActive
                              ? "bg-primary-soft text-primary"
                              : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                          )
                        }
                      >
                        <Icon className="h-4 w-4 shrink-0" aria-hidden />
                        <span className="flex-1 truncate">{label}</span>
                      </NavLink>
                    ),
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Outer wrapper carries the safe-area padding so the floating bar lifts
          above iOS gesture area on notched devices. */}
      <div
        className={cn(
          "pointer-events-none fixed inset-x-0 bottom-0 z-40",
          "pb-[max(env(safe-area-inset-bottom),0.5rem)]",
          "px-3",
        )}
      >
        <nav
          className={cn(
            "pointer-events-auto mx-auto max-w-2xl",
            "hairline rounded-full border bg-surface/92 backdrop-blur-md",
            "shadow-[0_4px_16px_-4px_rgba(10,10,15,0.08)]",
            "px-1.5 py-1",
          )}
          aria-label="Primary"
        >
          <ul className="flex items-stretch justify-around">
            {TABS.map(({ to, label, icon: Icon, end }) => (
              <li key={to} className="flex-1">
                <NavLink
                  to={to}
                  end={end}
                  className={({ isActive }) =>
                    cn(
                      "relative flex flex-col items-center gap-0.5 rounded-full px-2 py-1.5",
                      "text-[11px] font-medium tracking-tight transition-colors",
                      isActive ? "text-primary" : "text-ink-3 hover:text-ink-2",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <span
                        className={cn(
                          "relative grid h-7 w-7 place-items-center rounded-full",
                          isActive && "bg-primary-soft",
                        )}
                      >
                        <Icon className="h-5 w-5" />
                        {to === "/queue" && showPercent && (
                          <span
                            aria-label={`${queueRunningPct}% downloading`}
                            className={cn(
                              "absolute -right-2 -top-1.5 rounded-full bg-primary px-1",
                              "min-w-[18px] text-center text-[9px] font-bold leading-[14px] text-white",
                              "tabular-nums shadow-sm",
                            )}
                          >
                            {queueRunningPct}%
                          </span>
                        )}
                        {to === "/queue" && !showPercent && queueBadge > 0 && (
                          <span
                            aria-label={`${queueBadge} items in queue`}
                            className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-red"
                          />
                        )}
                      </span>
                      {label}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
            {/* Slice 4b — "More" overflow cell. A button (not a route): the
                frozen 5-tab contract counts `to:` literals, which are untouched. */}
            <li className="flex-1">
              <button
                type="button"
                onClick={() => setMoreOpen((v) => !v)}
                aria-label="More"
                aria-expanded={moreOpen}
                className={cn(
                  "relative flex w-full flex-col items-center gap-0.5 rounded-full px-2 py-1.5",
                  "text-[11px] font-medium tracking-tight transition-colors",
                  moreActive ? "text-primary" : "text-ink-3 hover:text-ink-2",
                )}
              >
                <span
                  className={cn(
                    "relative grid h-7 w-7 place-items-center rounded-full",
                    moreActive && "bg-primary-soft",
                  )}
                >
                  <MoreHorizontal className="h-5 w-5" />
                </span>
                More
              </button>
            </li>
          </ul>
        </nav>
      </div>
    </>
  );
}
