"""v3.43.9: User-defined template storage.

Templates the user saves after a successful teach session live in
``user_templates.json`` next to ``sites_config.json``. They have the
same structure as built-in templates (in ``templates.py``) but are
managed at runtime — added, edited, deleted, exported, imported.

When the picker auto-suggests a template for a URL, user templates
WIN over built-ins on regex match. The reasoning: if you've gone to
the trouble of teaching a site and saving the template, your saved
version is presumably better-fit than my generic built-in guess. The
built-ins remain available in the picker as fallbacks.

File format (user_templates.json):

    {
        "version": 1,
        "templates": [
            {
                "id": "user_my_site_2026_05_11",
                "name": "My favorite site",
                "description": "...",
                "patterns": ["r'mysite\\.com'", ...],
                "learned": {"download": {...}},
                "config_defaults": {...},
                "created_ts": 1762890000,
                "source": "user_teach"
            },
            ...
        ]
    }

Design choices:

  - Separate file (not under sites_config.json) so it's portable:
    you can copy user_templates.json between installs, share it with
    other users, or back it up without backing up your site configs.

  - IDs are namespaced with "user_" prefix so they can never collide
    with built-in template IDs (which are bare like "wowgirls_network").
    A user template whose name happens to match a built-in just lives
    alongside it.

  - The file is rewritten atomically on each change via a temp file +
    rename. Survives power loss / kill -9 mid-write.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

USER_TEMPLATES_FILE = Path("user_templates.json")
SCHEMA_VERSION = 1

# qti-user-tpl F1: every read-modify-write of the store holds this lock, so
# concurrent saves/deletes/imports in one process cannot drop each other.
_STORE_LOCK = threading.RLock()


class UserTemplatesStoreError(RuntimeError):
    """The store could not be read for a write, or the write failed. Raised
    instead of reporting success (qti-user-tpl F2/F3)."""


def _require_readable_store() -> None:
    """Writers call this before their read-modify-write: a present-but-
    unreadable store raises UserTemplatesStoreError instead of reading as
    empty, so the next save cannot overwrite the user's templates with a
    near-empty list (qti-user-tpl F2). _load() itself stays tolerant (and
    zero-arg) for readers."""
    if not USER_TEMPLATES_FILE.exists():
        return
    try:
        data = json.loads(USER_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        raise UserTemplatesStoreError(
            f"{USER_TEMPLATES_FILE.name} is unreadable ({e}); not overwritten "
            "-- fix or move the file, then retry") from e
    if not isinstance(data, dict) or not isinstance(data.get("templates") or [], list):
        raise UserTemplatesStoreError(
            f"{USER_TEMPLATES_FILE.name} has no templates list; not overwritten")


def _load_for_write() -> list[dict]:
    """_load() for a writer: refuses a damaged store first (F2)."""
    _require_readable_store()
    return _load()


def _load() -> list[dict]:
    """Read user_templates.json and return the templates list. Missing
    file is fine and returns an empty list. Malformed JSON logs a warning
    but doesn't crash — the user can recover by manually fixing the file
    or deleting it."""
    if not USER_TEMPLATES_FILE.exists():
        return []
    try:
        data = json.loads(USER_TEMPLATES_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        import sys
        sys.stderr.write(f"  user_templates.json malformed: {e}; treating as empty\n")
        return []
    if not isinstance(data, dict):
        return []
    templates = data.get("templates") or []
    if not isinstance(templates, list):
        return []
    # Defensive filter — drop any non-dict entries
    # BD-SEC-5 H1: config_defaults are cut to the safe allowlist on read too, so a
    # template stored before the import filter can never apply a command/path key.
    return [_safe_config(t) for t in templates if isinstance(t, dict) and "id" in t]


def _save(templates: list[dict]) -> None:
    """Write templates list atomically. Tmpfile + rename so a crash
    mid-write can't leave a corrupt file. The temp name is unique per write
    (F1: a fixed .json.tmp let two writers interleave), secret keys are
    stripped from every template written (F11), and a failed write raises
    UserTemplatesStoreError instead of passing as saved (F3)."""
    payload = {
        "version": SCHEMA_VERSION,
        "templates": [_strip_secrets(t) for t in templates],
    }
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(dir=str(USER_TEMPLATES_FILE.parent),
                                   prefix=f".{USER_TEMPLATES_FILE.name}.",
                                   suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2, ensure_ascii=False))
        Path(tmp).replace(USER_TEMPLATES_FILE)
    except Exception as e:
        import sys
        sys.stderr.write(f"  user_templates save failed: {e}\n")
        if tmp is not None and os.path.exists(tmp):
            try: os.unlink(tmp)
            except Exception: pass
        raise UserTemplatesStoreError(f"user templates not saved: {e}") from e


def list_user_templates() -> list[dict]:
    """Return all user templates with full data. UI gets this for the
    Settings -> Templates panel."""
    return _load()


def get_user_template(tid: str) -> dict | None:
    """Look up a single user template by id. Returns None if not found."""
    for t in _load():
        if t.get("id") == tid:
            return t
    return None


def _generate_id(name: str) -> str:
    """Build a stable, collision-resistant id from a human name.

    Format: ``user_<slug>_<unix_ts>``. The timestamp suffix guarantees
    uniqueness even if the user saves two templates with the same name
    (e.g. retemplating after a site refresh).

    Slugifies non-alphanumeric chars to underscores, lowercases, trims
    to 40 chars to keep IDs sane."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name or "template").strip("_").lower()
    slug = slug[:40] or "template"
    return f"user_{slug}_{int(time.time())}"


def _validate_pattern(pat: str) -> tuple[bool, str]:
    """Verify a pattern compiles as a regex. Returns (ok, error_msg).

    AUDIT v3.43.48: pattern length cap. Python's stdlib `re` has no
    matching timeout — a pathological pattern like '(a+)+$' compiles
    fine then hangs every URL routing call. We can't easily defend
    against backtracking without the third-party `regex` library, but
    we CAN reject patterns long enough to be obvious abuse vectors.
    500 chars is generous (real templates use 30-80 char patterns)."""
    if not isinstance(pat, str):
        return False, "pattern must be a string"
    if len(pat) > 500:
        return False, f"pattern too long ({len(pat)} chars; cap is 500)"
    try:
        re.compile(pat)
        return True, ""
    except re.error as e:
        return False, f"bad regex: {e}"


def _validate_template(t: dict) -> tuple[bool, str]:
    """Verify a template dict has the required structure. Returns
    (ok, error). Mirrors the assertions in test_templates_v438.py so
    user templates are held to the same standard as built-ins."""
    if not isinstance(t, dict):
        return False, "template must be an object"
    for field in ("name", "description"):
        if not isinstance(t.get(field), str) or not t[field].strip():
            return False, f"missing or empty {field}"
    if "patterns" in t and not isinstance(t["patterns"], list):
        return False, "patterns must be a list"
    # Validate each pattern compiles
    for pat in t.get("patterns") or []:
        if not isinstance(pat, str):
            return False, f"pattern must be a string, got {type(pat).__name__}"
        ok, err = _validate_pattern(pat)
        if not ok: return False, f"pattern {pat!r}: {err}"
    # learned.download must exist with row_selectors
    learned = t.get("learned") or {}
    download = learned.get("download") if isinstance(learned, dict) else None
    if not isinstance(download, dict):
        return False, "learned.download must be an object"
    rows = download.get("row_selectors")
    if not isinstance(rows, list) or not rows:
        return False, "learned.download.row_selectors must be a non-empty list"
    # If url_attribute is a list, it must be parallel to row_selectors
    ua = download.get("url_attribute")
    if isinstance(ua, list) and len(ua) != len(rows):
        return False, (f"url_attribute list has {len(ua)} entries but "
                       f"row_selectors has {len(rows)} — must be parallel")
    return True, ""


def save_user_template(
    name: str,
    description: str,
    patterns: list[str],
    learned: dict,
    config_defaults: dict | None = None,
    source: str = "user_teach",
    tid: str | None = None,
) -> tuple[bool, str | dict]:
    """Save a new user template OR update an existing one.

    If ``tid`` is provided and matches an existing user template, that
    template is updated in place. Otherwise a new id is generated and
    the template is appended.

    Returns (True, the_saved_template) on success.
    Returns (False, error_message) on validation failure.

    On success, the returned dict includes the assigned ``id`` and
    ``created_ts`` so the caller can reflect them in the UI immediately."""
    template = {
        "id": tid or _generate_id(name),
        "name": name.strip(),
        "description": (description or "").strip(),
        # F4: only a list is a pattern list; anything else reaches
        # _validate_template as-is and is refused (list("a.com") made one
        # single-character regex per letter, "." matching every URL).
        "patterns": (list(patterns) if isinstance(patterns, (list, tuple))
                     else (patterns if patterns else [])),
        "learned": learned,
        "source": source,
    }
    if config_defaults:
        template["config_defaults"] = config_defaults
    template = _safe_config(template)  # BD-SEC-5 H1: allowlisted keys only
    template = _strip_secrets(template)  # F11: no credential is stored or echoed

    ok, err = _validate_template(template)
    if not ok: return False, err

    # Enforce selector safety at the teach→save create flow: a learned block
    # with generic/nav row selectors (a, [href], nav a, …) would over-match at
    # runtime. lint_learned flags those as errors — refuse rather than store one.
    from .selector_lint import lint_learned, has_blocking_issues
    _dl = (learned or {}).get("download") if isinstance(learned, dict) else None
    _issues = lint_learned(_dl or {})
    if has_blocking_issues(_issues):
        _msgs = "; ".join(i.message for i in _issues if i.level == "error")
        return False, f"unsafe selectors — refine before saving: {_msgs}"[:200]

    with _STORE_LOCK:
        _save_locked(template, tid)

    # C3: record a selector-history version on every save (append-only, best
    # effort). A recording failure must never break the save, so this is fully
    # guarded and its result is ignored.
    try:
        from . import selector_versions as _sv
        _sv.record_template_version(template, source=source)
    except Exception:
        pass

    return True, template


def _save_locked(template: dict, tid: str | None) -> None:
    """Read-modify-write of one template; caller holds _STORE_LOCK."""
    existing = _load_for_write()
    if tid:
        # Update in place
        found = False
        for i, t in enumerate(existing):
            if t.get("id") == tid:
                # Preserve original created_ts; record updated_ts
                template["created_ts"] = t.get("created_ts") or int(time.time())
                template["updated_ts"] = int(time.time())
                existing[i] = template
                found = True
                break
        if not found:
            # tid was supplied but no template has it — treat as new save
            template["created_ts"] = int(time.time())
            existing.append(template)
    else:
        template["created_ts"] = int(time.time())
        existing.append(template)

    _save(existing)


def delete_user_template(tid: str) -> bool:
    """Remove a user template by id. Returns True if it existed and was
    removed, False if no template had that id."""
    with _STORE_LOCK:
        existing = _load_for_write()
        before = len(existing)
        remaining = [t for t in existing if t.get("id") != tid]
        if len(remaining) == before:
            return False
        _save(remaining)
        return True


def restore_template_set(as_of_ts, *, dry_run: bool = False,
                         base_dir=None) -> dict:
    """F8 / PIT: restore the whole template SET to its state as of
    ``as_of_ts`` (epoch seconds). Computes the plan via
    ``selector_versions.plan_set_restore``, then (unless ``dry_run``)
    re-saves each template's chosen historical selector block through
    ``save_user_template`` -- the single writer -- preserving
    name/description/patterns and swapping only ``learned``.

    Non-destructive: a template absent from the current store, or with no
    version at/before the cutoff, is skipped -- never created or emptied.
    Returns ``{"as_of", "restored": [tid...], "skipped":
    [{template_id, reason}...], "dry_run": bool}``."""
    from . import selector_versions as _sv
    plan = _sv.plan_set_restore(as_of_ts, base_dir)
    restored: list[str] = []
    skipped: list[dict] = list(plan.get("skipped") or [])
    for item in plan.get("restore") or []:
        tid = item.get("template_id")
        cur = get_user_template(tid)
        if not cur:
            skipped.append({"template_id": tid,
                            "reason": "not in current template store"})
            continue
        restored.append(tid)
        if dry_run:
            continue
        save_user_template(
            cur.get("name", tid), cur.get("description", ""),
            cur.get("patterns") or [], item.get("selectors") or {},
            config_defaults=cur.get("config_defaults"),
            source="set_restore", tid=tid)
    return {"as_of": plan.get("as_of"), "restored": restored,
            "skipped": skipped, "dry_run": bool(dry_run)}


def export_user_templates() -> dict:
    """Return the full user-templates payload, ready to be JSON-encoded
    and offered as a download. Same schema as the on-disk file."""
    return {
        "version": SCHEMA_VERSION,
        "exported_ts": int(time.time()),
        "templates": [_strip_secrets(t) for t in _load()],
    }


def import_user_templates(payload: dict, merge: bool = True) -> tuple[int, int, list[str]]:
    """Import a previously-exported templates payload.

    Args:
        payload: parsed JSON object with a 'templates' list.
        merge: if True (default), imported templates are added alongside
               existing ones. If False, existing templates are replaced
               entirely with the imported set (DESTRUCTIVE — for full
               restore from backup).

    Returns:
        (added, skipped, errors) — number of templates added, number
        skipped due to id collision (only in merge mode), and list of
        validation errors for templates that couldn't be imported.

    Validation errors don't abort the whole import; valid templates are
    saved, invalid ones are reported. Better partial success than total
    failure when someone hand-edits a template file.
    """
    incoming = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(incoming, list):
        return 0, 0, ["payload missing 'templates' array"]
    with _STORE_LOCK:
        return _import_locked(incoming, merge)


def _import_id_error(t) -> str:
    """F13: an imported template must carry a non-empty string id -- the store
    and GET drop id-less rows, so one would be reported added yet never listed."""
    tid = t.get("id") if isinstance(t, dict) else None
    return "" if isinstance(tid, str) and tid.strip() else "missing id"


def _import_locked(incoming: list, merge: bool) -> tuple[int, int, list[str]]:
    """import_user_templates body; caller holds _STORE_LOCK."""
    if not merge:
        # Replace mode: validate everything first, then save the lot
        valid = []
        errors = []
        seen: set = set()
        skipped = 0
        for i, t in enumerate(incoming):
            ok, err = _validate_template(t)
            if ok and _import_id_error(t):
                ok, err = False, _import_id_error(t)
            if ok and t["id"] in seen:
                skipped += 1  # F12: first entry with an id wins
                continue
            if ok:
                seen.add(t["id"])
                valid.append(_safe_config(_strip_secrets(t)))
            else: errors.append(f"#{i} ({t.get('name','<unnamed>') if isinstance(t, dict) else '<unnamed>'}): {err}")
        # v3.65.2: don't silently wipe the existing template set when a
        # restore-from-backup payload has entries but none validate.
        # The original code called _save([]) unconditionally, so a single
        # bad template in a "restore" file destroyed the user's working
        # set. An empty incoming list IS a legitimate "delete everything"
        # request; an incoming list where every entry failed validation
        # is a corrupted import that should not be acted on.
        if not valid and incoming:
            return 0, 0, errors + [
                "refusing to wipe existing templates: every entry in the "
                "imported payload failed validation. Fix the source file "
                "and try again, or use merge mode to skip bad entries."
            ]
        _save(valid)
        return len(valid), skipped, errors

    # Merge mode: keep existing, append new, skip on id collision
    existing = _load_for_write()
    existing_ids = {t.get("id") for t in existing}
    added = 0
    skipped = 0
    errors = []
    for i, t in enumerate(incoming):
        ok, err = _validate_template(t)
        if ok and _import_id_error(t):
            ok, err = False, _import_id_error(t)
        if not ok:
            errors.append(f"#{i} ({t.get('name','<unnamed>') if isinstance(t, dict) else '<unnamed>'}): {err}")
            continue
        if t.get("id") in existing_ids:
            skipped += 1
            continue
        existing.append(_safe_config(_strip_secrets(t)))
        existing_ids.add(t.get("id"))
        added += 1
    if added:
        _save(existing)
    return added, skipped, errors


# BD-SEC-5 H1: a user template's config_defaults may only carry these keys -- the
# selector / login-URL / quality / fetch-toggle keys the built-in catalog ships.
# Anything else (post_download_cmd and every *_cmd / hook key, download_dir and
# the other path keys, ...) is REFUSED: dropped on import and on read, listed in
# the import preview, never applied to a site. hooks.py runs post_download_cmd
# with shell=True, so a shared template file must not be able to set it.
TEMPLATE_CONFIG_KEYS = frozenset({
    "quality_preference", "min_resolution", "login_url", "success_url",
    "login_trigger", "user_field", "pass_field", "submit_btn",
    "trigger_selector", "dl_selector", "dismiss_selectors",
    "dismiss_selectors_login", "crawler_listing_url", "crawler_scene_patterns",
    "use_curl_cffi", "use_persistent_profile", "use_real_chrome",
    "use_live_recorder", "use_library_extractor", "_limitation",
})


def _refused_config_keys(t) -> list[str]:
    """config_defaults keys of ``t`` outside TEMPLATE_CONFIG_KEYS (a non-object
    config_defaults is refused whole, reported as "config_defaults")."""
    if not isinstance(t, dict) or t.get("config_defaults") in (None, {}):
        return []
    cd = t["config_defaults"]
    if not isinstance(cd, dict):
        return ["config_defaults"]
    return sorted(str(k) for k in cd if k not in TEMPLATE_CONFIG_KEYS)


def _safe_config(t):
    """Copy of template ``t`` whose config_defaults hold only allowlisted keys."""
    if not isinstance(t, dict) or "config_defaults" not in t:
        return t
    cd = t["config_defaults"]
    out = dict(t)
    out["config_defaults"] = ({k: v for k, v in cd.items() if k in TEMPLATE_CONFIG_KEYS}
                              if isinstance(cd, dict) else {})
    return out


# Secret-like fields a portable user template should never carry. The import
# path ignores them; the preview reports them as "secrets omitted" so an
# operator can see they'll be dropped before they apply the import.
# REDACT-SOT unification: a portable template must carry NO credential. The
# secret decision routes through the shared config-domain SoT
# (site_editor.is_secret_config_key) so it can never drift; the old exact
# allowlist below leaked private_key / passphrase / master_password /
# vault_token / client_secret. These template-domain names are NOT credential
# substrings in the general floor but ARE template secrets, so they are kept as
# a domain-specific exact extra (username/*_email are login identity fields the
# export has always stripped).
_TEMPLATE_SECRET_EXTRA = frozenset({
    "username", "auth_email", "auth_pass", "cookies", "cookies_b64",
    "tpdb_key", "api_key", "vpn_username", "vpn_password",
})


def _is_template_secret_key(k) -> bool:
    from .site_editor import is_secret_config_key  # lazy: no static import edge
    return k in _TEMPLATE_SECRET_EXTRA or is_secret_config_key(k)


def _secret_keys_in(t) -> list[str]:
    if not isinstance(t, dict):
        return []
    found = []
    def walk(value, path="", depth=0):
        if depth > 500:
            return
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if _is_template_secret_key(key):
                    found.append(child_path)
                else:
                    walk(child, child_path, depth + 1)
        elif isinstance(value, list):
            for i, child in enumerate(value):
                walk(child, f"{path}[{i}]", depth + 1)
    walk(t)
    return sorted(found)


def _strip_secrets(t):
    """Return a copy of template ``t`` with all secret keys removed. v3.66.557
    (F-CORE_BD10-01): a portable user template must never carry credentials, and the
    import preview promises "secrets omitted" -- so the import/export must actually drop
    them, not just report them. REDACT-SOT: secret decision via the shared SoT."""
    if not isinstance(t, dict):
        return t
    def walk(value, depth=0):
        if depth > 500:
            return {}
        if isinstance(value, dict):
            return {k: walk(v, depth + 1) for k, v in value.items()
                    if not _is_template_secret_key(k)}
        if isinstance(value, list):
            return [walk(v, depth + 1) for v in value]
        return value
    return walk(t)


def preview_user_templates_import(payload: dict, merge: bool = True) -> dict:
    """Read-only preview of :func:`import_user_templates`.

    Classifies what the same (payload, merge) import WOULD do, WITHOUT writing
    anything. Mirrors the import's merge/replace semantics exactly:

      merge   : a valid incoming template with a fresh id is ``new``; an id that
                collides with an existing template is ``conflict`` (the import
                would skip it); a template that fails validation is ``invalid``.
                Merge never removes, so ``destructive`` is empty.
      replace : a valid incoming template is ``new`` (fresh id) or ``changed``
                (existing id, overwritten); existing templates whose id is not
                in the incoming valid set are listed in ``destructive`` (the
                import would remove them). To match the import's safety guard,
                a non-empty payload in which every entry fails validation is
                NOT treated as a wipe — ``destructive`` stays empty and the
                refusal is surfaced in ``errors``.

    Returns:
        {ok, mode, counts:{new,changed,conflict,invalid,destructive,
        secrets_omitted,refused}, items:[{id,name,status,secrets_omitted,
        refused,[error]}] (refused = config_defaults keys outside
        TEMPLATE_CONFIG_KEYS that the import drops, BD-SEC-5),
        destructive:[{id,name}], errors:[str]}
    """
    out = {
        "ok": True,
        "mode": "merge" if merge else "replace",
        "counts": {"new": 0, "changed": 0, "conflict": 0,
                   "invalid": 0, "destructive": 0, "secrets_omitted": 0,
                   "refused": 0},
        "items": [],
        "destructive": [],
        "errors": [],
    }
    incoming = payload.get("templates") if isinstance(payload, dict) else None
    if not isinstance(incoming, list):
        out["ok"] = False
        out["errors"].append("payload missing 'templates' array")
        return out

    existing = _load()
    existing_ids = {t.get("id") for t in existing}
    valid_incoming_ids: set = set()
    payload_ids: set = set()  # F12: a repeated id in the payload is skipped

    for t in incoming:
        name = t.get("name", "<unnamed>") if isinstance(t, dict) else "<unnamed>"
        tid = t.get("id") if isinstance(t, dict) else None
        secret_keys = _secret_keys_in(t)
        refused = _refused_config_keys(t)
        item = {"id": tid, "name": name, "status": None,
                "secrets_omitted": secret_keys, "refused": refused}
        ok, err = _validate_template(t)
        if ok and _import_id_error(t):  # F13: same rule as the import
            ok, err = False, _import_id_error(t)
        if not ok:
            item["status"] = "invalid"
            item["error"] = err
            out["counts"]["invalid"] += 1
        elif tid in payload_ids:
            item["status"] = "conflict"  # F12: the import skips the repeat
            out["counts"]["conflict"] += 1
        elif merge:
            payload_ids.add(tid)
            if tid in existing_ids:
                item["status"] = "conflict"
                out["counts"]["conflict"] += 1
            else:
                item["status"] = "new"
                out["counts"]["new"] += 1
        else:  # replace
            payload_ids.add(tid)
            valid_incoming_ids.add(tid)
            if tid in existing_ids:
                item["status"] = "changed"
                out["counts"]["changed"] += 1
            else:
                item["status"] = "new"
                out["counts"]["new"] += 1
        if secret_keys:
            out["counts"]["secrets_omitted"] += 1
        if refused:
            out["counts"]["refused"] += 1
        out["items"].append(item)

    if not merge:
        any_valid = bool(valid_incoming_ids)
        if incoming and not any_valid:
            out["errors"].append(
                "refusing to wipe existing templates: every entry in the "
                "imported payload failed validation")
        else:
            for t in existing:
                if t.get("id") not in valid_incoming_ids:
                    out["destructive"].append(
                        {"id": t.get("id"), "name": t.get("name", "<unnamed>")})
            out["counts"]["destructive"] = len(out["destructive"])
    return out


def suggest_for_url(url: str) -> list[str]:
    """Like templates.suggest_for_url but for the user-template subset.
    Returned in insertion order. Called by the merged suggester in
    templates.py — don't call directly."""
    if not url: return []
    matches = []
    lower = url.lower()
    for t in _load():
        for pat in t.get("patterns") or []:
            if not isinstance(pat, str): continue
            try:
                if re.search(pat.lower(), lower):
                    matches.append(t["id"])
                    break
            except re.error:
                if pat.lower() in lower:
                    matches.append(t["id"])
                    break
    return matches
