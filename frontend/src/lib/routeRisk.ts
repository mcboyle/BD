// Cut 1 substrate — routeRisk: the single source of truth mapping an SPA route
// to its risk profile. Banner tiering, Danger/Integrity-zone placement, and
// verification expectations all read from here so there's no per-page drift.
//
// Presentational/advisory only: this NEVER changes gating, confirmation, or any
// guard — it only describes how strongly the UI should signal risk on a route.

export type RouteSeverity = "low" | "medium" | "high";
export type RouteBannerShape = "full" | "chip";

export interface RouteRisk {
  /** Overall risk weight of the route. */
  severity: RouteSeverity;
  /** Which gated-write banner tier the route should show. */
  bannerShape: RouteBannerShape;
  /** Whether the route has a page-level destructive block to group. */
  needsDangerZone: boolean;
  /** Whether the route has a capture/redaction integrity block to group. */
  needsIntegrityZone: boolean;
}

const FULL_DANGER: RouteRisk = {
  severity: "high",
  bannerShape: "full",
  needsDangerZone: true,
  needsIntegrityZone: false,
};
const FULL_PLAIN: RouteRisk = {
  severity: "medium",
  bannerShape: "full",
  needsDangerZone: false,
  needsIntegrityZone: false,
};
const CHIP: RouteRisk = {
  severity: "low",
  bannerShape: "chip",
  needsDangerZone: false,
  needsIntegrityZone: false,
};

// Conservative default for any unmapped route: a low-severity chip with no
// zones. Never blocks; just under-signals rather than over-signals.
const DEFAULT_RISK: RouteRisk = CHIP;

// Keyed by route path. Longest-prefix match wins (so /sites/:id/payload-actions
// can be more specific than /sites). Dynamic segments are matched structurally
// by their literal prefix.
const RISK_MAP: Record<string, RouteRisk> = {
  // Write-heavy / destructive surfaces -> full banner + danger zone.
  "/secrets": FULL_DANGER,
  "/backup": FULL_DANGER,
  "/maintenance": FULL_DANGER,
  "/rebalance": FULL_DANGER,
  "/batch-ops": FULL_DANGER,
  "/pools-macros": FULL_DANGER,
  "/dedup": FULL_DANGER,
  "/vpn": FULL_DANGER,
  "/ai-teach": FULL_DANGER,
  "/imports": FULL_DANGER,
  "/sites/payload-actions": FULL_DANGER,

  // Settings: write surface AND the capture/redaction integrity block.
  "/settings": {
    severity: "high",
    bannerShape: "full",
    needsDangerZone: true,
    needsIntegrityZone: true,
  },

  // Config-bearing but not destructive -> full banner, no zone.
  "/templates": FULL_PLAIN,
  "/integrations": CHIP,
  "/import-views": FULL_PLAIN,
  "/more-actions": FULL_PLAIN,

  // Read-mostly -> chip.
  "/dashboard": CHIP,
  "/history": CHIP,
  "/activity": CHIP,
  "/notifications": CHIP,
  "/cluster": CHIP,
  "/needs-review": CHIP,
  "/queue": CHIP,
  "/sites": CHIP,
  "/library": CHIP,
};

export function routeRisk(path: string): RouteRisk {
  if (!path) return DEFAULT_RISK;
  // Exact match first.
  if (RISK_MAP[path]) return RISK_MAP[path];
  // Longest-prefix match (handles /sites/:id/... etc).
  let best: RouteRisk | null = null;
  let bestLen = -1;
  for (const key of Object.keys(RISK_MAP)) {
    if ((path === key || path.startsWith(key + "/")) && key.length > bestLen) {
      best = RISK_MAP[key];
      bestLen = key.length;
    }
  }
  return best ?? DEFAULT_RISK;
}
