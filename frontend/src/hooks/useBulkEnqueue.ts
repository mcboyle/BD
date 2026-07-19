// useBulkEnqueue — 380 batch URL enqueue (wired v3.66.382).
// FULL /api/ literal: POST /api/bulk/enqueue  body {site_id, urls:[...]}
import { useMutation } from "@tanstack/react-query";

import { apiPost } from "@/lib/api-client";
import type { BulkEnqueueRequest, BulkEnqueueResult } from "@/lib/api-types";

export function useBulkEnqueue() {
  return useMutation<BulkEnqueueResult, Error, BulkEnqueueRequest>({
    mutationFn: (req) => apiPost<BulkEnqueueResult>("/api/bulk/enqueue", req),
  });
}
