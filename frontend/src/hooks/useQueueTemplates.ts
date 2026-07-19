import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiGet, apiPost, apiPut } from "@/lib/api-client";

// v3.66.733 — the queue_templates CONTROL cluster.
//
// bulk_downloader/app_queue_templates.py has existed with no GUI path:
//   GET/POST   /api/queue_templates                       list / create
//   GET/PUT/DELETE /api/queue_templates/<int:tid>         read / edit / drop
//   POST       /api/queue_templates/<int:tid>/apply/<sid> import into a site
// Saving a reusable URL set meant curling the API by hand.
//
// ============================================================================
// THE ONE FACT THIS FILE EXISTS TO GET RIGHT:
//
//   `mode` IS A QUERY PARAM, NOT A BODY FIELD.
//
//   app_queue_templates.py:  mode = (request.args.get("mode") or "append").lower()
//
// It is read from request.args. A body of {"mode": "replace"} would typecheck,
// would be accepted, would return 200 {"ok": true} -- and would APPEND, because
// the backend never looks in the body. The operator would believe they had
// replaced a queue they had merely grown. That is a type-correct, meaning-wrong
// control: a slower way of lying. It goes on the query string, and
// useQueueTemplates.test.ts pins that it stays there.
// ============================================================================

/** A row from GET /api/queue_templates. NOTE: `urls` is deliberately ABSENT --
 *  list_all() omits it (it could be huge) and returns `url_count` instead. To
 *  edit a template you must GET the single template first. */
export interface QueueTemplateRow {
  id: number;
  name: string;
  origin_site_id: string;
  note: string;
  ts_created: number;
  ts_used: number | null;
  use_count: number;
  url_count: number;
}

/** GET /api/queue_templates/<tid> — the full record, urls included. */
export interface QueueTemplate extends QueueTemplateRow {
  urls: string[];
  priority_map: Record<string, number>;
  force_set: string[];
}

/** The only two modes the backend accepts. Anything else 400s
 *  ("unknown mode: X") -- so the UI must never offer a third. */
export const APPLY_MODES = ["append", "replace"] as const;
export type ApplyMode = (typeof APPLY_MODES)[number];

export function useQueueTemplates() {
  return useQuery<{ ok: boolean; templates: QueueTemplateRow[] }, Error>({
    queryKey: ["queue-templates", "list"],
    queryFn: ({ signal }) =>
      apiGet<{ ok: boolean; templates: QueueTemplateRow[] }>("/api/queue_templates", signal),
    retry: 0,
  });
}

/** Fetch one template in full. Disabled until a tid is actually selected --
 *  GET /api/queue_templates/null would 404 in ROUTING (the rule is
 *  <int:tid>), which is indistinguishable from "template not found". */
export function useQueueTemplate(tid: number | null) {
  return useQuery<{ ok: boolean; template: QueueTemplate }, Error>({
    queryKey: ["queue-templates", "one", tid],
    queryFn: ({ signal }) =>
      apiGet<{ ok: boolean; template: QueueTemplate }>(`/api/queue_templates/${tid}`, signal),
    enabled: tid !== null,
    retry: 0,
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => {
    qc.invalidateQueries({ queryKey: ["queue-templates"] });
  };
}

/** POST /api/queue_templates. `name` is REQUIRED (backend 400s on empty) and
 *  `urls` MUST be a list (400 "urls must be a list" otherwise). */
export function useCreateQueueTemplate() {
  const invalidate = useInvalidate();
  return useMutation<
    { ok: boolean; id?: number; error?: string },
    Error,
    { name: string; urls: string[]; origin_site_id?: string; note?: string }
  >({
    mutationFn: (body) =>
      apiPost<{ ok: boolean; id?: number; error?: string }>("/api/queue_templates", {
        name: body.name,
        urls: body.urls,
        origin_site_id: body.origin_site_id ?? "",
        note: body.note ?? "",
      }),
    onSuccess: invalidate,
  });
}

/** PUT /api/queue_templates/<tid>. Every field is optional and None means
 *  "leave unchanged" -- but a PUT with NO changed fields hits `if not sets:
 *  return False`, i.e. it answers {"ok": false} having done nothing. The panel
 *  must not fire one; a Save that is guaranteed to report failure is a dead
 *  control. */
export function useUpdateQueueTemplate() {
  const invalidate = useInvalidate();
  return useMutation<
    { ok: boolean },
    Error,
    { tid: number; name?: string; urls?: string[]; note?: string }
  >({
    mutationFn: ({ tid, ...fields }) =>
      apiPut<{ ok: boolean }>(`/api/queue_templates/${tid}`, fields),
    onSuccess: invalidate,
  });
}

export function useDeleteQueueTemplate() {
  const invalidate = useInvalidate();
  return useMutation<{ ok: boolean }, Error, number>({
    mutationFn: (tid) => apiDelete<{ ok: boolean }>(`/api/queue_templates/${tid}`),
    onSuccess: invalidate,
  });
}

/** POST /api/queue_templates/<tid>/apply/<sid>?mode=append|replace
 *
 *  `mode` goes on the QUERY STRING (request.args) -- see the header block.
 *  The body is empty because the endpoint reads NOTHING from it.
 *
 *  mode=replace is DESTRUCTIVE: the view calls queue_delete_site(sid) and
 *  clears runner.jobs/urls before importing. The caller must confirm first. */
export function useApplyQueueTemplate() {
  const qc = useQueryClient();
  return useMutation<
    { ok: boolean; added?: number; mode?: string; error?: string },
    Error,
    { tid: number; sid: string; mode: ApplyMode }
  >({
    mutationFn: ({ tid, sid, mode }) =>
      apiPost<{ ok: boolean; added?: number; mode?: string; error?: string }>(
        `/api/queue_templates/${tid}/apply/${sid}?mode=${encodeURIComponent(mode)}`,
        {},
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["queue-templates"] });
      qc.invalidateQueries({ queryKey: ["queue-v2"] });
      qc.invalidateQueries({ queryKey: ["sites-v2"] });
      qc.invalidateQueries({ queryKey: ["dashboard-v2"] });
    },
  });
}
