import { useQuery } from "@tanstack/react-query";

import { AppShell } from "@/components/AppShell";
import { apiGet } from "@/lib/api-client";

// v3.66.499 O2 — per-plugin metrics, as a first-class SPA route (replaces the
// deploy-excluded cockpit_console.py panel). Read-only: reads the existing
// /api/plugins/status `metrics` field (plugins.plugin_metrics) — no new endpoint.

export interface PluginMetric {
  key: string;
  calls: number;
  fails: number;
  total_s: number;
  avg_ms: number;
  last_ms: number;
  // v3.66.776 V3-E residual: tail percentiles (bounded recent window) + the
  // quarantine state joined on the same key by the backend.
  p50_ms?: number;
  p95_ms?: number;
  quarantined?: boolean;
}

type PluginsStatusResp = { metrics?: PluginMetric[] };

export function PluginMetrics() {
  const status = useQuery<PluginsStatusResp, Error>({
    queryKey: ["plugins-status-metrics"],
    queryFn: () => apiGet<PluginsStatusResp>("/api/plugins/status"),
  });

  const metrics = status.data?.metrics ?? [];

  return (
    <AppShell
      title="Plugin Metrics"
      subtitle="Per-plugin call / fail / latency counters (read-only)"
    >
      {status.isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : metrics.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No plugin invocations recorded yet.
        </p>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left">
              <th className="py-1 pr-3">Plugin</th>
              <th className="py-1 pr-3">Calls</th>
              <th className="py-1 pr-3">Fails</th>
              <th className="py-1 pr-3">Avg ms</th>
              <th className="py-1 pr-3">p50 ms</th>
              <th className="py-1 pr-3">p95 ms</th>
              <th className="py-1 pr-3">Last ms</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m) => (
              <tr key={m.key} className="border-t">
                <td className="py-1 pr-3 font-mono">
                  {m.key}
                  {m.quarantined ? (
                    <span
                      data-testid={`quarantined-${m.key}`}
                      className="ml-2 rounded border border-destructive px-1 text-xs text-destructive"
                      title="currently quarantined (past the fail budget, inside cooldown)"
                    >
                      quarantined
                    </span>
                  ) : null}
                </td>
                <td className="py-1 pr-3">{m.calls}</td>
                <td className={m.fails ? "py-1 pr-3 text-destructive" : "py-1 pr-3"}>
                  {m.fails}
                </td>
                <td className="py-1 pr-3">{m.avg_ms}</td>
                <td className="py-1 pr-3">{m.p50_ms ?? "-"}</td>
                <td className="py-1 pr-3">{m.p95_ms ?? "-"}</td>
                <td className="py-1 pr-3">{m.last_ms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </AppShell>
  );
}

export default PluginMetrics;
