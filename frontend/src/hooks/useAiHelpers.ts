import { useMutation } from "@tanstack/react-query";

import { apiPost } from "@/lib/api-client";

// v3.66.752 — the dark ai CONTROL cluster (2 pure helpers + 1 site action).
//
//   POST /api/ai/classify             {element_desc} -> {ok, role,
//        confidence, reasoning, provider, latency_ms}. role is the model's
//        pick from the teach vocabulary (user_field | pass_field |
//        submit_btn | row_selectors | trigger_selectors | ignore) —
//        clamped server-side, unknown collapses to "ignore".
//   POST /api/ai/normalize_resolution {filename} -> {ok, resolution, label,
//        width, height, confidence, via}. THE `via` FIELD IS LOAD-BEARING:
//        ok:true with resolution:null is a real answer ("no-match" /
//        "empty"), not a blank success — the UI must say which. regex hits
//        never touch the model; "ai" means the LLM fallback ran.
//   POST /api/sites/<sid>/ai_reanalyze {url} -> suggest_selectors shape
//        ({ok, suggestions:[{selector, role, reasoning, confidence}]}) +
//        {had_screenshot, tried_count, event_count}. `url` MUST be a job
//        already in the site's queue (404 "url not in queue") — the
//        control's meaning is "re-analyze THIS queued/needs-review job",
//        which is why it mounts on a NeedsReview row, not a free-text form.
//
// Both helpers answer HTTP 200 {ok:false, error:"AI assist is disabled"}
// when AI is off — callers gate on the already-wired /api/ai/status
// (useAiStatus) and disable, rather than firing into a disabled backend.

export interface AiClassifyResult {
  ok: boolean;
  error?: string;
  role?: string;
  confidence?: number;
  reasoning?: string;
  provider?: string;
  latency_ms?: number;
}

export interface AiNormalizeResult {
  ok: boolean;
  error?: string;
  resolution?: string | null;
  label?: string | null;
  width?: number;
  height?: number;
  confidence?: number;
  via?: string;
  provider?: string;
  latency_ms?: number;
}

export interface AiSuggestion {
  selector?: string;
  role?: string;
  reasoning?: string;
  confidence?: number;
  [k: string]: unknown;
}

export interface AiReanalyzeResult {
  ok: boolean;
  error?: string;
  suggestions?: AiSuggestion[];
  had_screenshot?: boolean;
  tried_count?: number;
  event_count?: number;
  [k: string]: unknown;
}

export function useAiClassify() {
  return useMutation<AiClassifyResult, Error, string>({
    mutationFn: (elementDesc) =>
      apiPost<AiClassifyResult>("/api/ai/classify", {
        element_desc: elementDesc,
      }),
  });
}

export function useAiNormalizeResolution() {
  return useMutation<AiNormalizeResult, Error, string>({
    mutationFn: (filename) =>
      apiPost<AiNormalizeResult>("/api/ai/normalize_resolution", {
        filename,
      }),
  });
}

export function useAiReanalyze() {
  return useMutation<AiReanalyzeResult, Error, { sid: string; url: string }>({
    mutationFn: ({ sid, url }) =>
      apiPost<AiReanalyzeResult>(
        `/api/sites/${encodeURIComponent(sid)}/ai_reanalyze`,
        { url },
      ),
  });
}
