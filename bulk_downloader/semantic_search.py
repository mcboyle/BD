"""bulk_downloader.semantic_search -- Cut 624 / C2: RAG over captures + templates.

The search layer that ties ``embeddings`` + ``vector_index`` to BD's own corpus:
the Phase-1 capture metadata index (``db.db_captures_all``) and the user-template
corpus (``user_templates``). ``reindex`` embeds a short descriptive text per item
and stores it; ``search`` embeds an NL query and returns the best-matching prior
capture or template. The answer to "which prior capture / template talks about
X?" without the operator remembering exact filenames.

Privacy: only capture *metadata* is indexed (host / name / dir / kind) -- never
capture bodies -- so the index carries nothing a redacted capture wouldn't.

Config lives in the ``semantic_search`` block of ``app_config.json`` (read
directly; NO settings-center field is added, so the editable-field count pins are
untouched). Defaults: enabled, ``dims=256``.

``reindex`` accepts injected ``captures`` / ``templates`` lists (defaulting to the
live pulls) so the engine is unit-testable without the live DB path.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import embeddings as _emb
from . import vector_index as _vi

_DEFAULT_DIMS = 256
_RERANK_TIMEOUT_S = 0.15
_RERANK_CANDIDATE_LIMIT = 50


def _load_cfg(base_dir: str | os.PathLike | None = None) -> dict:
    base = str(base_dir) if base_dir else "."
    raw: dict = {}
    try:
        with open(os.path.join(base, "app_config.json"), "r") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("semantic_search"), dict):
            raw = doc["semantic_search"]
    except (OSError, ValueError):
        raw = {}
    dims = raw.get("dims")
    if not isinstance(dims, int) or dims <= 0:
        dims = _DEFAULT_DIMS
    return {"enabled": bool(raw.get("enabled", True)), "dims": dims}


# ── indexable text per item (metadata only) ─────────────────────────────

def _capture_text(row: dict) -> str:
    parts = [row.get("host", ""), row.get("name", ""), row.get("dir", ""),
             row.get("kind", "")]
    return " ".join(str(p) for p in parts if p)


def _capture_summary(row: dict) -> str:
    host = row.get("host") or "?"
    return f"{row.get('name', row.get('rel_path', '?'))} ({host}, {row.get('kind', '')})".strip()


def _template_text(t: dict) -> str:
    parts = [t.get("name", ""), t.get("description", "")]
    pats = t.get("patterns")
    if isinstance(pats, (list, tuple)):
        parts.extend(str(p) for p in pats)
    return " ".join(str(p) for p in parts if p)


def _template_summary(t: dict) -> str:
    return f"{t.get('name', t.get('id', '?'))} - {t.get('description', '')}".strip(" -")


# ── live pulls (defaults; injectable for tests) ─────────────────────────

def _live_captures() -> list:
    try:
        from . import db
        return db.db_captures_all()
    except Exception:
        return []


def _live_templates() -> list:
    try:
        from . import user_templates
        return user_templates.list_user_templates()
    except Exception:
        return []


def _rerank_endpoint() -> str | None:
    """Return the configured local reranker URL, never an external endpoint."""
    raw = os.environ.get("RERANKER_ENDPOINT", "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}:
        return None
    return f"{raw}/rerank"


def _rerank(query: str, results: list[dict]) -> list[dict] | None:
    endpoint = _rerank_endpoint()
    if not endpoint or not results:
        return None
    try:
        payload = json.dumps({"query": query, "documents": [row["summary"] for row in results]}).encode("utf-8")
        request = Request(endpoint, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=_RERANK_TIMEOUT_S) as response:
            body = json.loads(response.read().decode("utf-8"))
        scores = body.get("results") if isinstance(body, dict) else None
        if not isinstance(scores, list) or len(scores) != len(results):
            return None
        ordered = []
        for item in scores:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                return None
            index = item["index"]
            if index < 0 or index >= len(results) or not isinstance(item.get("score"), (int, float)):
                return None
            row = dict(results[index])
            row["score"] = float(item["score"])
            ordered.append(row)
        if len({item["index"] for item in scores}) != len(results):
            return None
        return sorted(ordered, key=lambda row: row["score"], reverse=True)
    except (OSError, ValueError, UnicodeDecodeError):
        return None


# ── reindex / search / status ───────────────────────────────────────────

def reindex(captures=None, templates=None,
            base_dir: str | os.PathLike | None = None) -> dict:
    """(Re)build the vector index from captures + templates. Pass explicit lists
    or let it pull from the live DB / template store. Returns ``{ok, indexed}``.
    Fail-open: on any error returns ``{ok: False, indexed: 0, error: ...}``
    without raising."""
    try:
        cfg = _load_cfg(base_dir)
        dims = cfg["dims"]
        caps = _live_captures() if captures is None else captures
        tmpls = _live_templates() if templates is None else templates
        rows = []
        for c in caps or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("rel_path") or c.get("name")
            if not cid:
                continue
            vec = _emb.embed(_capture_text(c), dims=dims)
            rows.append((f"capture:{cid}", vec,
                         {"kind": "capture", "id": cid, "summary": _capture_summary(c),
                          "host": c.get("host", "")}))
        for t in tmpls or []:
            if not isinstance(t, dict):
                continue
            tid = t.get("id") or t.get("name")
            if not tid:
                continue
            vec = _emb.embed(_template_text(t), dims=dims)
            rows.append((f"template:{tid}", vec,
                         {"kind": "template", "id": tid, "summary": _template_summary(t)}))
        _vi.clear(base_dir=base_dir)
        n = _vi.upsert_many(rows, base_dir=base_dir)
        return {"ok": True, "indexed": n}
    except Exception as e:
        return {"ok": False, "indexed": 0, "error": str(e)[:200]}


def search(query: str, k: int = 10,
           base_dir: str | os.PathLike | None = None) -> dict:
    """Return the best-matching prior captures/templates for an NL ``query`` as
    ``{ok, query, results:[{kind, id, score, summary, ...}]}``. Never raises; an
    empty index yields an empty result list."""
    try:
        cfg = _load_cfg(base_dir)
        q = (query or "").strip()
        if not q:
            return {"ok": True, "query": "", "results": []}
        qv = _emb.embed(q, dims=cfg["dims"])
        hits = _vi.search(qv, k=_RERANK_CANDIDATE_LIMIT, base_dir=base_dir)
        results = []
        for h in hits:
            meta = h.get("meta") or {}
            row = {"kind": meta.get("kind", ""), "id": meta.get("id", h.get("id")),
                   "score": float(h.get("score", 0.0)),
                   "summary": meta.get("summary", "")}
            if meta.get("host"):
                row["host"] = meta["host"]
            results.append(row)
        reranked = _rerank(q, results)
        if reranked is not None:
            results = reranked
        results = results[:k]
        return {"ok": True, "query": q, "results": results}
    except Exception as e:
        return {"ok": False, "query": query, "results": [], "error": str(e)[:200]}


def status(base_dir: str | os.PathLike | None = None) -> dict:
    """A read-only snapshot: enabled flag, indexed count, dims, and whether the
    optional sqlite-vec speed path is present. Never raises."""
    cfg = _load_cfg(base_dir)
    try:
        n = _vi.size(base_dir=base_dir)
    except Exception:
        n = 0
    return {"enabled": cfg["enabled"], "indexed": n, "dims": cfg["dims"],
            "sqlite_vec": _vi.sqlite_vec_available()}
