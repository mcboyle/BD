"""queue_templates API -- extracted from app.py (Phase 4, thin-core-shell).

Pure code MOTION: the /api/queue_templates views moved onto a Flask Blueprint.
Endpoint labels gain a "queue_templates." prefix; the (rule, methods, bare-name)
routing surface is byte-identical (test_route_map_invariant diffs empty).

Shared state (runners) is owned by app.py and reached
via _app_<name>() accessors (getattr, fresh per call -- same object by reference).
"""
from __future__ import annotations

import sys

from flask import Blueprint, jsonify, request

queue_templates_bp = Blueprint("queue_templates", __name__)

def _app_runners():
    """The live shared runners from app.py (fetched fresh per call, by reference)."""
    import importlib
    return getattr(importlib.import_module("bulk_downloader.app_state"), "runners")


# qt-validate (O1789 bughunt queue-templates-io M1 M5 L2 L5): every body field is type-checked before it reaches
# queue_templates / sqlite. A wrong type is a 400 naming the field -- never a 500, never a silent coercion
# ("https://x" stored as one URL per character). null = field absent.
# qti-queue-meta r3: ONE check for create and update. priority_map values are part of it (a non-string
# priority was stored and then silently ignored on apply).
_FIELD_TYPES = {"name": (str, "a string"), "origin_site_id": (str, "a string"), "note": (str, "a string"),
                "urls": (list, "a list of strings"), "force_set": (list, "a list of strings"),
                "priority_map": (dict, "an object of url -> priority strings")}


def _field_error(body):
    """The 400 message for the first mistyped field of a template body, else None."""
    for key, (kind, what) in _FIELD_TYPES.items():
        value = body.get(key)
        if value is None:
            continue
        if not isinstance(value, kind) or (kind is list and not all(isinstance(u, str) for u in value)):
            return f"{key} must be {what}"
        if kind is dict and not all(isinstance(v, str) for v in value.values()):
            return f"{key} must be {what}"
    return None


def _template_shape_error(t):
    """A stored template apply cannot use (written before validation existed), else None. Checked BEFORE
    mode=replace clears the queue, so a bad template changes nothing."""
    if not isinstance(t.get("priority_map"), dict):
        return "template priority_map is not an object; fix it with PUT first"
    fset = t.get("force_set")
    if not isinstance(fset, list) or not all(isinstance(u, str) for u in fset):
        return "template force_set is not a list of strings; fix it with PUT first"   # r2: [[]] / [{..}] too
    return None


def _masked(t):
    """queue-headers-redact r1: a template as a client sees it -- header names
    kept, every value masked, in its entries and in the entry-keyed maps."""
    from . import queue_templates as _qt
    out = dict(t)
    for key in ("urls", "priority_map", "force_set"):
        v = t.get(key)   # a map written before validation may be any shape
        if isinstance(v, dict):
            out[key] = {_qt.masked_entry(k): x for k, x in v.items()}
        elif isinstance(v, list):
            out[key] = [_qt.masked_entry(u) for u in v]
    return out


def _unmasked(body, held):
    """(urls, priority_map, force_set) of a POST/PUT body with each masked
    header value replaced by the ``held`` one or dropped; None stays None."""
    from . import queue_templates as _qt
    urls, pmap, fset = body.get("urls"), body.get("priority_map"), body.get("force_set")
    if isinstance(urls, list):
        urls = [_qt.unmasked_entry(u, held) for u in urls]
    if isinstance(pmap, dict):
        pmap = {_qt.unmasked_entry(k, held): v for k, v in pmap.items()}
    if isinstance(fset, list):
        fset = [_qt.unmasked_entry(u, held) for u in fset]
    return urls, pmap, fset


@queue_templates_bp.route("/api/queue_templates", methods=["GET", "POST"])
def api_queue_templates():
    from . import queue_templates as _qt
    if request.method == "GET":
        return jsonify({"ok": True, "templates": _qt.list_all()})
    body = request.json
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400
    err = _field_error(body)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name required"}), 400
    origin = body.get("origin_site_id") or ""
    # queue-headers-redact r1: a masked header value handed back holds nothing
    urls, pmap, fset = _unmasked(body, {})
    tid = _qt.create(
        name=name, origin_site_id=origin, urls=urls or [],
        priority_map=pmap or {},
        force_set=fset or [],
        note=body.get("note") or "")
    return jsonify({"ok": True, "id": tid})


@queue_templates_bp.route("/api/queue_templates/<int:tid>",
           methods=["GET", "PUT", "DELETE"])
def api_queue_template_one(tid):
    from . import queue_templates as _qt
    if request.method == "GET":
        t = _qt.get(tid)
        if not t: return jsonify({"ok": False,
                                  "error": "not found"}), 404
        return jsonify({"ok": True, "template": _masked(t)})
    if request.method == "DELETE":
        ok = _qt.delete(tid)
        return jsonify({"ok": ok}), (200 if ok else 404)
    # PUT
    body = request.json
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "body must be a JSON object"}), 400
    err = _field_error(body)
    if err:
        return jsonify({"ok": False, "error": err}), 400
    if all(body.get(k) is None for k in _FIELD_TYPES if k != "origin_site_id"):
        return jsonify({"ok": False, "error": "nothing to update: give name, urls, priority_map, force_set "
                                             "or note"}), 400
    stored = _qt.get(tid)
    if not stored:
        return jsonify({"ok": False, "error": "not found"}), 404
    # queue-headers-redact r1: a masked value handed back is the stored one
    urls, pmap, fset = _unmasked(body, _qt.held_header_values(stored["urls"]))
    ok = _qt.update(
        tid,
        name=body.get("name"),
        urls=urls,
        priority_map=pmap,
        force_set=fset,
        note=body.get("note"))
    return jsonify({"ok": ok})


@queue_templates_bp.route("/api/queue_templates/<int:tid>/apply/<sid>",
           methods=["POST"])
def api_queue_template_apply(tid, sid):
    """Import a template into a site's queue. Mode `append` (default)
    skips URLs already in the target queue; `replace` clears first."""
    runners = _app_runners()
    if sid not in runners: return jsonify({"error":"Not found"}), 404
    from . import queue_templates as _qt
    t = _qt.get(tid)
    if not t: return jsonify({"ok": False, "error": "not found"}), 404
    mode = (request.args.get("mode") or "append").lower()
    if mode not in ("append", "replace"):
        return jsonify({"ok": False, "error":
                        f"unknown mode: {mode}"}), 400
    shape = _template_shape_error(t)
    if shape:
        return jsonify({"ok": False, "error": shape}), 400
    # r2 (REFUTE cx172 F1): everything derived from the template is built BEFORE replace clears the queue.
    pmap = t.get("priority_map") or {}
    fset = set(t.get("force_set") or [])
    runner = runners[sid]
    if mode == "replace":
        with runner._lock:
            from .db import queue_delete_site
            queue_delete_site(sid)
            runner.jobs.clear()
            runner.urls.clear()
    # Insert URLs
    # A template entry may be "url<TAB>Header=value"; the job is keyed by the
    # URL alone (job_url), and so is the row's priority / force metadata.
    new_urls = [u for u in t["urls"] if _qt.job_url(u) not in runner.jobs]
    added = 0
    if new_urls:
        result = runner.load_urls(new_urls, folder_scan=False)
        added = result[0] if isinstance(result, tuple) else len(new_urls)
    # Apply priority + force flags from the template
    from .db import queue_upsert
    persist = []
    with runner._lock:
        # Append skips URLs already queued, so their metadata stays as it was.
        for entry in new_urls:
            url = _qt.job_url(entry)
            if url not in runner.jobs: continue
            fields = {}
            priority = pmap.get(entry, pmap.get(url))
            if priority and isinstance(priority, str):
                runner.jobs[url]["priority"] = priority
                fields["priority"] = priority
            if entry in fset or url in fset:
                runner.jobs[url]["force_download"] = True
                fields["force_download"] = 1
            if fields:
                persist.append((url, fields))
    # Persist outside the lock; a DB failure must not fail the apply.
    for url, fields in persist:
        try:
            queue_upsert(sid, url, **fields)
        except Exception as e:
            sys.stderr.write(f"  template apply metadata persist failed: {e}\n")
    _qt.record_use(tid)
    return jsonify({"ok": True, "added": added, "applied": tid,
                    "site_id": sid, "mode": mode})

def register_routes(app) -> int:
    app.register_blueprint(queue_templates_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("queue_templates."))

