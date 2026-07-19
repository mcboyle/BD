// useTemplateAuthoring — T10 (v3.66.211) template-authoring wiring.
//
// FULL /api/ literals for the 4 template families T10 ports into the
// existing /templates route (TemplateManager). Handler-correct shapes
// re-derived from bulk_downloader/app.py at 210:
//
//   GET  /api/templates?url=         {ok,templates:[{id,name,...,suggested}]}
//   POST /api/template/extract       body {html,page_url?,site_hint_name?}
//                                     → {ok,template,candidates,warnings,stats}
//                                     Pure rule-based extraction, no side
//                                     effect → ungated compute.
//   POST /api/template/refine        body {html,template,candidates}
//                                     → refined draft (AI assist required).
//                                     No persistent side effect → ungated.
//   POST /api/template/sandbox       body {url,template,mode?,wait_ms?}
//                                     → {ok,mode,url,html_bytes,matches}.
//                                     CSRF. Fetches a LIVE url (http or a
//                                     browser launch) → B-tier confirm.

import { useMutation, useQuery } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  TemplateExtractBody,
  TemplateExtractResult,
  TemplateRefineBody,
  TemplateSandboxBody,
  TemplateSandboxResult,
  TemplatesListResponse,
} from "@/lib/api-types";

export function useTemplateLibrary(url?: string) {
  return useQuery<TemplatesListResponse, Error>({
    queryKey: ["templates", "list", url || ""],
    queryFn: ({ signal }) =>
      apiGet<TemplatesListResponse>(
        url ? `/api/templates?url=${encodeURIComponent(url)}` : "/api/templates",
        signal,
      ),
  });
}

export function useTemplateExtract() {
  return useMutation<TemplateExtractResult, Error, TemplateExtractBody>({
    mutationFn: (body) => apiPost<TemplateExtractResult>("/api/template/extract", body),
  });
}

export function useTemplateRefine() {
  return useMutation<TemplateExtractResult, Error, TemplateRefineBody>({
    mutationFn: (body) => apiPost<TemplateExtractResult>("/api/template/refine", body),
  });
}

/** Live sandbox fetch — B-tier confirm at the page. */
export function useTemplateSandbox() {
  return useMutation<TemplateSandboxResult, Error, TemplateSandboxBody>({
    mutationFn: (body) => apiPost<TemplateSandboxResult>("/api/template/sandbox", body),
  });
}
