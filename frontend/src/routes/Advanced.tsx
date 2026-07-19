import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Check, X, AlertTriangle } from "lucide-react";
import { Link } from "react-router-dom";

import { AppShell } from "@/components/AppShell";
import {
  SettingRow,
  SettingSection,
} from "@/components/SettingSection";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { apiGet } from "@/lib/api-client";
import { formatBytes, formatPercent } from "@/lib/format";
import type { HealthV2 } from "@/lib/api-types";
import { cn } from "@/lib/utils";
import { DevToolsSection } from "@/components/sections/DevToolsSection";

// Advanced sub-page. Mockup advanced.png:
//   - Tinted "power user only" warning at top
//   - HEALTH section: live check, DB integrity, WAL mode, Ollama,
//     last suite, VPN (omitted — VPN status is its own complex API)
//   - SYSTEM section: version, uptime, sites loaded, queue depth
//
// Reads /api/health/v2 (added in U2). Refreshes every 15s — the
// page is read-only and gives users a confidence check that the
// system is alive, not a control surface.

export function Advanced() {
  const { data, isLoading, isError, dataUpdatedAt } = useQuery<HealthV2>({
    queryKey: ["health-v2-advanced"],
    queryFn: ({ signal }) => apiGet<HealthV2>("/api/health/v2", signal),
    refetchInterval: 15_000,
    refetchOnWindowFocus: true,
  });

  const trailing = (
    <Link
      to="/settings"
      className="grid h-8 w-8 place-items-center rounded-sm text-ink-3 hover:bg-surface-2 hover:text-ink"
      aria-label="Back to settings"
    >
      <ArrowLeft className="h-4 w-4" aria-hidden />
    </Link>
  );

  return (
    <AppShell
      title="Advanced"
      subtitle="Power user diagnostics"
      trailing={trailing}
    >
      <Card className="border-amber bg-amber-soft p-3 text-xs text-amber-dim">
        <strong className="font-semibold">Power user only.</strong> Changes
        here can affect downloads in progress. Most users don't need this
        page.
      </Card>

      <Link
        to="/templates"
        className="mt-3 flex items-center justify-between rounded-md border bg-surface p-3 hairline hover:bg-surface-2"
      >
        <div className="min-w-0">
          <div className="text-sm font-medium text-ink">Template Manager</div>
          <div className="text-xs text-ink-3">
            Review, promote, and disable site templates
          </div>
        </div>
        <ArrowLeft className="h-4 w-4 rotate-180 text-ink-3" aria-hidden />
      </Link>

      {isError && (
        <Card className="mt-3 border-red bg-red-soft p-3 text-sm text-red" role="alert">
          Couldn't load health.
        </Card>
      )}

      {isLoading && !data && (
        <div className="mt-3 space-y-3">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      )}

      {data && (
        <div className="mt-3 space-y-5">
          <SettingSection label="Health">
            <HealthRow
              label="Backend"
              hint={data.degraded ? `Degraded: ${data.degraded}` : "All checks passing."}
              status={data.ok ? "pass" : "warn"}
              detail={data.ok ? "OK" : "DEGRADED"}
            />
            <HealthRow
              label="Database"
              hint="Liveness + journal mode."
              status={data.db_ok ? "pass" : "fail"}
              detail={
                data.db_ok
                  ? data.db_journal_mode?.toUpperCase() || "OK"
                  : "ERROR"
              }
            />
            <HealthRow
              label="AI · Ollama"
              hint={
                data.ollama.reachable
                  ? data.ollama.model || "Reachable, no model"
                  : data.ollama.error || "Unreachable"
              }
              status={data.ollama.reachable ? "pass" : "warn"}
              detail={data.ollama.reachable ? "OK" : "OFF"}
            />
            <HealthRow
              label="Last full test suite"
              hint={
                data.last_suite.available
                  ? `Ran ${formatRelative(data.last_suite.mtime_ts)} ago`
                  : "No SUMMARY.txt found."
              }
              status={data.last_suite.available ? "pass" : "warn"}
              detail={data.last_suite.available ? "OK" : "—"}
            />
          </SettingSection>

          {data.disks.length > 0 && (
            <SettingSection
              label="Disk"
              description="Free space per configured download dir."
            >
              {data.disks.map((d) => (
                <SettingRow
                  key={d.path}
                  label={d.path}
                  hint={`${formatPercent(d.free_pct, 1)} free`}
                  control={
                    <span
                      className={cn(
                        "tabular text-xs",
                        d.free_pct < 10
                          ? "text-red"
                          : d.free_pct < 25
                            ? "text-amber"
                            : "text-ink-2",
                      )}
                    >
                      {formatBytes(d.free_gb * 1024 ** 3, 1)} /{" "}
                      {formatBytes(d.total_gb * 1024 ** 3, 0)}
                    </span>
                  }
                  stacked
                />
              ))}
            </SettingSection>
          )}

          <SettingSection label="System">
            <SettingRow
              label="Version"
              control={
                <span className="tabular text-xs text-ink-2">
                  v{data.version}
                </span>
              }
            />
            <SettingRow
              label="Uptime"
              control={
                <span className="tabular text-xs text-ink-2">
                  {formatUptime(data.uptime_s)}
                </span>
              }
            />
            <SettingRow
              label="Sites loaded"
              control={
                <span className="tabular text-xs text-ink-2">
                  {data.sites_loaded ?? "—"}
                </span>
              }
            />
            <SettingRow
              label="Queue depth"
              hint="Pending + running across all sites."
              control={
                <span className="tabular text-xs text-ink-2">
                  {data.queue_depth ?? 0}
                </span>
              }
            />
          </SettingSection>

          {dataUpdatedAt > 0 && (
            <p className="px-1 text-[10px] tabular text-ink-3">
              Updated {formatRelative(Math.floor(dataUpdatedAt / 1000))} ago
            </p>
          )}
        </div>
      )}
      <DevToolsSection />
    </AppShell>
  );
}

type HealthStatus = "pass" | "warn" | "fail";

function HealthRow({
  label,
  hint,
  status,
  detail,
}: {
  label: string;
  hint?: string;
  status: HealthStatus;
  detail: string;
}) {
  const { icon: Icon, color } =
    status === "pass"
      ? { icon: Check, color: "text-green" }
      : status === "warn"
        ? { icon: AlertTriangle, color: "text-amber" }
        : { icon: X, color: "text-red" };
  return (
    <SettingRow
      label={label}
      hint={hint}
      control={
        <span className={cn("flex items-center gap-1.5 text-xs font-medium", color)}>
          <Icon className="h-3.5 w-3.5" aria-hidden />
          {detail}
        </span>
      }
    />
  );
}

function formatUptime(seconds: number): string {
  if (!seconds || seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    return `${h}h ${m}m`;
  }
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  return `${d}d ${h}h`;
}

function formatRelative(ts: number | undefined): string {
  if (!ts) return "—";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000) - ts);
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86400)}d`;
}
