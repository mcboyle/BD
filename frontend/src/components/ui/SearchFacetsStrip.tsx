import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/lib/api-client";

// v3.66.743 — the /api/search/facets consumer. History's FTS search shows a
// match count; this strip breaks it down by site and status so a big number
// is navigable. GET-ARGS family (query/site_id/status ride request.args),
// like its sibling /api/search — not the POST-body live-search family.

interface Facets {
  by_site: Record<string, number>;
  by_status: Record<string, number>;
  total: number;
}

interface FacetsResponse {
  ok: boolean;
  facets?: Facets;
}

export function SearchFacetsStrip({ q }: { q: string }) {
  const query = q.trim();
  const facets = useQuery<FacetsResponse>({
    queryKey: ["search", "facets", query],
    queryFn: ({ signal }) =>
      apiGet<FacetsResponse>(
        `/api/search/facets?query=${encodeURIComponent(query)}`,
        signal,
      ),
    enabled: query.length > 0,
    refetchOnWindowFocus: false,
  });

  if (!query || !facets.data?.ok || !facets.data.facets) return null;
  const f = facets.data.facets;
  const sites = Object.entries(f.by_site).sort((a, b) => b[1] - a[1]);
  const statuses = Object.entries(f.by_status).sort((a, b) => b[1] - a[1]);
  if (sites.length === 0 && statuses.length === 0) return null;

  return (
    <span className="text-xs text-ink-3 flex flex-wrap gap-2" role="status">
      {sites.slice(0, 6).map(([sid, n]) => (
        <span key={`s-${sid}`} className="rounded bg-muted px-1.5 py-0.5">
          {sid}: {n}
        </span>
      ))}
      {statuses.slice(0, 4).map(([st, n]) => (
        <span key={`t-${st}`} className="rounded bg-muted px-1.5 py-0.5">
          {st}: {n}
        </span>
      ))}
    </span>
  );
}
