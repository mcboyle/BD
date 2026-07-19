import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  APPLY_MODES,
  useApplyQueueTemplate,
  useCreateQueueTemplate,
  useDeleteQueueTemplate,
  useQueueTemplate,
  useQueueTemplates,
  useUpdateQueueTemplate,
  type ApplyMode,
} from "@/hooks/useQueueTemplates";
import { apiGet } from "@/lib/api-client";
import type { SitesV2 } from "@/lib/api-types";

// v3.66.733 — GUI for the queue_templates CONTROL cluster (3 dark endpoints).
//
// The vocabulary here is DERIVED from bulk_downloader/app_queue_templates.py +
// queue_templates.py, not invented:
//
//   * `mode` is APPEND or REPLACE and nothing else -- the view 400s on
//     ("unknown mode: X"), so a third option would be a doomed request.
//   * `mode` rides the QUERY STRING (request.args), never the body. See the
//     header of hooks/useQueueTemplates.ts; this is the defect the cut exists
//     to avoid.
//   * REPLACE clears the target site's queue first (queue_delete_site +
//     runner.jobs.clear()). It is destructive and gets a Tier-A confirm.
//     APPEND is additive and gets none -- a confirm in front of a safe action
//     is how operators learn to click through the real ones.
//   * `name` is REQUIRED on create (400 "name required" on empty/whitespace).
//   * A PUT with no changed fields returns {"ok": false} having done nothing
//     (`if not sets: return False`), so Save stays disabled until dirty.
//   * The list response has NO urls (list_all omits them; it returns
//     url_count), so the editor GETs the single template to populate.

function fmtWhen(ts: number | null | undefined): string {
  if (!ts) return "never";
  return new Date(ts * 1000).toLocaleDateString();
}

export function QueueTemplatesPanel() {
  const list = useQueueTemplates();
  const create = useCreateQueueTemplate();
  const update = useUpdateQueueTemplate();
  const remove = useDeleteQueueTemplate();
  const apply = useApplyQueueTemplate();

  // Sites for the apply target. The runner must exist or apply 404s ("Not
  // found") -- so the picker offers only sites the backend actually has.
  const sites = useQuery<SitesV2>({
    queryKey: ["sites-v2"],
    queryFn: ({ signal }) => apiGet<SitesV2>("/api/sites/v2", signal),
  });

  const [editing, setEditing] = useState<number | null>(null);
  const one = useQueueTemplate(editing);

  const [name, setName] = useState("");
  const [urlsText, setUrlsText] = useState("");
  const [note, setNote] = useState("");

  const [applyTid, setApplyTid] = useState<number | null>(null);
  const [applySid, setApplySid] = useState("");
  const [applyMode, setApplyMode] = useState<ApplyMode>("append");
  const [confirmReplace, setConfirmReplace] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  // Populate the editor once the full record lands (the list has no urls).
  const loaded = one.data?.template;
  useEffect(() => {
    if (editing !== null && loaded) {
      setName(loaded.name ?? "");
      setUrlsText((loaded.urls ?? []).join("\n"));
      setNote(loaded.note ?? "");
    }
  }, [editing, loaded]);

  const rows = list.data?.templates ?? [];
  const siteIds = (sites.data?.sites ?? []).map((s) => s.site_id);

  const parseUrls = (t: string) =>
    t
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);

  const resetForm = () => {
    setEditing(null);
    setName("");
    setUrlsText("");
    setNote("");
  };

  // Create: the backend 400s on an empty name. Gate it rather than fire a
  // request we know is refused.
  const canCreate = name.trim().length > 0 && !create.isPending;

  // Save: a PUT with nothing changed answers ok:false having done nothing.
  const dirty =
    !!loaded &&
    (name !== (loaded.name ?? "") ||
      urlsText !== (loaded.urls ?? []).join("\n") ||
      note !== (loaded.note ?? ""));
  const canSave = editing !== null && dirty && !update.isPending;

  const submitCreate = () => {
    if (!canCreate) return;
    create.mutate(
      { name: name.trim(), urls: parseUrls(urlsText), note: note.trim() },
      {
        onSuccess: (r) => {
          if (r.ok) {
            toast.success(`Template saved (#${r.id}).`);
            resetForm();
          } else {
            toast.error(r.error ?? "Could not save the template.");
          }
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const submitSave = () => {
    if (!canSave || editing === null) return;
    update.mutate(
      { tid: editing, name: name.trim(), urls: parseUrls(urlsText), note: note.trim() },
      {
        onSuccess: (r) =>
          r.ok ? toast.success("Template updated.") : toast.error("Nothing was updated."),
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const fireApply = (tid: number, sid: string, mode: ApplyMode) => {
    apply.mutate(
      { tid, sid, mode },
      {
        onSuccess: (r) =>
          r.ok
            ? toast.success(`Imported ${r.added ?? 0} URL(s) into ${sid} (${r.mode ?? mode}).`)
            : toast.error(r.error ?? "Apply failed."),
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const submitApply = () => {
    if (applyTid === null || !applySid) return;
    // REPLACE wipes the target queue -- confirm. APPEND does not -- do not.
    if (applyMode === "replace") {
      setConfirmReplace(true);
      return;
    }
    fireApply(applyTid, applySid, "append");
  };

  return (
    <Card className="p-4 space-y-4" data-testid="queue-templates-panel">
      <div>
        <h2 className="text-lg font-semibold">Queue templates</h2>
        <p className="text-sm text-muted-foreground">
          Save a reusable URL set and import it into a site&apos;s queue.
        </p>
      </div>

      {list.isLoading ? (
        <Skeleton className="h-20 w-full" />
      ) : list.isError ? (
        <p className="text-sm text-destructive">Could not load templates.</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-muted-foreground">No templates yet.</p>
      ) : (
        <ul className="divide-y" data-testid="queue-template-list">
          {rows.map((t) => (
            <li key={t.id} className="py-2 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="font-medium truncate">{t.name}</div>
                <div className="text-xs text-muted-foreground">
                  {t.url_count} URL(s) · used {t.use_count}× · last {fmtWhen(t.ts_used)}
                  {t.note ? ` · ${t.note}` : ""}
                </div>
              </div>
              <div className="flex gap-2 shrink-0">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setApplyTid(t.id)}
                  aria-label={`Apply ${t.name}`}
                >
                  Apply
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditing(t.id)}
                  aria-label={`Edit ${t.name}`}
                >
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setConfirmDelete(t.id)}
                  aria-label={`Delete ${t.name}`}
                >
                  Delete
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Apply -- target site + mode. */}
      {applyTid !== null && (
        <div className="rounded border p-3 space-y-3" data-testid="queue-template-apply">
          <div className="text-sm font-medium">Import into a site</div>
          <div className="flex flex-wrap gap-2 items-center">
            <select
              className="h-9 rounded-md border bg-background px-2 text-sm"
              aria-label="Target site"
              value={applySid}
              onChange={(e) => setApplySid(e.target.value)}
            >
              <option value="">Select a site…</option>
              {siteIds.map((sid) => (
                <option key={sid} value={sid}>
                  {sid}
                </option>
              ))}
            </select>
            <select
              className="h-9 rounded-md border bg-background px-2 text-sm"
              aria-label="Import mode"
              value={applyMode}
              onChange={(e) => setApplyMode(e.target.value as ApplyMode)}
            >
              {APPLY_MODES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
            <Button size="sm" disabled={!applySid || apply.isPending} onClick={submitApply}>
              Import
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setApplyTid(null)}>
              Cancel
            </Button>
          </div>
          {applyMode === "replace" && (
            <p className="text-xs text-destructive">
              Replace clears the target site&apos;s queue before importing.
            </p>
          )}
        </div>
      )}

      {/* Create / edit. */}
      <div className="space-y-2 border-t pt-3">
        <div className="text-sm font-medium">
          {editing === null ? "New template" : `Editing #${editing}`}
        </div>
        <Input
          aria-label="Template name"
          placeholder="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <textarea
          aria-label="Template URLs"
          className="w-full min-h-24 rounded-md border bg-background p-2 text-sm font-mono"
          placeholder="One URL per line"
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
        />
        <Input
          aria-label="Template note"
          placeholder="Note (optional)"
          value={note}
          onChange={(e) => setNote(e.target.value)}
        />
        <div className="flex gap-2">
          {editing === null ? (
            <Button size="sm" disabled={!canCreate} onClick={submitCreate}>
              Create template
            </Button>
          ) : (
            <>
              <Button size="sm" disabled={!canSave} onClick={submitSave}>
                Save changes
              </Button>
              <Button variant="ghost" size="sm" onClick={resetForm}>
                Cancel
              </Button>
            </>
          )}
        </div>
      </div>

      <ConfirmDialog
        open={confirmReplace}
        target={`the queue on ${applySid}`}
        consequence="Replace clears every job in that site's queue before importing the template."
        onCancel={() => setConfirmReplace(false)}
        onConfirm={() => {
          setConfirmReplace(false);
          if (applyTid !== null && applySid) fireApply(applyTid, applySid, "replace");
        }}
      />

      <ConfirmDialog
        open={confirmDelete !== null}
        target="this template"
        consequence="This permanently deletes it."
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          const tid = confirmDelete;
          setConfirmDelete(null);
          if (tid === null) return;
          remove.mutate(tid, {
            onSuccess: (r) =>
              r.ok ? toast.success("Template deleted.") : toast.error("Template not found."),
            onError: (e) => toast.error(e.message),
          });
          if (editing === tid) resetForm();
        }}
      />
    </Card>
  );
}
