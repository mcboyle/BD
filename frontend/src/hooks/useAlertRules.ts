// useAlertRules — 380 alert-rules editor + test-send (wired v3.66.382).
// FULL /api/ literals (scanner credit -> gui_parity spa_wired):
//   GET  /api/alerts/rules
//   POST /api/alerts/rules               body {id, metric, op, threshold, ...}
//   POST /api/alerts/rules/${id}/remove
//   POST /api/alerts/evaluate            (force an evaluation pass = test-send)
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";
import type {
  AlertRule,
  AlertRuleSaveResult,
  AlertRulesResponse,
} from "@/lib/api-types";

export function useAlertRules() {
  const qc = useQueryClient();
  const queryKey = ["alert-rules"];
  const invalidate = () => void qc.invalidateQueries({ queryKey });

  const query = useQuery<AlertRulesResponse>({
    queryKey,
    queryFn: ({ signal }) => apiGet<AlertRulesResponse>("/api/alerts/rules", signal),
    refetchOnWindowFocus: false,
  });

  const save = useMutation<AlertRuleSaveResult, Error, AlertRule>({
    mutationFn: (rule) => apiPost<AlertRuleSaveResult>("/api/alerts/rules", rule),
    onSuccess: invalidate,
  });

  const remove = useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => apiPost<{ ok: boolean }>(`/api/alerts/rules/${id}/remove`, {}),
    onSuccess: invalidate,
  });

  const testEvaluate = useMutation<unknown, Error, void>({
    mutationFn: () => apiPost<unknown>("/api/alerts/evaluate", {}),
  });

  return { query, save, remove, testEvaluate };
}
