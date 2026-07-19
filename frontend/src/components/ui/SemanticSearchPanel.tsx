import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  useSemanticReindex,
  useSemanticSearch,
  useSemanticStatus,
  type SemanticSearchResult,
} from "@/hooks/useSemanticSearch";

// v3.66.743 — GUI for the semantic CONTROL cluster (recall over prior
// captures + the template corpus).
//
// Derived from app_semantic_search.py + semantic_search.py:
//
//  * search/reindex run happily against an EMPTY index and return ok:true with
//    zero hits — indistinguishable from "no matches" unless `indexed` from
//    /api/semantic/status is on screen. The indexed readout IS the control's
//    meaning; this panel always renders it, and says "index is empty" out
//    loud rather than passing an unbuilt index off as a clean miss.
//  * reindex rebuilds from the live corpus — potentially expensive, not
//    destructive (no confirm), but single-fire: disabled while pending.
//  * empty query is a REAL 400 server-side; the panel never fires it.

export function SemanticSearchPanel() {
  const status = useSemanticStatus();
  const search = useSemanticSearch();
  const reindex = useSemanticReindex();

  const [query, setQuery] = useState("");
  const [result, setResult] = useState<SemanticSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const st = status.data;
  const indexed = st?.indexed ?? null;
  const emptyIndex = st?.ok === true && (st.indexed ?? 0) === 0;
  const hasQuery = query.trim().length > 0;

  const doSearch = () => {
    if (!hasQuery || search.isPending) return; // endpoint 400s on empty query
    setError(null);
    setResult(null);
    search.mutate(
      { query: query.trim(), k: 10 },
      {
        onSuccess: (r) => {
          if (!r.ok) setError(r.error ?? "semantic search failed");
          else setResult(r);
        },
        onError: (e) => setError(e.message),
      },
    );
  };

  const doReindex = () => {
    if (reindex.isPending) return; // single-fire
    setError(null);
    reindex.mutate(undefined, {
      onError: (e) => setError(e.message),
    });
  };

  return (
    <Card className="p-4 space-y-3">
      <div className="flex items-baseline justify-between">
        <h3 className="text-sm font-semibold">Semantic recall</h3>
        <span className="text-xs text-muted-foreground" role="status">
          {st?.ok === false
            ? `status unavailable (${st.error ?? "?"})`
            : indexed === null
              ? "…"
              : `${indexed} indexed`}
          {st?.ok && st.enabled === false ? " · disabled" : ""}
        </span>
      </div>

      {emptyIndex && (
        <p className="text-xs text-amber-600">
          The index is empty — a search here would return zero hits no matter
          what exists. Reindex first.
        </p>
      )}

      <div className="flex gap-2">
        <input
          type="text"
          className="flex-1 rounded border px-2 py-1 text-sm bg-background"
          placeholder="Semantic search…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Button size="sm" onClick={doSearch} disabled={!hasQuery || search.isPending}>
          Search
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={doReindex}
          disabled={reindex.isPending}
        >
          {reindex.isPending ? "Reindexing…" : "Reindex"}
        </Button>
      </div>

      {error && (
        <p className="text-xs text-destructive" role="alert">
          {error}
        </p>
      )}

      {result && (
        <ul className="text-xs list-disc pl-4">
          {(result.hits ?? []).length === 0 && (
            <li className="list-none text-muted-foreground">
              no matches{emptyIndex ? " (index is empty)" : ""}
            </li>
          )}
          {(result.hits ?? []).slice(0, 20).map((h, i) => (
            <li key={i}>
              <span className="break-all">
                {String(h.ref ?? h.kind ?? "")}{" "}
                {typeof h.score === "number" ? `(${h.score.toFixed(3)})` : ""}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
