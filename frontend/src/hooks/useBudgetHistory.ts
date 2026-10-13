// useBudgetHistory — 380 daily byte-budget usage history (wired v3.66.382).
// FULL /api/ literal: GET /api/daily_budget/history?site=${siteId}&days=N
// The id rides the query string, not the path: browsers normalize "..", "/../x" and the
// server decodes %2F before routing, so a path segment cannot carry every id (stb-fix F6).
import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api-client";
import type { BudgetHistoryResponse } from "@/lib/api-types";

export function useBudgetHistory(siteId: string, days = 30) {
  return useQuery<BudgetHistoryResponse>({
    queryKey: ["budget-history", siteId, days],
    queryFn: ({ signal }) =>
      apiGet<BudgetHistoryResponse>(
        `/api/daily_budget/history?site=${encodeURIComponent(siteId)}&days=${days}`,
        signal,
      ),
    enabled: Boolean(siteId),
    refetchOnWindowFocus: false,
  });
}
