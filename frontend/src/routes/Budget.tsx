// Budget — 380 daily byte-budget usage history (wired v3.66.382).
//
// Charts a site's recent daily byte usage and (F041) edits the global
// cross-site cap (global_daily_byte_budget) in place: Edit opens a draft,
// Cancel discards it without a request, Save POSTs /api/global_config.
// Per-site caps stay in Settings -> global config.

import * as React from "react";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useBudgetHistory } from "@/hooks/useBudgetHistory";
import { useGlobalByteBudget } from "@/hooks/useGlobalByteBudget";

function fmtBytes(n: number): string {
  // F8: NaN slipped past `n <= 0` and rendered "NaN undefined".
  if (!Number.isFinite(n)) return "—";
  if (n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.max(0, Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024))));
  return `${(n / Math.pow(1024, i)).toFixed(1)} ${units[i]}`;
}

// A cap draft is whole non-negative bytes; anything else is refused, not coerced.
function parseCap(draft: string): number | null {
  const t = draft.trim();
  return /^\d+$/.test(t) && Number.isSafeInteger(Number(t)) ? Number(t) : null;
}

function GlobalCapEditor() {
  const { query, save } = useGlobalByteBudget();
  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState("");
  const cap = query.data;
  const parsed = parseCap(draft);

  const cancel = () => {
    setEditing(false);
    setDraft("");
    save.reset();
  };

  return (
    <section className="space-y-2" data-testid="budget-cap">
      <h2 className="text-sm font-medium">Global daily byte cap</h2>
      {query.isLoading && <p className="text-sm text-ink-soft">Loading…</p>}
      {query.isError && (
        <p role="alert" className="text-sm text-danger">
          Could not load the global daily byte cap.
        </p>
      )}
      {cap !== undefined && !editing && (
        <div className="flex items-center gap-2">
          <span className="text-sm" data-testid="budget-cap-value">
            {cap > 0 ? `${fmtBytes(cap)} (${cap} bytes)` : "Uncapped"}
          </span>
          <Button
            type="button"
            onClick={() => {
              setDraft(String(cap));
              setEditing(true);
            }}
          >
            Edit
          </Button>
        </div>
      )}
      {cap !== undefined && editing && (
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (parsed === null) return;
            save.mutate(parsed, { onSuccess: cancel });
          }}
        >
          <label className="text-sm">
            Cap in bytes (0 = uncapped)
            <Input
              aria-label="global daily byte cap"
              inputMode="numeric"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          </label>
          <Button type="submit" disabled={parsed === null || save.isPending}>
            Save
          </Button>
          <Button type="button" onClick={cancel} disabled={save.isPending}>
            Cancel
          </Button>
        </form>
      )}
      {editing && parsed === null && (
        <p role="alert" className="text-sm text-danger">
          Enter a whole number of bytes (0 or more).
        </p>
      )}
      {editing && save.isError && (
        <p role="alert" className="text-sm text-danger">
          Could not save the global daily byte cap.
        </p>
      )}
    </section>
  );
}

export default function Budget() {
  const [input, setInput] = React.useState("");
  const [siteId, setSiteId] = React.useState("");
  const q = useBudgetHistory(siteId, 30);

  const history = q.data?.history ?? [];
  const max =
    history.reduce((m, h) => (Number.isFinite(h.bytes) ? Math.max(m, h.bytes) : m), 0) || 1;

  return (
    <AppShell title="Daily byte usage" subtitle="Recent daily bytes for a site. Edit the global cross-site cap here; per-site caps are in Settings → global config.">
      <div className="space-y-4" data-testid="budget-page">
        <GlobalCapEditor />

        <div className="flex items-end gap-2">
          <label className="text-sm">
            Site ID
            <Input
              aria-label="site id"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                // F8: Enter loads, like the button.
                if (e.key === "Enter" && input.trim()) setSiteId(input.trim());
              }}
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
