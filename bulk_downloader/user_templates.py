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
import re
import time
from pathlib import Path

USER_TEMPLATES_FILE = Path("user_templates.json")
SCHEMA_VERSION = 1


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
    return [t for t in templates if isinstance(t, dict) and "id" in t]


def _save(templates: list[dict]) -> None:
    """Write templates list atomically. Tmpfile + rename so a crash
    mid-write can't leave a corrupt file."""
    payload = {
        "version": SCHEMA_VERSION,
        "templates": templates,
    }
    tmp = USER_TEMPLATES_FILE.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(USER_TEMPLATES_FILE)
    except Exception as e:
        import sys
        sys.stderr.write(f"  user_templates save failed: {e}\n")
        if tmp.exists():
            try: tmp.unlink()
            except Exception: pass


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
        "patterns": list(patterns or []),
        "learned": learned,
        "source": source,
    }
    if config_defaults:
        template["config_defaults"] = config_defaults

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

    existing = _load()
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

    # C3: record a selector-history version on every save (append-only, best
    # effort). A recording failure must never break the save, so this is fully
    # guarded and its result is ignored.
    try:
        from . import selector_versions as _sv
        _sv.record_template_version(template, source=source)
    except Exception:
        pass

    return True, template


def delete_user_template(tid: str) -> bool:
    """Remove a user template by id. Returns True if it existed and was
    removed, False if no template had that id."""
    existing = _load()
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

    if not merge:
        # Replace mode: validate everything first, then save the lot
        valid = []
        errors = []
        for i, t in enumerate(incoming):
            ok, err = _validate_template(t)
            if ok: valid.append(_strip_secrets(t))
            else: errors.append(f"#{i} ({t.get('name','<unnamed>')}): {err}")
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
        return len(valid), 0, errors

    # Merge mode: keep existing, append new, skip on id collision
    existing = _load()
    existing_ids = {t.get("id") for t in existing}
    added = 0
    skipped = 0
    errors = []
    for i, t in enumerate(incoming):
        ok, err = _validate_template(t)
        if not ok:
            errors.append(f"#{i} ({t.get('name','<unnamed>')}): {err}")
            continue
        if t.get("id") in existing_ids:
            skipped += 1
            continue
        existing.append(_strip_secrets(t))
        existing_ids.add(t.get("id"))
        added += 1
    if added:
        _save(existing)
    return added, skipped, errors


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
    return sorted(k for k in t if _is_template_secret_key(k))


def _strip_secrets(t):
    """Return a copy of template ``t`` with all secret keys removed. v3.66.557
    (F-CORE_BD10-01): a portable user template must never carry credentials, and the
    import preview promises "secrets omitted" -- so the import/export must actually drop
    them, not just report them. REDACT-SOT: secret decision via the shared SoT."""
    if not isinstance(t, dict):
        return t
    return {k: v for k, v in t.items() if not _is_template_secret_key(k)}


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
        secrets_omitted}, items:[{id,name,status,secrets_omitted,[error]}],
        destructive:[{id,name}], errors:[str]}
    """
    out = {
        "ok": True,
        "mode": "merge" if merge else "replace",
        "counts": {"new": 0, "changed": 0, "conflict": 0,
                   "invalid": 0, "destructive": 0, "secrets_omitted": 0},
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

    for t in incoming:
        name = t.get("name", "<unnamed>") if isinstance(t, dict) else "<unnamed>"
        tid = t.get("id") if isinstance(t, dict) else None
        secret_keys = _secret_keys_in(t)
        item = {"id": tid, "name": name, "status": None,
                "secrets_omitted": secret_keys}
        ok, err = _validate_template(t)
        if not ok:
            item["status"] = "invalid"
            item["error"] = err
            out["counts"]["invalid"] += 1
        elif merge:
            if tid in existing_ids:
                item["status"] = "conflict"
                out["counts"]["conflict"] += 1
            else:
                item["status"] = "new"
                out["counts"]["new"] += 1
        else:  # replace
            valid_incoming_ids.add(tid)
            if tid in existing_ids:
                item["status"] = "changed"
                out["counts"]["changed"] += 1
            else:
                item["status"] = "new"
                out["counts"]["new"] += 1
        if secret_keys:
            out["counts"]["secrets_omitted"] += 1
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
