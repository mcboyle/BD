import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, XCircle, CloudSun, Server } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { Badge } from "@/components/ui/badge";
import { BuildTruthPanel, type BuildInfo } from "@/components/BuildTruthPanel";
import { CountTiles } from "@/components/CountTiles";
import { Button } from "@/components/ui/button";
import { RefreshChip } from "@/components/ui/RefreshChip";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useDashboard,
  useStats,
  useStatsBandwidth,
  useStatsTimeline,
  useHourlyStats,
  useCapacity,
  useStatus,
  useSessionStatus,
  useHealthChecklist,
  useWidgetsAll,
  useWeather,
  useChangelog,
  useRouteUrlsLookup,
} from "@/hooks/useDashboardData";
import { formatBytes, formatCount, formatRate } from "@/lib/format";
import { apiGet } from "@/lib/api-client";
import { useEventStream } from "@/hooks/useEventStream";

// ── T1 read-only dashboard (v3.66.205) ──────────────────────────────
//
// Ports the legacy `widgets.js` mini-framework as ONE consolidated SPA
// route (not 1:1 widget tiles), preserving the current dashboard contract
// 2 / T1. Pure read-only: every panel is a GET (the one POST,
// /api/route_urls, is a non-mutating URL→site lookup). No writes, no
// confirm-gates, no secret fields — those arrive in stateful tranches
// (T2+).
//
// This is the first route-level-code-split route: App.tsx loads it via
// React.lazy + Suspense, establishing the pattern the rest of Phase 2
// follows.

function Panel({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className="text-lg font-semibold tabular-nums">{value}</span>
    </div>
  );
}

function OverviewPanel() {
  const { data, isLoading } = useDashboard();
  if (isLoading) return <Skeleton className="h-24 w-full" />;
  const t = data?.totals ?? {};
  return (
    <div className="grid grid-cols-3 gap-4 sm:grid-cols-6">
      <Stat label="Running" value={formatCount(t.running ?? 0)} />
      <Stat label="Pending" value={formatCount(t.pending ?? 0)} />
      <Stat label="Done" value={formatCount(t.done ?? 0)} />
      <Stat label="Failed" value={formatCount(t.failed ?? 0)} />
      <Stat label="Review" value={formatCount(t.needs_review ?? 0)} />
      <Stat
        label="Throughput"
        value={formatRate(data?.bytes_per_sec_total ?? 0)}
      />
    </div>
  );
}

function StatusPanel() {
  const { data: status } = useStatus();
  const { data: session } = useSessionStatus();
  const siteCount = status ? Object.keys(status).length : 0;
  const keepers = session?.keepers ?? [];
  const connected = keepers.filter((k) => k?.state === "connected").length;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Server className="h-4 w-4 text-muted-foreground" />
        <span className="text-sm">
          {siteCount} site{siteCount === 1 ? "" : "s"} tracked
        </span>
      </div>
      <span className="text-sm text-muted-foreground">
        {connected}/{keepers.length} site sessions connected
      </span>
    </div>
  );
}

function HealthPanel() {
  const { data, isLoading } = useHealthChecklist();
  if (isLoading) return <Skeleton className="h-20 w-full" />;
  const checks = data?.checks ?? [];
  const overall = data?.overall_status;
  if (checks.length === 0)
    return <span className="text-sm text-muted-foreground">No checks reported.</span>;
  return (
    <div className="flex flex-col gap-2">
      {overall ? (
        <Badge variant={overall === "ok" ? "secondary" : "destructive"}>
          overall: {overall}
        </Badge>
      ) : null}
      <ul className="flex flex-col gap-1.5">
        {checks.map((c, i) => {
          const ok = c.status === "ok";
          const warn = c.status === "warn";
          return (
            <li key={c.name ?? i} className="flex items-center gap-2 text-sm">
              {ok ? (
                <CheckCircle2 className="h-4 w-4 text-green-600" />
              ) : (
                <XCircle
                  className={"h-4 w-4 " + (warn ? "text-amber-500" : "text-red-600")}
                />
              )}
              <span>{c.name ?? "check"}</span>
              {c.message ? (
                <span className="text-xs text-muted-foreground">— {c.message}</span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function CapacityPanel() {
  const { data, isLoading } = useCapacity();
  if (isLoading) return <Skeleton className="h-20 w-full" />;
  const freeGb = data?.disk?.free_gb;
  if (typeof freeGb !== "number" || !Number.isFinite(freeGb) || freeGb < 0)
    return <span className="text-sm text-muted-foreground">No volumes reported.</span>;
  const runwayDays = data?.disk?.runway_days;
  return (
    <ul className="flex flex-col gap-1.5">
      <li className="flex justify-between text-sm">
        <span className="truncate">Download storage</span>
        <span className="tabular-nums text-muted-foreground">
          {formatBytes(freeGb * 1024 ** 3)} free
          {typeof runwayDays === "number" && Number.isFinite(runwayDays) && runwayDays > 0
            ? ` · ${runwayDays.toFixed(1)}d runway`
            : null}
        </span>
      </li>
    </ul>
  );
}

function StatsPanel() {
  // Three throughput/stat reads consolidated into one panel.
  const { data: stats } = useStats();
  const { data: bandwidth } = useStatsBandwidth();
  const { data: timeline } = useStatsTimeline();
  const { data: hourly } = useHourlyStats();
  const bwPoints = bandwidth?.series?.length ?? 0;
  const tlPoints = timeline?.points?.length ?? 0;
  const hourBuckets = hourly?.hours?.length ?? 0;
  const statKeys = stats ? Object.keys(stats).length : 0;
  return (
    <div className="grid grid-cols-2 gap-4">
      <Stat label="Stat counters" value={statKeys} />
      <Stat label="Bandwidth samples" value={bwPoints} />
      <Stat label="Timeline points" value={tlPoints} />
      <Stat label="Hourly buckets" value={hourBuckets} />
    </div>
  );
}

function WeatherPanel() {
  const { data } = useWeather();
  return (
    <div className="flex items-center gap-2">
      <CloudSun className="h-5 w-5 text-muted-foreground" />
      <span className="text-sm">
        {data?.summary ?? "Weather unavailable"}
        {typeof data?.temp === "number" ? ` · ${data.temp}°` : ""}
      </span>
    </div>
  );
}

function SiteChangesPanel() {
  const { data } = useChangelog();
  const sites = (data?.sites ?? []).filter(
    (s) => s.headline_severity && s.headline_severity !== "ok",
  );
  if (sites.length === 0)
    return (
      <span className="text-sm text-muted-foreground">
        No site behavior changes flagged.
      </span>
    );
  const dot = (sev?: string) =>
    sev === "alert"
      ? "text-red-600"
      : sev === "warn"
        ? "text-amber-500"
        : "text-muted-foreground";
  return (
    <ul className="flex flex-col gap-1.5">
      {sites.slice(0, 5).map((s, i) => (
        <li key={s.site_id ?? i} className="flex flex-col text-sm">
          <span className="flex items-center gap-1.5">
            <span className={dot(s.headline_severity)}>●</span>
            <span className="font-medium">{s.site_id ?? "site"}</span>
            <span className="text-xs text-muted-foreground">
              {s.headline_severity}
            </span>
          </span>
          {s.entries?.[0]?.message ? (
            <span className="ml-3.5 text-xs text-muted-foreground">
              {s.entries[0].message}
            </span>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

function WidgetsConfigPanel() {
  const { data } = useWidgetsAll();
  const perSite = data?.per_site ? Object.keys(data.per_site).length : 0;
  const catalog = data?.catalog?.length ?? 0;
  return (
    <div className="grid grid-cols-2 gap-4">
      <Stat label="Catalog widgets" value={catalog} />
      <Stat label="Per-site overrides" value={perSite} />
    </div>
  );
}

function RouteLookupPanel() {
  const [text, setText] = useState("");
  const lookup = useRouteUrlsLookup();
  // F4.1 PWA share-target receiver. The manifest share_target action is
  // "/dashboard" (GET, params title/text/url); a phone "share to BD" lands
  // here. Pull a shared URL from the query string into the resolve box
  // (the existing add flow — all gates still apply), then strip the params
  // so a refresh doesn't re-trigger. Two taps: share, then Resolve.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    const shared = p.get("url") || p.get("text") || "";
    if (!shared) return;
    // Prefer an explicit url param; otherwise extract the first http(s)
    // link out of shared text (Android often puts the URL in `text`).
    const m = shared.match(/https?:\/\/\S+/);
    const url = p.get("url") || (m ? m[0] : "");
    if (url) {
      setText((prev) => (prev ? prev : url));
      // Clear share params without a navigation/history entry.
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);
  const run = () => {
    if (text.trim()) lookup.mutate(text);
  };
  const results = lookup.data?.results ?? [];
  return (
    <div className="flex flex-col gap-2">
      <Input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Paste URLs to resolve their site…"
      />
      <Button
        size="sm"
        variant="outline"
        onClick={run}
        disabled={lookup.isPending || !text.trim()}
      >
        {lookup.isPending ? "Resolving…" : "Resolve"}
      </Button>
      {results.length > 0 ? (
        <ul className="flex flex-col gap-1">
          {results.map((r, i) => (
            <li key={i} className="flex justify-between text-xs">
              <span className="truncate">{r.url}</span>
              <span className="text-muted-foreground">
                {r.site_id ?? (r.matched ? "matched" : "no match")}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

// Cut 6.1 — Dashboard version-truth panel. Reads /api/health (which exposes the
// backend build sha when build_info.json is present) and compares it to the
// FE-loaded build stamp baked at build time. Absent stamp -> versions only.
function BuildPanel() {
  const { data } = useQuery<{ version?: string; build?: BuildInfo }>({
    queryKey: ["health-build"],
    queryFn: ({ signal }) => apiGet("/api/health", signal),
    staleTime: 60_000,
    retry: 0,
  });
  const stamp =
    (import.meta.env.VITE_BUILD_STAMP as string | undefined) ?? null;
  return (
    <BuildTruthPanel
      version={data?.version ?? "?"}
      build={data?.build}
      buildStamp={stamp}
    />
  );
}

// Cut 6.2 — read-only count tiles sourced from the existing dashboard summary
// (+ a lightweight templates read). queue/review/capture come from the summary;
// template from /api/templates. The review tile links into the Cockpit.
function CountTilesPanel() {
  const { data } = useDashboard();
  const { data: tpl } = useQuery<{ templates?: unknown[] }>({
    queryKey: ["templates-all"],
    queryFn: ({ signal }) => apiGet("/api/templates", signal),
    staleTime: 60_000,
    retry: 0,
  });
  return (
    <CountTiles
      counts={{
        queue: data?.totals?.pending ?? 0,
        review: data?.totals?.needs_review ?? 0,
        capture: data?.active_workers ?? 0,
        template: tpl?.templates?.length ?? 0,
      }}
    />
  );
}

export function Dashboard() {
  // F4.5: consume the shared /api/stream `dashboard` push into the query
  // cache so the Overview/Status panels render live without fast polling.
  // useDashboard()'s refetchInterval backs off while the stream is connected
  // (isStreamConnected), and resumes polling if it drops.
  const queryClient = useQueryClient();
  const { dataUpdatedAt, isFetching } = useDashboard();
  useEventStream({
    dashboard: (d) => queryClient.setQueryData(["dashboard"], d),
  });
  return (
    <AppShell
      title="System Overview"
      subtitle="Read-only metrics & health"
      trailing={
        <RefreshChip
          updatedAt={dataUpdatedAt}
          refreshing={isFetching}
          onRefresh={() => queryClient.invalidateQueries({ queryKey: ["dashboard"] })}
        />
      }
    >
      <div className="flex flex-col gap-4">
        <CountTilesPanel />
        <Panel title="Overview" description="Queue + throughput at a glance">
          <OverviewPanel />
        </Panel>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Panel title="Build">
            <BuildPanel />
          </Panel>
          <Panel title="Status">
            <StatusPanel />
          </Panel>
          <Panel title="Health checklist">
            <HealthPanel />
          </Panel>
          <Panel title="Capacity">
            <CapacityPanel />
          </Panel>
          <Panel title="Stats">
            <StatsPanel />
          </Panel>
          <Panel title="Weather">
            <WeatherPanel />
          </Panel>
          <Panel title="Widgets config">
            <WidgetsConfigPanel />
          </Panel>
          <Panel
            title="Site changes"
            description="Sites with recent behavior drift"
          >
            <SiteChangesPanel />
          </Panel>
          <Panel
            title="Route lookup"
            description="Resolve which site a URL routes to"
          >
            <RouteLookupPanel />
          </Panel>
        </div>
      </div>
    </AppShell>
  );
}

export default Dashboard;
