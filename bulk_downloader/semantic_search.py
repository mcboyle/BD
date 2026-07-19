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

from . import embeddings as _emb
from . import vector_index as _vi

_DEFAULT_DIMS = 256


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
        hits = _vi.search(qv, k=k, base_dir=base_dir)
        results = []
        for h in hits:
            meta = h.get("meta") or {}
            row = {"kind": meta.get("kind", ""), "id": meta.get("id", h.get("id")),
                   "score": float(h.get("score", 0.0)),
                   "summary": meta.get("summary", "")}
            if meta.get("host"):
                row["host"] = meta["host"]
            results.append(row)
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
