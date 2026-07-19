import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  KNOWLEDGE_NOTE_KINDS,
  useAddKnowledgeNote,
  useKnowledgeNotes,
  useRemoveKnowledgeNote,
  type KnowledgeNoteKind,
} from "@/hooks/useKnowledgeNotes";

// v3.66.751 — GUI for the knowledge/notes CONTROL cluster (3 dark endpoints).
//
// Operator failure lore: `pattern` is a case-insensitive substring matched
// against future failure messages; when a failure matches, BD surfaces the
// note's `resolution` next to the failed row (knowledge.py runbook path).
// Derived from app_knowledge.py + knowledge.py:
//
//  * THE LIST'S ERROR SHAPE IS LOAD-BEARING. GET degrades to
//    {notes: [], error} at HTTP 500 — an empty store and a broken store
//    both show zero notes. The panel renders `error` out loud; "no notes
//    yet" is only ever said when there is no error.
//  * pattern + resolution are both required — a REAL 400. Submit stays
//    disabled until both are present; the panel never fires a doomed POST.
//  * `kind` is a CONSTRAINED select over the derived vocabulary
//    (failure | login | rate_limit) — nothing in the backend branches on
//    kind today, but the docstring reserves these; free text here becomes
//    meaning-wrong the day a consumer starts branching.
//  * Delete removes the note's pattern->resolution mapping for every
//    future failure — destructive, so it gets a confirm.
//
// NOTE: the library item notes endpoint (POST, library id in the path) is
// a DIFFERENT store (library item free-text) — same word, different table.
// It is deliberately NOT merged into this panel (identifier-masquerading
// risk); it belongs on Library. Its path is deliberately not written out
// here: the gui_parity scanner harvests /api literals from RAW TEXT,
// comments included, and a prose mention would mark that endpoint wired.

export function KnowledgeNotesPanel({ siteId }: { siteId: string }) {
  const [kindFilter, setKindFilter] = useState<string>("");
  const list = useKnowledgeNotes(siteId, kindFilter || undefined);
  const add = useAddKnowledgeNote();
  const remove = useRemoveKnowledgeNote();

  const [pattern, setPattern] = useState("");
  const [resolution, setResolution] = useState("");
  const [kind, setKind] = useState<KnowledgeNoteKind>("failure");
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);

  const canAdd =
    pattern.trim().length > 0 && resolution.trim().length > 0 && !add.isPending;

  const doAdd = () => {
    if (!canAdd) return; // both fields required — a real 400 server-side
    add.mutate(
      {
        site_id: siteId,
        kind,
        pattern: pattern.trim(),
        resolution: resolution.trim(),
      },
      {
        onSuccess: (r) => {
          if (!r.ok) {
            toast.error(r.error ?? "Could not save the note.");
            return;
          }
          setPattern("");
          setResolution("");
          toast.success("Note saved");
        },
        onError: (e) => toast.error(e.message),
      },
    );
  };

  const doRemove = (nid: number) => {
    remove.mutate(nid, {
      onSuccess: (r) => {
        if (!r.ok) toast.error(r.error ?? "Could not delete the note.");
        else toast.success("Note deleted");
      },
      onError: (e) => toast.error(e.message),
    });
  };

  const notes = list.data?.notes ?? [];
  const listError = list.data?.error ?? null;

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">Failure notes</h3>
        <select
          aria-label="Filter by kind"
          className="text-xs border rounded px-1 py-0.5 bg-background"
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value)}
        >
          <option value="">all kinds</option>
          {KNOWLEDGE_NOTE_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </div>

      <p className="text-xs text-muted-foreground">
        When a future failure message contains the pattern, the resolution is
        shown next to the failed row.
      </p>

      {/* The 500 shape: {notes: [], error}. Say it — a broken store must
          never read as an empty one. */}
      {listError && (
        <p className="text-xs text-red-600" role="alert">
          Notes could not be loaded: {listError}
        </p>
      )}

      {!listError && !list.isLoading && notes.length === 0 && (
        <p className="text-xs text-muted-foreground">
          No notes yet for this site.
        </p>
      )}

      {notes.length > 0 && (
        <ul className="space-y-2">
          {notes.map((n) => (
            <li
              key={n.id}
              className="text-xs border rounded p-2 flex items-start justify-between gap-2"
            >
              <div className="min-w-0">
                <div className="font-mono truncate">
                  <span className="text-muted-foreground">[{n.kind}]</span>{" "}
                  {n.pattern}
                </div>
                <div className="text-muted-foreground whitespace-pre-wrap">
                  {n.resolution}
                </div>
              </div>
              <Button
                variant="ghost"
                size="sm"
                aria-label={`Delete note ${n.id}`}
                onClick={() => setConfirmDelete(n.id)}
              >
                Delete
              </Button>
            </li>
          ))}
        </ul>
      )}

      <div className="space-y-2 border-t pt-3">
        <div className="flex gap-2 items-center">
          <label className="text-xs" htmlFor="kn-kind">
            Kind
          </label>
          <select
            id="kn-kind"
            className="text-xs border rounded px-1 py-0.5 bg-background"
            value={kind}
            onChange={(e) => setKind(e.target.value as KnowledgeNoteKind)}
          >
            {KNOWLEDGE_NOTE_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>
        <textarea
          aria-label="Failure pattern"
          className="w-full text-xs border rounded p-2 bg-background"
          rows={2}
          placeholder="Failure pattern (substring of the failure message, e.g. 'cloudflare challenge')"
          value={pattern}
          onChange={(e) => setPattern(e.target.value)}
        />
        <textarea
          aria-label="Resolution"
          className="w-full text-xs border rounded p-2 bg-background"
          rows={2}
          placeholder="How to fix it when this pattern appears"
          value={resolution}
          onChange={(e) => setResolution(e.target.value)}
        />
        <Button
          size="sm"
          disabled={!canAdd}
          title={canAdd ? "Save note" : "Pattern and resolution are both required"}
          onClick={doAdd}
        >
          Save note
        </Button>
      </div>

      <ConfirmDialog
        open={confirmDelete !== null}
        target={`note ${confirmDelete ?? ""}`}
        consequence="Future failures matching its pattern will no longer show this resolution."
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => {
          const nid = confirmDelete;
          setConfirmDelete(null);
          if (nid !== null) doRemove(nid);
        }}
      />
    </Card>
  );
}
