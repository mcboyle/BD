// useGlobalByteBudget — F041 global cross-site daily byte cap (read + save).
// FULL /api/ literal: GET /api/global_config, POST /api/global_config
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost } from "@/lib/api-client";

interface GlobalByteBudgetConfig {
  global_daily_byte_budget?: number;
}

const KEY = ["global-byte-budget"];

export function useGlobalByteBudget() {
  const qc = useQueryClient();
  const query = useQuery<number>({
    queryKey: KEY,
    queryFn: async ({ signal }) => {
      const cfg = await apiGet<GlobalByteBudgetConfig>("/api/global_config", signal);
      return Number(cfg.global_daily_byte_budget) || 0;
    },
    refetchOnWindowFocus: false,
  });
  // The POST answers with the whole global config. Project it to the cap before
  // returning, so no unrelated field is retained in the mutation cache.
  const save = useMutation<number, Error, number>({
    mutationFn: async (bytes) => {
      const cfg = await apiPost<GlobalByteBudgetConfig>("/api/global_config", {
        global_daily_byte_budget: bytes,
      });
      return Number(cfg.global_daily_byte_budget ?? bytes) || 0;
    },
    onSuccess: (cap) => {
      qc.setQueryData(KEY, cap);
    },
  });
  return { query, save };
}
