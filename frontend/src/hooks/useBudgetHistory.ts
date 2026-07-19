// useBudgetHistory — 380 daily byte-budget usage history (wired v3.66.382).
// FULL /api/ literal: GET /api/daily_budget/history/${siteId}?days=N
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api-client";
import type { BudgetHistoryResponse } from "@/lib/api-types";

export function useBudgetHistory(siteId: string, days = 30) {
  return useQuery<BudgetHistoryResponse>({
    queryKey: ["budget-history", siteId, days],
    queryFn: ({ signal }) =>
      apiGet<BudgetHistoryResponse>(
        `/api/daily_budget/history/${siteId}?days=${days}`,
        signal,
      ),
    enabled: Boolean(siteId),
    refetchOnWindowFocus: false,
  });
}
