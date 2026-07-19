// useMacrosOps — T10 (v3.66.211) macro get/save/replay wiring for the
// existing /pools-macros route (PoolsMacros). FULL /api/ literals; inline
// ${x} only on the true sid/name path params (normalises to the baseline's
// /api/macros/get/{x} and /api/macros/replay/{x}).
//
// Handler-correct shapes re-derived from bulk_downloader/app.py at 210:
//   GET  /api/macros/get/<sid>/<name>   macro dict | 404 {error}
//   POST /api/macros/save               {site_id,name,actions,description?,
//                                        tags?} → record result. CSRF. B-tier.
//   POST /api/macros/replay/<sid>/<name>  {start_url?,headless?,
//                                        persist_result?} → replay result|404.
//                                        CSRF. Opens a NESTED Playwright
//                                        context (INV-001): the page must warn
//                                        the operator to pause running workers
//                                        first → B-tier confirm carries that
//                                        warning.

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type { MacroRecord, MacroReplayBody, MacroReplayResult, MacroSaveBody } from "@/lib/api-types";

export function useMacroGet() {
  return useMutation<MacroRecord, Error, { sid: string; name: string }>({
    mutationFn: ({ sid, name }) =>
      apiGet<MacroRecord>(
        `/api/macros/get/${encodeURIComponent(sid)}/${encodeURIComponent(name)}`,
      ),
  });
}

export function useMacroSave() {
  const qc = useQueryClient();
  return useMutation<MacroRecord, Error, MacroSaveBody>({
    mutationFn: (body) => apiPost<MacroRecord>("/api/macros/save", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["macros", "list"] }),
  });
}

/** Nested Playwright replay (INV-001) — B-tier confirm warns to pause
 *  running workers first. */
export function useMacroReplay() {
  return useMutation<MacroReplayResult, Error, { sid: string; name: string; body?: MacroReplayBody }>({
    mutationFn: ({ sid, name, body }) =>
      apiPost<MacroReplayResult>(
        `/api/macros/replay/${encodeURIComponent(sid)}/${encodeURIComponent(name)}`,
        body || {},
      ),
  });
}
