// AlertRules — 380 alert-rules editor + test-send (wired v3.66.382).
//
// Lists built-in + custom rules, lets the operator upsert a custom rule
// (id, metric, comparison, threshold) and remove custom ones, and fire an
// immediate evaluation pass ("test-send") instead of waiting for the 60s cron.

import * as React from "react";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAlertRules } from "@/hooks/useAlertRules";

const METRICS = [
  "bd_failure_rate_1h",
  "bd_pending_count",
  "bd_disk_free_gb",
  "bd_circuit_breakers_open",
  "bd_bitrot_open_issues",
  "bd_oldest_pending_hours",
  "bd_job_failures_1h",
];
const OPS = [">=", "<=", ">", "<", "=="];

export default function AlertRules() {
  const { query, save, remove, testEvaluate } = useAlertRules();
  const [id, setId] = React.useState("");
  const [metric, setMetric] = React.useState(METRICS[0]);
  const [op, setOp] = React.useState(">=");
  const [threshold, setThreshold] = React.useState("");

  const rules = query.data?.rules ?? [];
  const canSave =
    id.trim().length > 0 && threshold.trim().length > 0 && !save.isPending;

  function onSave() {
    if (!canSave) return;
    save.mutate(
      { id: id.trim(), metric, op, threshold: Number(threshold) },
      {
        onSuccess: (r) => {
          if (r.ok) {
            setId("");
            setThreshold("");
          }
        },
      },
    );
  }

  return (
    <AppShell
      title="Alert rules"
      subtitle="Threshold rules evaluated every 60s. Edit a rule, then test-send to evaluate immediately."
      trailing={
        <Button
          type="button"
          variant="outline"
          onClick={() => testEvaluate.mutate()}
          disabled={testEvaluate.isPending}
        >
          {testEvaluate.isPending ? "Evaluating\u2026" : "Test-send (evaluate now)"}
        </Button>
      }
    >
      <div className="space-y-4" data-testid="alert-rules-page">

        <div className="space-y-2 rounded-md border border-hairline p-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-sm">
              Rule id
              <Input
                aria-label="rule id"
                value={id}
                onChange={(e) => setId(e.target.value)}
                placeholder="my_custom_rule"
              />
            </label>
            <label className="text-sm">
              Metric
              <select
                aria-label="metric"
                className="block rounded-md border border-hairline bg-transparent p-2 text-sm"
                value={metric}
                onChange={(e) => setMetric(e.target.value)}
              >
                {METRICS.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Op
              <select
                aria-label="op"
                className="block rounded-md border border-hairline bg-transparent p-2 text-sm"
                value={op}
                onChange={(e) => setOp(e.target.value)}
              >
                {OPS.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-sm">
              Threshold
              <Input
                aria-label="threshold"
                type="number"
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
              />
            </label>
            <Button type="button" onClick={onSave} disabled={!canSave}>
              {save.isPending ? "Saving\u2026" : "Save rule"}
            </Button>
          </div>
          {save.data && !save.data.ok && (
            <p role="alert" className="text-sm text-danger">
              {save.data.error || "Could not save rule"}
            </p>
          )}
        </div>

        {query.isLoading && <p className="text-sm text-ink-soft">Loading…</p>}
        {query.isError && (
          <p role="alert" className="text-sm text-danger">
            Could not load rules.
          </p>
        )}

        {rules.length > 0 && (
          <table className="bd-table w-full text-sm" data-testid="alert-rules-table">
            <thead>
              <tr className="text-left text-ink-soft">
                <th className="py-1">Rule</th>
                <th>Metric</th>
                <th>Condition</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr key={r.id} className="border-t border-hairline">
                  <td className="py-1">
                    {r.name || r.id}
                    {r.builtin && (
                      <span className="ml-2 text-xs text-ink-soft">built-in</span>
                    )}
                  </td>
                  <td>{r.metric}</td>
                  <td>
                    {r.op} {r.threshold}
                  </td>
                  <td className="py-1">
                    {!r.builtin && (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => remove.mutate(r.id)}
                        disabled={remove.isPending}
                      >
                        Remove
                      </Button>
                    )}
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
