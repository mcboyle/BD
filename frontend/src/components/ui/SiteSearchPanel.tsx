import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  useSearchAll,
  useSearchSite,
  useSitesAvailable,
  type AllSearchResult,
  type SiteSearchResult,
} from "@/hooks/useSiteSearch";

// v3.66.743 — GUI for the search CONTROL cluster (live-site search).
//
// Derived from app_search.py:
//
//  * BOTH endpoints DEGRADE AT HTTP 200 ("search_extractor unavailable",
//    ok:false, status 200). The capability signal is the READ:
//    /api/search/sites_available carries the same guard. This panel reads it
//    FIRST and renders a disabled state — it never fires a doomed POST. That
//    read is the load-bearing control of the cluster.
//  * /api/search (GET, args) is HISTORY FTS — a different job, wired on
//    History.tsx. This panel never touches it (pinned by test).
//  * Query rides the POST BODY for both search/site and search/all;
//    search/all's optional `sites` must be a list.
//  * Search queues nothing. The UI acts on the operator's picks separately.

export function SiteSearchPanel() {
  const avail = useSitesAvailable();
  const searchAll = useSearchAll();
  const searchSite = useSearchSite();

  const [query, setQuery] = useState("");
  const [siteId, setSiteId] = useState("");
  const [allResult, setAllResult] = useState<AllSearchResult | null>(null);
  const [siteResult, setSiteResult] = useState<SiteSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const capability = avail.data;
  const unavailable = capability !== undefined && capability.ok === false;
  const sites = capability?.available ?? [];
  const hasQuery = query.trim().length > 0;
  const busy = searchAll.isPending || searchSite.isPending;

  const doSearchAll = () => {
    if (unavailable || !hasQuery || busy) return; // never post into the dark
    setError(null);
    setAllResult(null);
    searchAll.mutate(
      { query: query.trim() },
      {
        onSuccess: (r) => {
          if (!r.ok) setError(r.error ?? "search failed");
          else setAllResult(r);
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  const doSearchSite = () => {
    if (unavailable || !hasQuery || !siteId || busy) return;
    setError(null);
    setSiteResult(null);
    searchSite.mutate(
      { site_id: siteId, query: query.trim() },
      {
        onSuccess: (r) => {
          if (!r.ok) setError(r.error ?? "search failed");
          else setSiteResult(r);
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">Site search</h3>
        {capability?.ok && (
          <span className="text-xs text-muted-foreground">
            search {capability.count ?? sites.length} of your{" "}
            {capability.total_sites ?? "?"} sites
          </span>
        )}
      </div>

      {unavailable && (
        <p className="text-xs text-amber-600" role="status">
          Search extractor not installed — live-site search is unavailable on
          this box. ({capability?.error})
        </p>
      )}

      <div className="flex gap-2 items-center">
        <input
          type="text"
          className="flex-1 rounded border px-2 py-1 text-sm bg-background"
          placeholder="Search your sites…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={unavailable}
        />
        <label className="text-xs text-muted-foreground" htmlFor="site-search-site">
          Site
        </label>
        <select
          id="site-search-site"
          className="rounded border px-2 py-1 text-sm bg-background"
          value={siteId}
          onChange={(e) => setSiteId(e.target.value)}
          disabled={unavailable}
        >
          <option value="">(pick one)</option>
          {sites.map((s) => (
            <option key={s.site_id} value={s.site_id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      <div className="flex gap-2">
        <Button
          size="sm"
          onClick={doSearchAll}
          disabled={unavailable || !hasQuery || busy}
        >
          Search all sites
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={doSearchSite}
          disabled={unavailable || !hasQuery || !siteId || busy}
        >
          Search site
        </Button>
      </div>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {allResult && (
        <div className="space-y-2 text-sm">
          {Object.entries(allResult.results ?? {}).map(([sid, r]) => (
            <div key={sid}>
              <div className="text-xs font-medium">{sid}</div>
              {(r.hits ?? []).length === 0 ? (
                <div className="text-xs text-muted-foreground">no hits</div>
              ) : (
                <ul className="text-xs list-disc pl-4">
                  {(r.hits ?? []).slice(0, 20).map((h, i) => (
                    <li key={i}>
                      <span className="break-all">{String(h.title ?? h.url ?? "")}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ))}
          {Object.keys(allResult.results ?? {}).length === 0 && (
            <div className="text-xs text-muted-foreground">
              {allResult.note ?? "no searchable sites matched"}
            </div>
          )}
        </div>
      )}

      {siteResult && (
        <ul className="text-xs list-disc pl-4">
          {(siteResult.hits ?? []).length === 0 && (
            <li className="list-none text-muted-foreground">no hits</li>
          )}
          {(siteResult.hits ?? []).slice(0, 50).map((h, i) => (
            <li key={i}>
              <span className="break-all">{String(h.title ?? h.url ?? "")}</span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
