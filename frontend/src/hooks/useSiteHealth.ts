import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api-client";
import type { SiteHealthV2 } from "@/lib/api-types";

// Fetches the per-site daily completion counts and exposes a lookup
// closure. Called once at the Activity route level so the visible
// rows share a single network call regardless of how many sites are
// represented.
//
// Cache: keyed by (days, status) so the Activity route can later
// expose a status-filter dropdown without invalidating unrelated
// queries. 5-minute fresh window; sparkline data drifts slowly.

export function useSiteHealth(
  days: number = 14,
  status: string = "*",
): {
  isLoading: boolean;
  isError: boolean;
  lookup: (siteId: string) => number[] | null;
  data: SiteHealthV2 | undefined;
} {
  const q = useQuery<SiteHealthV2>({
    queryKey: ["site-health-v2", days, status],
    queryFn: ({ signal }) =>
      apiGet<SiteHealthV2>(
        `/api/activity/v2/site_health?days=${days}&status=${encodeURIComponent(status)}`,
        signal,
      ),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
    retry: 0,
  });
  return {
    isLoading: q.isLoading,
    isError: q.isError,
    data: q.data,
    lookup: (siteId: string) => q.data?.by_site?.[siteId] ?? null,
  };
}
