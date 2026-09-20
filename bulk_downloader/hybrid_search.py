"""bulk_downloader.hybrid_search -- Row 878: Reciprocal Rank Fusion over BM25
keyword search + vector similarity search.

RRF combines two independently-ranked result lists using only rank position,
not raw scores: score(id) = sum over engines that returned it of 1/(k+rank),
rank 1-based. That is what lets it compose BM25 (unbounded, corpus-dependent
scores) with cosine similarity (bounded, differently-scaled) without having to
reconcile their scales -- pure keyword search misses conceptual synonyms, pure
vector search misses exact alphanumeric codes and filenames.

Both engines are passed in as callables (``keyword_fn``, ``vector_fn``), each
``(query, limit) -> list[dict]`` with a best-match-first ranking and an ``id``
key per row. The runtime engines do NOT have that shape themselves --
``db.db_search_fts(query, *, limit)`` takes a keyword-only limit and returns
bare history rows, ``semantic_search.search(query, k)`` returns
``{ok, results}`` with ``kind``-tagged rows -- so ``keyword_engine`` /
``vector_engine`` below adapt them (and tag every row with a ``kind`` so the
fusion identity ``kind/id`` is uniform across engines); ``hybrid_search``
runs RRF over the two adapters.

Fail-soft: an engine that raises, or returns something other than a list of
dicts, is treated as offline and contributes no ranks -- the fused result
still comes back from whichever engine is left (Row 878 acceptance
criterion 3). ``rrf_search`` itself never raises.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

_DEFAULT_K = 60
_EXACT_MATCH_BOOST = 1.0


def _safe_call(fn: Callable[[str, int], Iterable[dict]], query: str, limit: int) -> tuple[list[dict], bool]:
    try:
        results = fn(query, limit)
    except Exception:
        return [], False
    if not isinstance(results, list):
        return [], False
    # A malformed element (null, a dict without an id) means the engine is not
    # speaking the contract: the whole engine is OFFLINE for this query, not
    # "online with the bad rows quietly dropped" (E4).
    if any(not isinstance(r, dict) or r.get("id") is None for r in results):
        return [], False
    return list(results), True


def _identity(row: dict) -> str:
    """Fusion key ``kind/id``: the row's id namespaced by its ``kind``
    (semantic_search returns capture/<id> and template/<id> rows whose bare
    ids can collide -- they are different things, E3). A row without a kind
    gets the empty namespace, so the key shape is the same for every engine
    and the same item reported by both engines fuses into ONE entry only
    when both tag it alike -- the adapters below guarantee that."""
    kind = row.get("kind") or ""
    return f"{kind}/{row['id']}"


# ── runtime engine adapters ─────────────────────────────────────────────
KEYWORD_KIND = "history"   # what db_search_fts rows are: download history


def keyword_engine(query: str, limit: int, *, kind: str = KEYWORD_KIND) -> list[dict]:
    """BM25/FTS5 keyword engine over the download history, in the engine
    contract: ``(query, limit) -> [{id, kind, ...}]`` best-first."""
    from . import db  # lazy: the sqlite layer is heavy and test-patched
    rows = db.db_search_fts(query, limit=limit)
    out = []
    for row in rows or []:
        if isinstance(row, dict) and row.get("id") is not None:
            out.append({**row, "kind": row.get("kind") or kind})
    return out


def vector_engine(query: str, limit: int) -> list[dict]:
    """Vector-similarity engine over captures/templates, in the engine
    contract; semantic_search's ``{ok: False}`` envelope is an offline engine
    (raise -> _safe_call marks it offline)."""
    from . import semantic_search  # lazy
    res = semantic_search.search(query, k=limit)
    if not isinstance(res, dict) or not res.get("ok"):
        raise RuntimeError(f"semantic_search offline: {res!r}"[:200])
    return [r for r in (res.get("results") or []) if isinstance(r, dict)]


def hybrid_search(query: str, *, k: int = _DEFAULT_K, limit: int = 10) -> dict:
    """RRF over the two runtime engines (the production entry point)."""
    return rrf_search(query, keyword_engine, vector_engine, k=k, limit=limit)


def _is_exact_match(query: str, item_id) -> bool:
    """True when `query` names `item_id` verbatim -- an alphanumeric code or
    filename a human typed exactly. Case-insensitive; `-`/`_` are treated as
    the same separator so ``SKU-9981`` matches ``sku_9981``."""
    if not query or item_id is None:
        return False

    def _norm(s: str) -> str:
        return s.strip().lower().replace("_", "-")

    q = _norm(str(query))
    iid = _norm(str(item_id))
    if not iid:
        return False
    if q == iid:
        return True
    # The whole normalized code must appear in the phrase on token boundaries:
    # "SKU-9981 for the order" names sku_9981; "sku-99810" does not (E1).
    q_words = " ".join(q.replace("-", " ").split())
    code = " ".join(iid.replace("-", " ").split())
    return re.search(rf"(?<![a-z0-9]){re.escape(code)}(?![a-z0-9])", q_words) is not None


def rrf_search(query: str,
               keyword_fn: Callable[[str, int], Iterable[dict]],
               vector_fn: Callable[[str, int], Iterable[dict]],
               k: int = _DEFAULT_K, limit: int = 10) -> dict:
    """Merge ``keyword_fn(query, limit)`` and ``vector_fn(query, limit)`` via
    Reciprocal Rank Fusion. Returns
    ``{ok, query, results:[{id, score, sources, ...row}], engines:{keyword, vector}}``,
    highest score first. Never raises."""
    query = query or ""
    kw_rows, kw_ok = _safe_call(keyword_fn, query, limit)
    vec_rows, vec_ok = _safe_call(vector_fn, query, limit)

    fused: dict[str, dict] = {}

    def _accumulate(rows: list[dict], source: str) -> None:
        seen: set[str] = set()
        for rank, row in enumerate(rows, start=1):
            key = _identity(row)
            if key in seen:
                continue  # one vote per engine per identity: a repeat never gains rank (E2)
            seen.add(key)
            entry = fused.setdefault(
                key, {"id": row["id"], "score": 0.0, "sources": set(), "row": row}
            )
            entry["score"] += 1.0 / (k + rank)
            entry["sources"].add(source)

    _accumulate(kw_rows, "keyword")
    _accumulate(vec_rows, "vector")

    for entry in fused.values():
        if _is_exact_match(query, entry["id"]):
            entry["score"] += _EXACT_MATCH_BOOST

    ranked = sorted(fused.values(), key=lambda e: e["score"], reverse=True)
    results = []
    for entry in ranked[:limit]:
        row = dict(entry["row"])
        row["id"] = entry["id"]
        row["score"] = entry["score"]
        row["sources"] = sorted(entry["sources"])
        results.append(row)

    return {
        "ok": True,
        "query": query,
        "results": results,
        "engines": {"keyword": kw_ok, "vector": vec_ok},
    }
