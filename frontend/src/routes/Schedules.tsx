// Schedules — 379 recurring-capture schedules (wired v3.66.382).
//
// Operator UI over the capture_schedules write surface: list existing
// schedules with their cadence + next/last-run, add a new one, edit one's
// cadence/label (F038), run one now, or delete. All writes ride useSchedules (CSRF via apiPost). Recurring
// captures feed the normal run path; this never touches capture/extraction.

import * as React from "react";

import { AppShell } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useSchedules } from "@/hooks/useSchedules";

function fmtTs(ts?: number): string {
  if (!ts) return "\u2014";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

export default function Schedules() {
  const { query, add, update, remove, runNow } = useSchedules();
  const [siteId, setSiteId] = React.useState("");
  const [cadence, setCadence] = React.useState("24");
  const [label, setLabel] = React.useState("");
  const [editId, setEditId] = React.useState<number | null>(null);
  const [editCadence, setEditCadence] = React.useState("");
  const [editLabel, setEditLabel] = React.useState("");

  const schedules = query.data?.schedules ?? [];
  const canAdd = siteId.trim().length > 0 && Number(cadence) > 0 && !add.isPending;

  function onAdd() {
    if (!canAdd) return;
    add.mutate(
      { site_id: siteId.trim(), cadence_hours: Number(cadence), label: label.trim() },
      {
        onSuccess: (r) => {
          if (r.ok) {
            setSiteId("");
            setLabel("");
            setCadence("24");
          }
        },
      },
    );
  }

  function onEdit(id: number, cadenceHours: number, curLabel?: string) {
    setEditId(id);
    setEditCadence(String(cadenceHours));
    setEditLabel(curLabel ?? "");
  }

  // Whole hours only: the API refuses a fractional cadence (400) rather than truncating it.
  const canSave =
    editId !== null && Number.isInteger(Number(editCadence)) && Number(editCadence) > 0 && !update.isPending;

  function onSave() {
    if (!canSave || editId === null) return;
    update.mutate(
      { id: editId, cadence_hours: Number(editCadence), label: editLabel.trim() },
      { onSuccess: (r) => { if (r.ok) setEditId(null); } },
    );
  }

  return (
    <AppShell title="Recurring capture schedules" subtitle="Re-capture a site on a cadence. Due schedules enqueue through the normal run path; capture and extraction behaviour is unchanged.">
      <div className="space-y-4" data-testid="schedules-page">

        <div className="space-y-2 rounded-md border border-hairline p-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-sm">
              Site ID
              <Input
                aria-label="site id"
                value={siteId}
                onChange={(e) => setSiteId(e.target.value)}
                placeholder="e.g. example.com"
              />
            </label>
            <label className="text-sm">
              Cadence (hours)
              <Input
                aria-label="cadence hours"
                type="number"
                min={1}
                value={cadence}
                onChange={(e) => setCadence(e.target.value)}
              />
            </label>
            <label className="text-sm">
              Label (optional)
              <Input
                aria-label="label"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
            </label>
            <Button type="button" onClick={onAdd} disabled={!canAdd}>
              {add.isPending ? "Adding\u2026" : "Add schedule"}
            </Button>
          </div>
          {add.data && !add.data.ok && (
            <p role="alert" className="text-sm text-danger">
              {add.data.error || "Could not add schedule"}
            </p>
          )}
        </div>

        {update.data && !update.data.ok && (
          <p role="alert" className="text-sm text-danger">
            {update.data.error || "Could not save schedule"}
          </p>
        )}

        {query.isLoading && <p className="text-sm text-ink-soft">Loading…</p>}
        {query.isError && (
          <p role="alert" className="text-sm text-danger">
            Could not load schedules.
          </p>
        )}
        {!query.isLoading && !query.isError && schedules.length === 0 && (
          <p className="text-sm text-ink-soft">No schedules yet.</p>
        )}

        {schedules.length > 0 && (
          <table className="bd-table w-full text-sm" data-testid="schedules-table">
            <thead>
              <tr className="text-left text-ink-soft">
                <th className="py-1">Site</th>
                <th>Cadence</th>
                <th>Label</th>
                <th>Next run</th>
                <th>Last run</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id} className="border-t border-hairline">
                  <td className="py-1">{s.site_id}</td>
                  {editId === s.id ? (
                    <>
                      <td>
                        <Input
                          aria-label="edit cadence hours"
                          type="number"
                          min={1}
                          step={1}
                          value={editCadence}
                          onChange={(e) => setEditCadence(e.target.value)}
                        />
                      </td>
                      <td>
                        <Input
                          aria-label="edit label"
                          value={editLabel}
                          onChange={(e) => setEditLabel(e.target.value)}
                        />
                      </td>
                    </>
                  ) : (
                    <>
                      <td>{s.cadence_hours}h</td>
                      <td>{s.label || "\u2014"}</td>
                    </>
                  )}
                  <td>{fmtTs(s.next_run_ts)}</td>
                  <td>{fmtTs(s.last_run_ts)}</td>
                  <td className="flex gap-2 py-1">
                    {editId === s.id ? (
                      <>
                        <Button type="button" size="sm" onClick={onSave} disabled={!canSave}>
                          {update.isPending ? "Saving\u2026" : "Save"}
                        </Button>
                        <Button type="button" variant="outline" size="sm" onClick={() => setEditId(null)}>
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => onEdit(s.id, s.cadence_hours, s.label)}
                      >
                        Edit
                      </Button>
                    )}
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => runNow.mutate(s.id)}
                      disabled={runNow.isPending}
                    >
                      Run now
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => remove.mutate(s.id)}
                      disabled={remove.isPending}
                    >
                      Remove
                    </Button>
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
