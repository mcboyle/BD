"""bulk_downloader.vector_index -- Cut 624 / C2: brute-force cosine vector store.

A single-file (``vector_index.json``) store of ``id -> (vector, meta)`` with
top-k cosine search. Brute force over the whole set is the shipping default: for
a single-operator corpus (hundreds to a few thousand captures + templates) a
linear scan is well under a millisecond and needs no native extension, so the
feature works on any box.

``sqlite-vec`` (a single-file SQLite ANN extension) is **detected** via
``sqlite_vec_available`` and reserved as a future speed path for a very large
corpus; it is never required. Vectors are expected pre-normalized by
``embeddings.embed`` (L2 norm 1), so cosine reduces to a dot product, but
``search`` normalizes defensively regardless.

All read paths never raise on a missing/empty store. base_dir params keep tests
hermetic.
"""
from __future__ import annotations

import json
import math
import os

INDEX_FILE = "vector_index.json"


def _store_path(base_dir: str | os.PathLike | None = None) -> str:
    base = str(base_dir) if base_dir else "."
    return os.path.join(base, INDEX_FILE)


def _load(base_dir=None) -> dict:
    try:
        with open(_store_path(base_dir), "r") as fh:
            doc = json.load(fh)
        items = doc.get("items") if isinstance(doc, dict) else None
        return items if isinstance(items, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(items: dict, base_dir=None) -> bool:
    path = _store_path(base_dir)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"items": items}, fh)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def upsert(item_id: str, vector, meta=None,
           base_dir: str | os.PathLike | None = None) -> bool:
    items = _load(base_dir)
    items[str(item_id)] = {"vector": list(vector), "meta": meta or {}}
    return _save(items, base_dir)


def upsert_many(rows, base_dir: str | os.PathLike | None = None) -> int:
    """Bulk upsert ``[(id, vector, meta), ...]`` in a single write. Returns the
    number stored."""
    items = _load(base_dir)
    n = 0
    for row in rows or []:
        try:
            item_id, vector, meta = row[0], row[1], (row[2] if len(row) > 2 else {})
        except (TypeError, IndexError):
            continue
        items[str(item_id)] = {"vector": list(vector), "meta": meta or {}}
        n += 1
    _save(items, base_dir)
    return n


def remove(item_id: str, base_dir: str | os.PathLike | None = None) -> bool:
    items = _load(base_dir)
    if str(item_id) in items:
        del items[str(item_id)]
        return _save(items, base_dir)
    return False


def clear(base_dir: str | os.PathLike | None = None) -> bool:
    return _save({}, base_dir)


def size(base_dir: str | os.PathLike | None = None) -> int:
    return len(_load(base_dir))


def _cosine(a, b) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def search(query_vector, k: int = 10,
           base_dir: str | os.PathLike | None = None) -> list[dict]:
    """Top-``k`` items by cosine to ``query_vector``, highest first. Returns
    ``[{"id", "score", "meta"}, ...]``; empty list on an empty store. Never
    raises."""
    try:
        items = _load(base_dir)
        scored = []
        for item_id, rec in items.items():
            if not isinstance(rec, dict):
                continue
            score = _cosine(query_vector, rec.get("vector") or [])
            scored.append({"id": item_id, "score": float(score),
                           "meta": rec.get("meta") or {}})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[: max(0, int(k))]
    except Exception:
        return []


def sqlite_vec_available() -> bool:
    """True iff the ``sqlite-vec`` extension can be loaded. Reserved as a future
    ANN speed path for large corpora -- never required by the brute-force store.
    Never raises."""
    try:
        import sqlite3
        con = sqlite3.connect(":memory:")
        try:
            con.enable_load_extension(True)
        except (AttributeError, sqlite3.OperationalError):
            return False
        try:
            con.load_extension("vec0")
            return True
        except sqlite3.OperationalError:
            return False
        finally:
            con.close()
    except Exception:
        return False
