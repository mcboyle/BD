import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink, Link, useLocation } from "react-router-dom";
import {
  Home as HomeIcon,
  Globe,
  ListOrdered,
  Activity,
  Settings as SettingsIcon,
  Download,
  ChevronsLeft,
  ChevronsRight,
  Search,
  ArrowLeft,
  ExternalLink,
} from "lucide-react";

import { apiGet } from "@/lib/api-client";
import { Collapsible } from "@/components/ui/collapsible";
import type { DashboardV2 } from "@/lib/api-types";
import { statusChipText } from "@/lib/statusChip";
import { cn } from "@/lib/utils";
// Slice 4b — nav groups now live in one shared module consumed by both the
// desktop sidebar (here) and the mobile "More" drawer (BottomTabBar). The 5
// primaries stay always-visible below; this is the overflow long tail.
import { NAV_GROUPS, isPathInGroup } from "@/lib/navGroups";
import {
  useSidebarLayout,
  SIDEBAR_WIDTH_DEFAULT,
  SIDEBAR_COLLAPSED_WIDTH,
} from "@/hooks/useUiLayout";

// v3.64.x — desktop shell. At lg breakpoint (≥1024px), AppShell
// switches from the mobile layout (PageHeader on top, BottomTabBar
// pinned to the floor) to this two-column layout: a fixed sidebar
// nav on the left, a top utility bar with stats + actions, then the
// page content fills the rest.
//
// Mobile and tablet keep using PageHeader + BottomTabBar — nothing
// about this component is mobile-aware, it's only ever rendered when
// the viewport is wide enough that a sidebar makes sense.
//
// Data: this component reads /api/health/v2 itself (its own slow poll)
// so it can show service version + active downloads inline. AppShell
// already has the /api/queue/v2 cache; we don't re-fetch.

interface HealthLite {
  ok: boolean;
  version?: string;
  sites_loaded?: number;
  active_downloads?: number;
  queue_depth?: number;
  uptime_s?: number;
}

interface NavEntry {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  end?: boolean;
  badge?: number;
}

/** V6 — header variant matching PageHeader's. Display = larger
 *  heading + optional status line + optional pills row underneath;
 *  compact = legacy small title + optional subtitle. */
export type DesktopHeaderVariant = "compact" | "display";

export interface DesktopShellProps {
  title: string;
  subtitle?: string;
  trailing?: React.ReactNode;
  /** Total queue size (running + waiting) — feeds the sidebar badge. */
  queueBadge?: number;
  /** V6 — display vs compact header treatment. Default: compact. */
  variant?: DesktopHeaderVariant;
  /** V6 — only rendered in display variant. Inline status line under
   *  the heading (e.g. "Running · 3 queued · 2 of 4 workers"). */
  statusLine?: React.ReactNode;
  /** V6 — only rendered in display variant. Row of pills under the
   *  status line (e.g. status pills with done/running/failed counts). */
  belowStatus?: React.ReactNode;
  /** 269 — drop the max-w content cap so an opted-in page (the capture
   *  workflow) fills the full main column. Default: false (capped). */
  wide?: boolean;
  /** Cut A — back affordance for drill-in routes, shown above the title. */
  backTo?: { to: string; label?: string };
  /** Cut A — breadcrumb trail for drill-in routes, shown above the title. */
  breadcrumb?: React.ReactNode;
  children: React.ReactNode;
}

export function DesktopShell({
  title,
  subtitle,
  trailing,
  queueBadge = 0,
  variant = "compact",
  statusLine,
  belowStatus,
  wide = false,
  backTo,
  breadcrumb,
  children,
}: DesktopShellProps) {
  const { data: health } = useQuery<HealthLite>({
    queryKey: ["health-lite"],
    queryFn: ({ signal }) => apiGet<HealthLite>("/api/health/v2", signal),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });

  // P6-2 — the footer status chip also surfaces worker usage. Reuse the shared
  // ["dashboard-v2"] cache (Home owns it; this slow background poll keeps it
  // warm when Home is unmounted — react-query dedupes the request).
  const { data: dash } = useQuery<DashboardV2>({
    queryKey: ["dashboard-v2"],
    queryFn: ({ signal }) => apiGet<DashboardV2>("/api/dashboard/v2", signal),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });

  const navItems: NavEntry[] = [
    { to: "/",         label: "Home",     icon: HomeIcon,    end: true },
    { to: "/sites",    label: "Sites",    icon: Globe,       badge: health?.sites_loaded },
    { to: "/queue",    label: "Queue",    icon: ListOrdered, badge: queueBadge },
    { to: "/activity", label: "Activity", icon: Activity },
    { to: "/settings", label: "Settings", icon: SettingsIcon },
  ];

  const uptimeStr = formatUptime(health?.uptime_s);
  const chipText = statusChipText({
    activeDownloads: health?.active_downloads ?? 0,
    queueDepth: health?.queue_depth,
    workersActive: dash?.workers_active,
    workersTotal: dash?.workers_total,
    uptime: uptimeStr || undefined,
  });

  // 269 — collapsible + drag-resizable sidebar (per-device, localStorage).
  const { collapsed, setCollapsed, width, setWidth } = useSidebarLayout();
  const [draggingSidebar, setDraggingSidebar] = useState(false);

  // Slice 4a — which grouped route is active, so its group auto-surfaces +
  // is marked. A route is "in" a group if it matches a group item exactly or
  // is a child path of one (/vpn or /vpn/whatever).
  const { pathname } = useLocation();

  // Slice 4a — visible ⌘K affordance. The palette (CommandPalette, mounted in
  // AppShell) opens on a window keydown of meta/ctrl + k; we dispatch exactly
  // that so this button drives the real palette without lifting its state.
  const openPalette = () => {
    window.dispatchEvent(
      new KeyboardEvent("keydown", {
        key: "k",
        metaKey: true,
        ctrlKey: true,
        bubbles: true,
      }),
    );
  };

  // The sidebar sits at the viewport's left edge (x=0), so the pointer's
  // clientX IS the desired width. Pointer-captured so the drag keeps
  // tracking even when the cursor leaves the thin handle. Clamping lives
  // in the hook's setter; double-click resets to the default width.
  const onSidebarDown = (e: React.PointerEvent) => {
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    setDraggingSidebar(true);
  };
  const onSidebarMove = (e: React.PointerEvent) => {
    if (!draggingSidebar) return;
    setWidth(e.clientX);
  };
  const onSidebarUp = (e: React.PointerEvent) => {
    (e.currentTarget as HTMLElement).releasePointerCapture?.(e.pointerId);
    setDraggingSidebar(false);
  };

  return (
    <div className="flex min-h-dvh bg-bg text-ink">
      {/* Sidebar — sticky on scroll; collapsible icon-rail + drag-resizable. */}
      <aside
        className={cn(
          "hairline sticky top-0 z-30 flex h-dvh shrink-0 flex-col border-r bg-surface",
          // smooth width change on collapse toggle; disabled mid-drag so
          // the rail tracks the pointer 1:1 rather than lagging behind it.
          !draggingSidebar && "transition-[width] duration-150",
        )}
        style={{ width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : width }}
        aria-label="Primary navigation"
      >
        <div
          className={cn(
            "flex items-center gap-2.5 px-3 pb-3 pt-4",
            collapsed && "justify-center px-0",
          )}
        >
          <div
            aria-hidden
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md bg-ink text-surface"
          >
            <Download className="h-4 w-4" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold tracking-tight">
                BulkDownloader
              </div>
              <div className="truncate text-[11px] text-ink-3 tabular">
                v{health?.version ?? "?"}
              </div>
            </div>
          )}
        </div>

        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-2 pt-2">
          {/* Slice 4a — visible ⌘K affordance. Expanded: a faux search field
              hinting the shortcut; collapsed: an icon button. Both drive the
              real palette. */}
          <button
            type="button"
            onClick={openPalette}
            aria-label="Open command palette"
            title="Open command palette (⌘K)"
            className={cn(
              "mb-1 flex items-center gap-2.5 rounded-md border border-transparent px-2.5 py-2 text-sm",
              "text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
              collapsed ? "justify-center px-0" : "hairline border bg-surface-2/40",
            )}
          >
            <Search className="h-4 w-4 shrink-0" aria-hidden />
            {!collapsed && (
              <>
                <span className="flex-1 truncate text-left">Search…</span>
                <kbd className="hairline rounded border bg-surface px-1.5 py-px text-[10px] font-medium tabular text-ink-3">
                  ⌘K
                </kbd>
              </>
            )}
          </button>

          {navItems.map(({ to, label, icon: Icon, end, badge }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium",
                  "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                  collapsed && "justify-center px-0",
                  isActive
                    ? "bg-primary-soft text-primary"
                    : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden />
              {!collapsed && (
                <>
                  <span className="flex-1 truncate">{label}</span>
                  {typeof badge === "number" && badge > 0 && (
                    <span
                      className="hairline rounded-full border bg-surface-2 px-1.5 py-px text-[10px] font-medium tabular text-ink-3"
                      aria-label={`${badge}`}
                    >
                      {badge}
                    </span>
                  )}
                </>
              )}
            </NavLink>
          ))}

          {/* v3.66.326 — grouped overflow. Only when expanded; the collapsed
              icon-rail stays minimal (⌘K + expand cover the long tail). */}
          {!collapsed &&
            NAV_GROUPS.map((group) => {
              const groupActive = isPathInGroup(group, pathname);
              return (
                <Collapsible
                  key={group.label}
                  className="mt-2"
                  persistKey={`nav-${group.label}`}
                  forceOpen={groupActive}
                  active={groupActive}
                  headerClassName="rounded-md px-2.5 py-1.5 eyebrow hover:text-ink"
                  title={group.label}
                >
                  <div className="mt-0.5 flex flex-col gap-0.5">
                    {group.items.map(({ to, label, icon: Icon, external }) =>
                      external ? (
                        <a
                          key={to}
                          href={to}
                          target="_blank"
                          rel="noopener noreferrer"
                          className={cn(
                            "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                            "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                            "text-ink-3 hover:bg-surface-2 hover:text-ink",
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
                          className={({ isActive }) =>
                            cn(
                              "flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm",
                              "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary",
                              isActive
                                ? "bg-primary-soft text-primary"
                                : "text-ink-3 hover:bg-surface-2 hover:text-ink",
                            )
                          }
                        >
                          <Icon className="h-4 w-4 shrink-0" aria-hidden />
                          <span className="flex-1 truncate">{label}</span>
                        </NavLink>
                      ),
                    )}
                  </div>
                </Collapsible>
              );
            })}
        </nav>

        <div className="hairline border-t px-3 py-2.5">
          <div
            className={cn(
              "flex items-center gap-2 text-[11px] text-ink-3",
              collapsed && "justify-center",
            )}
          >
            <span
              aria-hidden
              className={cn(
                "h-1.5 w-1.5 shrink-0 rounded-full",
                (health?.active_downloads ?? 0) > 0 ? "bg-green" : "bg-ink-3/40",
              )}
            />
            {!collapsed && (
              <span className="truncate">{chipText}</span>
            )}
          </div>
        </div>

        {/* Drag-resize handle on the rail's right edge (expanded only).
         * Pointer-captured so the drag continues outside the strip;
         * double-click resets to the default width. */}
        {!collapsed && (
          <div
            data-testid="sidebar-resize-handle"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize sidebar"
            onPointerDown={onSidebarDown}
            onPointerMove={onSidebarMove}
            onPointerUp={onSidebarUp}
            onDoubleClick={() => setWidth(SIDEBAR_WIDTH_DEFAULT)}
            className="group absolute right-0 top-0 z-10 h-full w-1.5 cursor-col-resize"
          >
            <div
              className={cn(
                "absolute right-0 top-0 h-full w-px transition-colors group-hover:bg-primary",
                draggingSidebar && "bg-primary",
              )}
            />
          </div>
        )}
      </aside>

      {/* Main column — sticky utility bar, scrollable content under. */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header
          className={cn(
            "hairline sticky top-0 z-20 border-b bg-bg/85 backdrop-blur",
            // V6: compact header keeps the pre-V6 single-row layout;
            // display variant expands vertically to fit the status
            // line + pills row. The extra padding lets the bigger
            // heading breathe.
            variant === "display"
              ? "flex items-start gap-3 px-6 py-4"
              : "flex items-center gap-3 px-6 py-3",
          )}
        >
          <button
            type="button"
            data-testid="sidebar-collapse-toggle"
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
          >
            {collapsed ? (
              <ChevronsRight className="h-4 w-4" aria-hidden />
            ) : (
              <ChevronsLeft className="h-4 w-4" aria-hidden />
            )}
          </button>
          <div className="min-w-0 flex-1">
            {(backTo || breadcrumb) && (
              <div className="mb-1 flex items-center gap-2 text-xs text-ink-3">
                {backTo && (
                  <Link
                    to={backTo.to}
                    aria-label={backTo.label ?? "Back"}
                    className="inline-flex items-center gap-1 rounded text-ink-2 hover:text-ink-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                  >
                    <ArrowLeft className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    {backTo.label ?? "Back"}
                  </Link>
                )}
                {breadcrumb && (
                  <span className="min-w-0 truncate">{breadcrumb}</span>
                )}
              </div>
            )}
            {variant === "display" ? (
              <>
                <h1 className="truncate text-2xl font-bold tracking-tight">
                  {title}
                </h1>
                {statusLine && (
                  <div className="mt-1 truncate text-xs text-ink-3">
                    {statusLine}
                  </div>
                )}
                {belowStatus && (
                  <div className="mt-2 flex flex-wrap items-center gap-1.5">
                    {belowStatus}
                  </div>
                )}
              </>
            ) : (
              <>
                <h1 className="truncate text-base font-semibold tracking-tight">
                  {title}
                </h1>
                {subtitle && (
                  <div className="truncate text-xs text-ink-3">{subtitle}</div>
                )}
              </>
            )}
          </div>
          {trailing && (
            <div
              className={cn(
                "shrink-0",
                // In display variant, the trailing slot sits aligned
                // to the heading (top), not vertically-centered to
                // the whole stack — looks better next to the bigger
                // title.
                variant === "display" && "pt-1",
              )}
            >
              {trailing}
            </div>
          )}
        </header>

        <main
          className={cn(
            "mx-auto w-full flex-1 px-6 pb-12 pt-4",
            wide ? "max-w-none" : "max-w-7xl",
          )}
        >
          {children}
        </main>
      </div>
    </div>
  );
}

// Compact uptime — 4m, 2h, 3d. Matches what the legacy `/` showed.
function formatUptime(sec?: number): string {
  if (!sec || sec < 0) return "";
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)}m`;
  if (sec < 86400) return `${Math.round(sec / 3600)}h`;
  return `${Math.round(sec / 86400)}d`;
}
