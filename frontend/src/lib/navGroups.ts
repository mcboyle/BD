import {
  Camera,
  ClipboardCheck,
  Image as ImageIcon,
  Wrench,
  Layers,
  History,
  Filter,
  Copy,
  Scale,
  Plug,
  Server,
  KeyRound,
  Bell,
  Wand2,
  Workflow,
  Code,
  CalendarClock,
  Send,
  BellRing,
  Gauge,
  Sparkles,
  LayoutDashboard,
  Boxes,
  Terminal,
  Hammer,
  Users as UsersIcon,
} from "lucide-react";

// Slice 4b — the grouped nav (the long tail beyond the 5 primary tabs) is now
// a single source of truth, consumed by BOTH shells: the desktop sidebar
// (DesktopShell collapsible groups) and the mobile "More" drawer
// (BottomTabBar). Previously this lived only in DesktopShell, so mobile had no
// click path to these routes — only ⌘K. Keeping one list prevents drift.
//
// The 5 always-visible primaries (/, /sites, /queue, /activity, /settings)
// stay defined in their respective shells; this is strictly the overflow.

export interface NavGroupItem {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  // v3.66.506 — server-rendered console pages (/framework, /fleet, /cockpit) are
  // NOT react-router routes; a <NavLink> would route into the SPA catch-all
  // instead of loading the server page. external:true makes the shells render an
  // <a href> (new tab) so the click leaves the SPA. See navGroups "Consoles &
  // dashboards" + tools/nav_reachability.check_external_nav.
  external?: boolean;
}

export interface NavGroup {
  label: string;
  items: NavGroupItem[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Operations",
    items: [
      { to: "/capture", label: "Capture", icon: Camera },
      { to: "/needs-review", label: "Needs review", icon: ClipboardCheck },
      { to: "/library", label: "Library", icon: ImageIcon },
      { to: "/maintenance", label: "Maintenance", icon: Wrench },
      { to: "/batch-ops", label: "Batch ops", icon: Layers },
      { to: "/schedules", label: "Schedules", icon: CalendarClock },
      { to: "/bulk-enqueue", label: "Bulk enqueue", icon: Send },
      { to: "/tools", label: "Tools", icon: Hammer },
    ],
  },
  {
    label: "Data & imports",
    items: [
      { to: "/history", label: "History", icon: History },
      { to: "/imports", label: "Imports", icon: Filter },
      { to: "/dedup", label: "Dedup", icon: Copy },
      { to: "/rebalance", label: "Rebalance", icon: Scale },
      { to: "/budget", label: "Byte usage", icon: Gauge },
    ],
  },
  {
    label: "Network & security",
    items: [
      { to: "/vpn", label: "VPN", icon: Plug },
      { to: "/integrations", label: "Integrations", icon: Server },
      { to: "/secrets", label: "Secrets", icon: KeyRound },
      { to: "/users", label: "Users", icon: UsersIcon },
      { to: "/notifications", label: "Notifications", icon: Bell },
      { to: "/alerts", label: "Alert rules", icon: BellRing },
    ],
  },
  {
    label: "Automation",
    items: [
      { to: "/templates", label: "Templates", icon: Wand2 },
      { to: "/pools-macros", label: "Pools & macros", icon: Workflow },
      { to: "/ai-teach", label: "AI repair", icon: Code },
      { to: "/ai-assist", label: "AI Assist", icon: Sparkles },
      { to: "/plugins/metrics", label: "Plugin metrics", icon: Plug },
    ],
  },
  {
    // v3.66.506 — server-rendered consoles (NOT React routes). external:true so
    // the shells render <a href> (new tab) rather than a react-router NavLink,
    // which would otherwise route into the SPA catch-all. /cockpit is a single
    // bridge into the dev console; its 7 report sub-pages stay reached from the
    // console's own API-driven nav (don't SPA-link each).
    label: "Consoles & dashboards",
    items: [
      { to: "/framework", label: "Framework dashboard", icon: LayoutDashboard, external: true },
      { to: "/fleet", label: "Fleet view", icon: Boxes, external: true },
      { to: "/cockpit", label: "Cockpit console", icon: Terminal, external: true },
    ],
  },
];

/** Is a path inside a group (exact or child route)? */
export function isPathInGroup(group: NavGroup, pathname: string): boolean {
  return group.items.some(
    (i) => pathname === i.to || pathname.startsWith(i.to + "/"),
  );
}
