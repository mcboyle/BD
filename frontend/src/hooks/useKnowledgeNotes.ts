import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPost } from "@/lib/api-client";

// v3.66.751 — the knowledge/notes CONTROL cluster (operator failure lore).
//
//   GET    /api/knowledge/notes?site_id=&kind=  -> {notes: [...]} — degrades
//          to {notes: [], error} WITH HTTP 500 on failure. An empty list and
//          a broken store are different worlds; the panel must render the
//          error, never launder it into "no notes yet".
//   POST   /api/knowledge/notes {site_id, kind, pattern, resolution}
//          pattern+resolution both required — a REAL 400. The UI disables
//          submit until both are present rather than firing a doomed POST.
//   DELETE /api/knowledge/notes/<int:nid>       — destructive; confirm first.
//
// `kind` vocabulary is DERIVED from knowledge.py, not invented: nothing in
// the backend branches on kind values (list_notes filters verbatim), the
// schema has no CHECK, and add_note's docstring names 'failure' as current
// with 'login'/'rate_limit' reserved. So the UI offers exactly that set as
// a constrained select — free text would be a type-correct, meaning-wrong
// control the moment a consumer starts branching.

export const KNOWLEDGE_NOTE_KINDS = ["failure", "login", "rate_limit"] as const;
export type KnowledgeNoteKind = (typeof KNOWLEDGE_NOTE_KINDS)[number];

export interface KnowledgeNote {
  id: number;
  site_id?: string;
  kind?: string;
  pattern?: string;
  resolution?: string;
  created_at?: number;
  [k: string]: unknown;
}

export interface KnowledgeNotesList {
  notes: KnowledgeNote[];
  error?: string;
}

export interface KnowledgeNoteAddResult {
  ok: boolean;
  id?: number;
  error?: string;
}

export interface KnowledgeNoteRemoveResult {
  ok: boolean;
  error?: string;
}

export function useKnowledgeNotes(siteId?: string, kind?: string) {
  const params = new URLSearchParams();
  if (siteId) params.set("site_id", siteId);
  if (kind) params.set("kind", kind);
  const qs = params.toString();
  return useQuery<KnowledgeNotesList>({
    queryKey: ["knowledge", "notes", siteId ?? "", kind ?? ""],
    // literal ends at `?` then interpolates — the gui_parity scanner's
    // recognized shape (see useHistoryData); a conditional template here
    // reads identically at runtime and INVISIBLY to the wiring scanner.
    queryFn: ({ signal }) =>
      apiGet<KnowledgeNotesList>(`/api/knowledge/notes?${qs}`, signal),
  });
}

export function useAddKnowledgeNote() {
  const qc = useQueryClient();
  return useMutation<
    KnowledgeNoteAddResult,
    Error,
    { site_id: string; kind: KnowledgeNoteKind; pattern: string; resolution: string }
  >({
    mutationFn: (body) =>
      apiPost<KnowledgeNoteAddResult>("/api/knowledge/notes", body),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["knowledge", "notes"] });
    },
  });
}

export function useRemoveKnowledgeNote() {
  const qc = useQueryClient();
  return useMutation<KnowledgeNoteRemoveResult, Error, number>({
    mutationFn: (nid) =>
      apiDelete<KnowledgeNoteRemoveResult>(`/api/knowledge/notes/${nid}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["knowledge", "notes"] });
    },
  });
}
