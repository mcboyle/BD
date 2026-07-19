import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { apiGet } from "@/lib/api-client";
import { cn } from "@/lib/utils";

// V1 — Top header restyled to mockup signature.
//
// Two display modes:
//   - "compact" (default): brand square + small inline title + version
//     subtitle. The pre-V1 shape, used by Sites/Queue/Activity/
//     Settings/SiteDetail headers where the screen-level <h1> is
//     rendered inside the page content.
//   - "display": large display-sized title taking its own line, with
//     an optional status line below. Used by Home (BulkDL + "Idle ·
//     0 queued · 0 of 4 workers") so the screen title is the page
//     anchor, not the tiny chrome label.
//
// In compact mode, the brand square sits inline at left and the
// title is a single line. In display mode, the brand square moves
// next to the large title and a `statusLine` slot below carries the
// "Idle · 0 queued · 0/4 workers" prose seen in the mockup.
//
// Polling is unchanged from pre-V1: /api/health/v2 at 30s. The
// header is ambient context; the per-page widgets carry their own
// adaptive polling for live signals.

interface HealthLite {
  ok: boolean;
  version?: string;
  sites_loaded?: number;
  active_downloads?: number;
}

export type PageHeaderVariant = "compact" | "display";

export interface PageHeaderProps {
  /** Page title — shown beside the brand square. */
  title: string;
  /** Compact-only secondary line. Shown small, below the title. */
  subtitle?: string;
  /** Display-only prose line — "Idle · 0 queued · 0/4 workers" etc. */
  statusLine?: React.ReactNode;
  /** Optional pills row rendered below the status line in display mode. */
  belowStatus?: React.ReactNode;
  /** Trailing slot — right-aligned next to the title row. */
  trailing?: React.ReactNode;
  /** Layout variant. Defaults to "compact". */
  variant?: PageHeaderVariant;
  /** Cut A — a back affordance for drill-in routes (Site drill-ins, Logs diff).
   *  Renders a link above the title in both layouts. Navigational only. */
  backTo?: { to: string; label?: string };
  /** Cut A — a breadcrumb trail shown above the title (e.g. "Sites › Acme ›
   *  Payload actions"). Presentational; pair with backTo on drill-ins. */
  breadcrumb?: React.ReactNode;
}

export function PageHeader({
  title,
  subtitle,
  statusLine,
  belowStatus,
  trailing,
  variant = "compact",
  backTo,
  breadcrumb,
}: PageHeaderProps) {
  const { data } = useQuery<HealthLite>({
    queryKey: ["health-lite"],
    queryFn: ({ signal }) => apiGet<HealthLite>("/api/health/v2", signal),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });

  return (
    <header
      className={cn(
        "sticky top-0 z-30",
        "hairline border-b bg-bg/85 backdrop-blur",
        "px-4 py-3",
        "pt-[max(env(safe-area-inset-top),0.75rem)]",
      )}
    >
      <div className="mx-auto max-w-2xl">
        <BackBreadcrumb backTo={backTo} breadcrumb={breadcrumb} />
        {variant === "display" ? (
          <DisplayLayout
            title={title}
            statusLine={statusLine}
            belowStatus={belowStatus}
            trailing={trailing}
            data={data}
          />
        ) : (
          <CompactLayout
            title={title}
            subtitle={subtitle}
            trailing={trailing}
            data={data}
          />
        )}
      </div>
    </header>
  );
}

function CompactLayout({
  title,
  subtitle,
  trailing,
  data,
}: {
  title: string;
  subtitle?: string;
  trailing?: React.ReactNode;
  data?: HealthLite;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <BrandSquare />
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-base font-semibold tracking-tight">
          {title}
        </h1>
        {(subtitle || data?.version) && (
          <div className="flex items-center gap-1.5 text-xs text-ink-3 truncate">
            <StatusDot active={(data?.active_downloads ?? 0) > 0} />
            {subtitle ? (
              <span className="truncate">{subtitle}</span>
            ) : (
              <span className="tabular truncate">
                v{data?.version ?? "?"} · {data?.sites_loaded ?? 0} sites
              </span>
            )}
          </div>
        )}
      </div>
      {/* On mobile the trailing slot drops to its own full-width row so a wide
          action group can wrap instead of overflowing (Slice 4c). Inline +
          right-aligned on desktop. */}
      {trailing && (
        <div className="flex justify-end shrink-0 max-sm:mt-2 max-sm:w-full">
          {trailing}
        </div>
      )}
    </div>
  );
}

function DisplayLayout({
  title,
  statusLine,
  belowStatus,
  trailing,
  data,
}: {
  title: string;
  statusLine?: React.ReactNode;
  belowStatus?: React.ReactNode;
  trailing?: React.ReactNode;
  data?: HealthLite;
}) {
  // If no statusLine is provided, fall back to the standard version
  // line so the bottom of the display row still carries the ambient
  // health signal.
  const status = statusLine ?? (
    <span className="tabular">
      v{data?.version ?? "?"} · {data?.sites_loaded ?? 0} sites
    </span>
  );

  return (
    <>
      <div className="flex flex-wrap items-start gap-3">
        <BrandSquare />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-2xl font-bold tracking-tighter">
            {title}
          </h1>
          {status && (
            <div className="mt-1 flex items-center gap-1.5 text-xs text-ink-3">
              <StatusDot active={(data?.active_downloads ?? 0) > 0} />
              <span className="truncate">{status}</span>
            </div>
          )}
        </div>
        {trailing && (
          <div className="flex justify-end shrink-0 max-sm:mt-2 max-sm:w-full">
            {trailing}
          </div>
        )}
      </div>
      {belowStatus && (
        <div className="mt-2.5 flex flex-wrap gap-1.5">{belowStatus}</div>
      )}
    </>
  );
}

function BackBreadcrumb({
  backTo,
  breadcrumb,
}: {
  backTo?: { to: string; label?: string };
  breadcrumb?: React.ReactNode;
}) {
  if (!backTo && !breadcrumb) return null;
  return (
    <div className="mb-1.5 flex items-center gap-2 text-xs text-ink-3">
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
      {breadcrumb && <span className="min-w-0 truncate">{breadcrumb}</span>}
    </div>
  );
}

function BrandSquare() {
  return (
    <div
      aria-hidden
      className={cn(
        "grid h-9 w-9 shrink-0 place-items-center rounded-md",
        "bg-ink text-surface font-bold tracking-tighter",
      )}
    >
      B
    </div>
  );
}

function StatusDot({ active }: { active: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "h-1.5 w-1.5 shrink-0 rounded-full",
        active ? "bg-green" : "bg-ink-3/40",
      )}
    />
  );
}
