// Budget — 380 daily byte-budget usage history (wired v3.66.382).
//
// Charts a site's recent daily byte usage (read-only). The global cross-site
// cap (global_daily_byte_budget) and per-site caps are set in Settings ->
// global config (that endpoint is already wired); this page surfaces the
// resulting usage trend so the operator can size the cap.

import * as React from "react";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useBudgetHistory } from "@/hooks/useBudgetHistory";

function fmtBytes(n: number): string {
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

export default function Budget() {
  const [input, setInput] = React.useState("");
  const [siteId, setSiteId] = React.useState("");
  const q = useBudgetHistory(siteId, 30);

  const history = q.data?.history ?? [];
  const max = history.reduce((m, h) => Math.max(m, h.bytes), 0) || 1;

  return (
    <AppShell title="Daily byte usage" subtitle="Recent daily bytes for a site. Set the global cross-site cap and per-site caps in Settings → global config.">
      <div className="space-y-4" data-testid="budget-page">

        <div className="flex items-end gap-2">
          <label className="text-sm">
            Site ID
            <Input
              aria-label="site id"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="e.g. example.com"
            />
          </label>
          <Button
            type="button"
            onClick={() => setSiteId(input.trim())}
            disabled={input.trim().length === 0}
          >
            Load
          </Button>
        </div>

        {siteId && q.isLoading && <p className="text-sm text-ink-soft">Loading…</p>}
        {siteId && q.isError && (
          <p role="alert" className="text-sm text-danger">
            Could not load usage for {siteId}.
          </p>
        )}
        {siteId && !q.isLoading && !q.isError && history.length === 0 && (
          <p className="text-sm text-ink-soft">No usage recorded for {siteId}.</p>
        )}

        {history.length > 0 && (
          <table className="bd-table w-full text-sm" data-testid="budget-table">
            <thead>
              <tr className="text-left text-ink-soft">
                <th className="py-1">Date</th>
                <th>Bytes</th>
                <th className="w-1/2">Usage</th>
              </tr>
            </thead>
            <tbody>
              {history.map((h) => (
                <tr key={h.ymd} className="border-t border-hairline">
                  <td className="py-1">{h.ymd}</td>
                  <td>{fmtBytes(h.bytes)}</td>
                  <td>
                    <div
                      className="h-2 rounded bg-accent"
                      style={{ width: `${Math.max(2, (h.bytes / max) * 100)}%` }}
                      aria-hidden
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </AppShell>
  );
}
