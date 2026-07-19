"""bulk_downloader.selector_library -- Cut 623 / C3: named-selector library.

A store of reusable, named selectors so a template can reference a shared
selector by name (``@lib:<name>``) instead of duplicating a brittle CSS/XPath
string across templates. Completes the DOM-authoring loop's *reuse* half.

Storage: ``selector_library.json`` next to ``user_templates.json``. Shape::

    { "<name>": {"selector": "button.download", "description": "...",
                 "tags": [...], "created_ts": <epoch>} }

Reference expansion (``resolve_ref`` / ``expand_in_selectors``) is **pass-through
for any value that is not a live reference** -- a plain selector, or a
``@lib:<name>`` whose name is unknown, is returned unchanged (never blanked).
That property is what lets the expander be wired into the selector-materialize
path with zero behaviour change for every template that uses no references.
All read/resolve paths never raise.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

LIBRARY_FILE = "selector_library.json"
_REF_PREFIX = "@lib:"
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


def _store_path(base_dir: str | os.PathLike | None = None) -> str:
    base = str(base_dir) if base_dir else "."
    return os.path.join(base, LIBRARY_FILE)


def _load(base_dir=None) -> dict:
    try:
        with open(_store_path(base_dir), "r") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(doc: dict, base_dir=None) -> bool:
    path = _store_path(base_dir)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


# ── CRUD ────────────────────────────────────────────────────────────────

def add_named(name: str, selector: str, *, description: str = "",
              tags=None, base_dir: str | os.PathLike | None = None) -> tuple[bool, str]:
    """Store (or overwrite) a named selector. Rejects an empty/whitespace or
    malformed name and an empty selector. Returns (ok, message)."""
    name = (name or "").strip()
    selector = (selector or "").strip()
    if not _NAME_RE.match(name):
        return False, "name must be 1-64 chars of [A-Za-z0-9_.-] with no spaces"
    if not selector:
        return False, "selector must be non-empty"
    doc = _load(base_dir)
    doc[name] = {
        "selector": selector,
        "description": (description or "").strip(),
        "tags": list(tags or []),
        "created_ts": int(time.time()),
    }
    if not _save(doc, base_dir):
        return False, "could not write selector library"
    return True, "saved"


def get_named(name: str, base_dir: str | os.PathLike | None = None) -> dict | None:
    """Return the entry (with a ``name`` field folded in) or ``None``."""
    e = _load(base_dir).get((name or "").strip())
    if not isinstance(e, dict):
        return None
    out = dict(e)
    out["name"] = (name or "").strip()
    return out


def list_named(base_dir: str | os.PathLike | None = None) -> list[dict]:
    """All named selectors, each with its ``name`` folded in. Never raises."""
    doc = _load(base_dir)
    out = []
    for name, e in doc.items():
        if isinstance(e, dict):
            row = dict(e)
            row["name"] = name
            out.append(row)
    return sorted(out, key=lambda r: r.get("name", ""))


def remove_named(name: str, base_dir: str | os.PathLike | None = None) -> bool:
    doc = _load(base_dir)
    if (name or "").strip() in doc:
        del doc[(name or "").strip()]
        return _save(doc, base_dir)
    return False


# ── reference resolution / expansion (pass-through-safe) ────────────────

def resolve_ref(value, base_dir: str | os.PathLike | None = None):
    """If ``value`` is a live ``@lib:<name>`` reference, return the named
    selector; otherwise return ``value`` UNCHANGED (a plain selector, a
    non-string, or an unknown reference all pass through). Never raises."""
    if not isinstance(value, str) or not value.startswith(_REF_PREFIX):
        return value
    name = value[len(_REF_PREFIX):].strip()
    e = _load(base_dir).get(name)
    if isinstance(e, dict) and e.get("selector"):
        return e["selector"]
    return value  # unknown ref -> unchanged (never blank a selector)


def expand_in_selectors(selectors, base_dir: str | os.PathLike | None = None):
    """Return a deep copy of ``selectors`` with every ``@lib:<name>`` leaf
    expanded. Non-reference leaves are untouched, so this is byte-identical for
    any selector tree that uses no references. Input is not mutated. Never
    raises (falls back to the original object on any error)."""
    try:
        if isinstance(selectors, dict):
            return {k: expand_in_selectors(v, base_dir) for k, v in selectors.items()}
        if isinstance(selectors, list):
            return [expand_in_selectors(v, base_dir) for v in selectors]
        return resolve_ref(selectors, base_dir)
    except Exception:
        return selectors
