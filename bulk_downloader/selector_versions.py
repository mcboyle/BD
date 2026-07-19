"""bulk_downloader.selector_versions -- Cut 623 / C3: selector version history.

Completes the DOM-authoring loop's *history* half. The scoring
(``selector_check``) and repair (``aiassist.diff_repair``) engines already exist;
this records a template's selector block (its ``learned`` dict) each time the
template is saved, so an operator can SEE what changed over time and REVERT to a
prior state.

Storage: an append-only ``selector_history.json`` sitting next to
``user_templates.json`` (same bare-relative resolution). Shape::

    {
      "<template_id>": [
        {"version": "<id>", "ts": <epoch>, "source": "save",
         "note": "", "selectors": { ...the learned block... }},
        ...
      ]
    }

Contract:
- ``record_template_version`` is **append-only** and **fail-open**: it never
  raises and never mutates the template; a no-op save (selectors byte-identical
  to the latest recorded version) does NOT create a duplicate. It is called
  best-effort from ``user_templates.save_user_template`` so history populates
  automatically without changing save behaviour.
- All read paths (``list_versions`` / ``get_version`` / ``diff_versions`` /
  ``revert_selectors``) never raise, and revert is **fail-closed** (returns
  ``None`` for an unknown template/version rather than a wrong or empty state).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HISTORY_FILE = "selector_history.json"
_MAX_PER_TEMPLATE = 50  # keep the newest N versions per template; trim older


def _store_path(base_dir: str | os.PathLike | None = None) -> str:
    base = str(base_dir) if base_dir else "."
    return os.path.join(base, HISTORY_FILE)


def _load(base_dir=None) -> dict:
    try:
        with open(_store_path(base_dir), "r") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(doc: dict, base_dir=None) -> bool:
    """Atomic-ish write (tmp + replace). Fail-open: returns False on error."""
    path = _store_path(base_dir)
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(doc, fh, indent=1)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _selectors_of(template: dict) -> dict:
    """The selector block we version == the template's ``learned`` dict (the
    download/login/quality selectors). Falls back to a ``selectors`` block if a
    template carries the runtime shape instead."""
    if not isinstance(template, dict):
        return {}
    learned = template.get("learned")
    if isinstance(learned, dict) and learned:
        return learned
    sel = template.get("selectors")
    return sel if isinstance(sel, dict) else {}


def _version_id(ts: int, selectors: dict) -> str:
    """Stable-ish short id: timestamp + a short content hash so two versions in
    the same second are still distinguishable."""
    import hashlib
    h = hashlib.sha256(json.dumps(selectors, sort_keys=True).encode("utf-8")).hexdigest()[:8]
    return f"{ts}-{h}"


def record_template_version(template: dict, *, source: str = "save",
                            note: str = "",
                            base_dir: str | os.PathLike | None = None) -> str | None:
    """Append a selector snapshot for ``template``. Append-only, fail-open,
    de-duplicated against the latest recorded version. Returns the new version id
    (or the existing id on a no-op), or ``None`` on any failure -- callers treat
    this as best-effort and never let it break a save."""
    try:
        tid = (template or {}).get("id")
        if not tid:
            return None
        selectors = _selectors_of(template)
        doc = _load(base_dir)
        hist = doc.get(str(tid))
        if not isinstance(hist, list):
            hist = []
        # de-dupe: identical to the latest snapshot -> no new version
        if hist:
            latest = hist[-1]
            if isinstance(latest, dict) and latest.get("selectors") == selectors:
                return latest.get("version")
        ts = int(time.time())
        vid = _version_id(ts, selectors)
        hist.append({"version": vid, "ts": ts, "source": source,
                     "note": note or "", "selectors": selectors})
        if len(hist) > _MAX_PER_TEMPLATE:
            hist = hist[-_MAX_PER_TEMPLATE:]
        doc[str(tid)] = hist
        _save(doc, base_dir)
        return vid
    except Exception:
        return None


def list_versions(template_id: str,
                  base_dir: str | os.PathLike | None = None) -> list[dict]:
    """Version metadata (no selector bodies), NEWEST FIRST. Never raises."""
    hist = _load(base_dir).get(str(template_id))
    if not isinstance(hist, list):
        return []
    out = [{"version": h.get("version"), "ts": h.get("ts"),
            "source": h.get("source"), "note": h.get("note", "")}
           for h in hist if isinstance(h, dict)]
    return list(reversed(out))


def get_version(template_id: str, version: str,
                base_dir: str | os.PathLike | None = None) -> dict | None:
    """The full selector snapshot for one version, or ``None``. Never raises."""
    hist = _load(base_dir).get(str(template_id))
    if not isinstance(hist, list):
        return None
    for h in hist:
        if isinstance(h, dict) and h.get("version") == version:
            sel = h.get("selectors")
            return sel if isinstance(sel, dict) else {}
    return None


def _flatten(prefix: str, obj) -> dict:
    """Flatten a nested selector dict to dotted leaf paths for diffing."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(f"{prefix}.{k}" if prefix else str(k), v))
    else:
        out[prefix] = obj
    return out


def diff_versions(template_id: str, version_a: str, version_b: str,
                  base_dir: str | os.PathLike | None = None) -> dict:
    """Structured diff a->b: ``{added, removed, changed}`` keyed by dotted leaf
    path. ``changed`` maps path -> {"from": old, "to": new}. Never raises; a
    missing version is treated as an empty snapshot."""
    a = _flatten("", get_version(template_id, version_a, base_dir) or {})
    b = _flatten("", get_version(template_id, version_b, base_dir) or {})
    added = {k: b[k] for k in b if k not in a}
    removed = {k: a[k] for k in a if k not in b}
    changed = {k: {"from": a[k], "to": b[k]} for k in a if k in b and a[k] != b[k]}
    return {"added": added, "removed": removed, "changed": changed}


def revert_selectors(template_id: str, version: str,
                     base_dir: str | os.PathLike | None = None) -> dict | None:
    """Return the selector block to restore for ``version`` -- the caller
    re-saves it via ``user_templates.save_user_template``. This module never
    writes to the template store itself (single-writer discipline). Fail-closed:
    returns ``None`` for an unknown template/version. Never raises."""
    return get_version(template_id, version, base_dir)


# ── F8 / PIT: set-wide restore-to-known-good-date ────────────────────
#
# revert_selectors restores ONE template to ONE version. This is the
# SET-WIDE analog: given a cutoff timestamp, for EVERY template pick the
# latest version recorded at or before it. Pure/read-only and never-raises
# (module posture); this computes the plan only, it does not write. The
# apply half lives in user_templates.restore_template_set (single writer).

def plan_set_restore(as_of_ts,
                     base_dir: str | os.PathLike | None = None) -> dict:
    """Set-wide point-in-time restore PLAN. For every template in the
    history, choose the latest version recorded at or before ``as_of_ts``
    (epoch seconds). Returns::

        {"as_of": <int>,
         "restore": [{"template_id","version","ts","selectors"} ...],
         "skipped": [{"template_id","reason"} ...]}

    A template whose earliest version is *after* the cutoff (it did not
    exist yet) goes to ``skipped``. ``restore`` is sorted by template_id
    for determinism. Read-only; never raises."""
    try:
        cutoff = int(as_of_ts)
    except (TypeError, ValueError):
        cutoff = 0
    restore, skipped = [], []
    try:
        doc = _load(base_dir)
    except Exception:
        doc = {}
    for tid in sorted(doc.keys()):
        hist = doc.get(tid)
        if not isinstance(hist, list) or not hist:
            continue
        chosen = None
        for h in hist:                       # append-only chronological
            if not isinstance(h, dict):
                continue
            ts = h.get("ts")
            if isinstance(ts, int) and ts <= cutoff:
                if chosen is None or ts >= chosen.get("ts", -1):
                    chosen = h
        if chosen is None:
            skipped.append({"template_id": tid,
                            "reason": "no version at or before cutoff"})
            continue
        sel = chosen.get("selectors")
        restore.append({"template_id": tid,
                        "version": chosen.get("version"),
                        "ts": chosen.get("ts"),
                        "selectors": sel if isinstance(sel, dict) else {}})
    return {"as_of": cutoff, "restore": restore, "skipped": skipped}


def restore_set_to_date(as_of_ts,
                        base_dir: str | os.PathLike | None = None) -> dict:
    """Convenience view of :func:`plan_set_restore`: ``{template_id:
    selectors}`` for every template restorable at/before ``as_of_ts``.
    Read-only."""
    plan = plan_set_restore(as_of_ts, base_dir)
    return {r["template_id"]: r["selectors"] for r in plan["restore"]}
