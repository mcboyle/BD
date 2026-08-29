"""v3.52 (Phase 5) — site-editor support: validation, export/import, diff.

The site editor is a large modal form. Until v3.52 the only feedback
the operator got was a 400 on save if a path was bad. This module adds:

  validate_config(cfg) -> {ok, errors, warnings}
      Dry-run validation. Checks required fields, URL well-formedness,
      numeric ranges, selector presence, internal consistency. Errors
      block a save; warnings don't (they flag likely-but-not-certain
      mistakes). Used by a new /api/sites/validate endpoint so the
      editor can validate-as-you-type without persisting anything.

  export_config(cfg, *, include_secrets) -> dict
      Produce a portable JSON snapshot of one site's config. Secrets
      (password, API keys, tokens) are stripped by default — the
      operator opts in with include_secrets=True for a personal
      backup. The export carries a small envelope (schema marker +
      app version) so import can sanity-check it.

  import_config(payload) -> {ok, config, errors}
      Inverse of export. Validates the envelope, strips anything that
      isn't a known CFG_FIELD, runs validate_config on the result.

  diff_config(old, new) -> list[{field, old, new, kind}]
      Field-by-field comparison for the "preview changes before save"
      affordance. Secrets are shown as changed/unchanged booleans, never
      as values.

This module is pure logic over plain dicts — no Flask, no DB — so it's
cheap to unit-test exhaustively.
"""
from __future__ import annotations

import math
import re
from typing import Any
from urllib.parse import urlparse

# Envelope marker for exported configs. Bumped only if the export
# format changes incompatibly.
EXPORT_SCHEMA = "bd-site-config/1"

# Secret-bearing fields — stripped from exports unless explicitly
# included, and shown as booleans (not values) in diffs. Kept in sync
# with the redaction list in audit.py.
SECRET_FIELDS = frozenset({
    "password", "qb_password", "captcha_api_key", "stash_api_key",
    "plex_token", "jellyfin_api_key", "ha_token", "tpdb_api_key",
    "ai_api_key", "auth_token",
})

# --- Shared config-domain secret-key predicate (REDACT-SOT unification) -------
# The single source of truth for "is this CONFIG/template/VPN FIELD NAME a
# credential?" -- distinct from the URL/query SoT (capture_redact.SENSITIVE_QS_KEY)
# which governs signed-URL query keys. Four modules (app_settings_center, vpn,
# vpn_config, user_templates) had each drifted their own keyword set and were
# leaking real reachable secrets (private_key / cookies / passphrase /
# preshared_key / master_password / vault_token / client_secret). They now all
# route through this predicate so they can never drift again.
#
# D1 (fail-OPEN): the floor is a CURATED substring set that deliberately EXCLUDES
# the bare ambiguous terms "key" / "sid" / "session" / "auth" / "login", so real
# non-secret fields (key_rotation_days, session_timeout, sid_cookie_name,
# login_url) are NOT over-redacted. Only distinctive credential substrings are
# floored. Widening this set risks feature breakage -- add exact names to
# SECRET_FIELDS instead of broadening the floor.
_CONFIG_SECRET_FLOOR = (
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "credential", "private_key", "privatekey", "passphrase", "vault",
    "cookies", "preshared",
)


def is_secret_config_key(k) -> bool:
    """True iff a CONFIG/template/VPN field NAME denotes a credential value.

    SECRET_FIELDS (exact) UNION a curated distinctive-substring floor. Fail-open
    per D1: bare ambiguous terms (key/sid/session/auth/login) never trigger, so a
    real non-secret field is not wrongly redacted. This is the shared SoT for the
    config-field domain; the URL/query domain uses capture_redact.SENSITIVE_QS_KEY.
    """
    if not isinstance(k, str):
        return False
    if k in SECRET_FIELDS:
        return True
    kl = k.lower()
    return any(marker in kl for marker in _CONFIG_SECRET_FLOOR)

# Fields that must be non-empty for ANY usable site config.
REQUIRED_FIELDS = ("name",)

# Numeric fields with their (min, max) acceptable range. A value
# outside the range is an error; the editor's number inputs should
# already clamp, but a hand-edited sites_config.json or an import
# could carry anything.
NUMERIC_RANGES = {
    "wait":                 (0, 120),
    "delay":                (0, 300),
    "max_concurrent":       (1, 32),
    "max_retries":          (0, 20),
    "no_button_threshold":  (1, 100),
    "disk_threshold_gb":    (0, 10000),
    "min_resolution":       (0, 8640),
    "chunk_size_mb":        (1, 256),
    "prelogin_minutes":     (0, 1440),
    "parallel_chunks":      (1, 32),
    "parallel_min_size_mb": (0, 100000),
    "auto_relogin_interval_hours": (1, 168),
    "warmup_every":         (0, 86400),
    "min_size_pct":         (0, 100),
    # MOD-1 F1.4 (v3.66.810): predictive-relogin fraction is a 0..1 multiplier of
    # the learned-lifetime median. Float-consumed (NOT in INT_TYPED_FIELDS).
    "predictive_relogin_fraction": (0.0, 1.0),
}

# v3.66.527 (VR-P11 redirected): the subset of NUMERIC_RANGES whose runtime
# consumers read with bare int() (runner.py min-res gate, runner_extractors yt-dlp
# hint, chunk/concurrency/retry/threshold knobs). A fractional value for one of
# these (e.g. min_resolution=1080.5 or the string "1080.5") passes float()+range
# but then ValueErrors at int("1080.5") mid-download. We reject non-integer values
# for these fields at the boundary. The remaining (wait/delay/disk_threshold_gb/
# parallel_min_size_mb/auto_relogin_interval_hours/min_size_pct) are float-consumed
# and legitimately fractional, so they are NOT integer-checked.
INT_TYPED_FIELDS = frozenset({
    "max_concurrent", "max_retries", "no_button_threshold", "min_resolution",
    "chunk_size_mb", "prelogin_minutes", "parallel_chunks", "warmup_every",
})


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _looks_like_url(v: str) -> bool:
    """Loose URL check — has a scheme and a netloc. We don't require
    http(s) specifically because some sites are configured with custom
    schemes, but a bare 'example.com' with no scheme is almost always
    a mistake."""
    try:
        p = urlparse(v.strip())
        return bool(p.scheme) and bool(p.netloc)
    except Exception:
        return False


def validate_numeric_updates(updates: dict) -> dict:
    """Range/type backstop for a proposed update set — the numeric subset of
    ``validate_config``, sourced from the same ``NUMERIC_RANGES`` table so the two
    cannot diverge. Returns ``{field: message}`` for any non-numeric or out-of-range
    value among the NUMERIC_RANGES fields *present* in ``updates``. Blank values are
    skipped (preserve-on-blank handles those); non-numeric fields are untouched. Pure
    (no side effects), so it is safe to call from the audited write path.
    """
    errors: dict = {}
    for field, (lo, hi) in NUMERIC_RANGES.items():
        if field not in (updates or {}):
            continue
        v = updates[field]
        if v in (None, ""):
            continue
        if isinstance(v, bool) or isinstance(v, (list, dict)):
            errors[field] = f"'{field}' must be a number (got {v!r})."
            continue
        try:
            n = float(v)
        except (TypeError, ValueError):
            errors[field] = f"'{field}' must be a number (got {v!r})."
            continue
        # v3.66.523 (VR-P08): NaN slips the range check below (NaN < lo and
        # NaN > hi are BOTH False), so it would persist into live config via the
        # audited PUT. Reject any non-finite value (NaN / +-inf) explicitly.
        if not math.isfinite(n):
            errors[field] = f"'{field}' must be a finite number (got {v!r})."
            continue
        # v3.66.527 (VR-P11 redirected): integer-typed fields are read with bare
        # int() at runtime; a fractional value (1080.5 / "1080.5") passes float()
        # but ValueErrors at the consumer. Reject it here, at the boundary. Numeric
        # strings of whole numbers ("1080") still parse to an integer-valued float
        # and are accepted (preserves the pinned numeric-string contract).
        if field in INT_TYPED_FIELDS and not float(n).is_integer():
            errors[field] = f"'{field}' must be a whole number (got {v!r})."
            continue
        if n < lo or n > hi:
            errors[field] = f"'{field}' must be between {lo} and {hi} (got {n:g})."
    return errors


def validate_config(cfg: dict) -> dict:
    """Dry-run validate a site config dict.

    Returns {ok: bool, errors: [str], warnings: [str]}. `ok` is True iff
    errors is empty — warnings never block. Each message is a complete
    human-readable sentence the editor can show inline.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(cfg, dict):
        return {"ok": False,
                "errors": ["Config must be a JSON object."],
                "warnings": []}

    # ── Required fields ────────────────────────────────────────────
    for field in REQUIRED_FIELDS:
        if _is_blank(cfg.get(field)):
            errors.append(f"'{field}' is required and cannot be empty.")

    # ── URL fields ─────────────────────────────────────────────────
    for url_field in ("login_url", "success_url"):
        val = cfg.get(url_field)
        if not _is_blank(val) and not _looks_like_url(str(val)):
            errors.append(
                f"'{url_field}' does not look like a valid URL "
                f"(needs a scheme like https://).")

    # ── Numeric ranges ─────────────────────────────────────────────
    for field, (lo, hi) in NUMERIC_RANGES.items():
        if field not in cfg or cfg[field] in (None, ""):
            continue
        try:
            n = float(cfg[field])
        except (TypeError, ValueError):
            errors.append(
                f"'{field}' must be a number (got {cfg[field]!r}).")
            continue
        # v3.66.559 (F-COREBD06-01 / VR-P08 parity): NaN slips the range check
        # below (NaN < lo and NaN > hi are BOTH False), so it would persist into
        # sites_config.json via a hand-edit or import. Reject any non-finite
        # value (NaN / +-inf) explicitly, matching validate_numeric_updates.
        if not math.isfinite(n):
            errors.append(
                f"'{field}' must be a finite number (got {cfg[field]!r}).")
            continue
        if n < lo or n > hi:
            errors.append(
                f"'{field}' must be between {lo} and {hi} "
                f"(got {n:g}).")

    # ── String-type coercion sanity ────────────────────────────────
    # Collection types in string slots corrupt sites_config.json.
    for str_field in ("name", "login_url", "username", "cookie_file",
                      "download_dir", "trigger_selector", "dl_selector",
                      "filename_template"):
        v = cfg.get(str_field)
        if v is not None and isinstance(v, (list, dict)):
            errors.append(
                f"'{str_field}' must be a string, not a "
                f"{type(v).__name__}.")

    # ── Login consistency ──────────────────────────────────────────
    # If a login_url is set, the operator almost certainly wants
    # credentials OR a cookie_file. Warn (don't error) — some sites
    # are public and just need a starting URL.
    has_login_url = not _is_blank(cfg.get("login_url"))
    has_creds = not _is_blank(cfg.get("username")) or \
        bool(cfg.get("has_password")) or not _is_blank(cfg.get("password"))
    has_cookie = not _is_blank(cfg.get("cookie_file"))
    if has_login_url and not has_creds and not has_cookie:
        warnings.append(
            "A login URL is set but no username/password or cookie "
            "file — auto-login won't work. Fine if the site is public.")

    # ── cookie_file filesystem sanity (D3 U4: Tier 1 #8) ──────────
    # The v3.63.10 trigger bug: cookie_file pointing at a directory,
    # not a file. The runner's `os.path.exists()` returned True; the
    # cookie load failed obscurely on first download. Catch the
    # directory case as a HARD ERROR (it's never right) and a
    # missing-path as a WARNING (the file may be created later by
    # an export). A regular-file path that exists: no message.
    cf = cfg.get("cookie_file")
    if not _is_blank(cf) and isinstance(cf, str):
        try:
            import os as _os
            cf_str = cf.strip()
            if cf_str and _os.path.isdir(cf_str):
                errors.append(
                    f"'cookie_file' points at a directory, not a file: "
                    f"{cf_str!r}. Set it to the cookies.json file inside "
                    f"that directory.")
            elif cf_str and not _os.path.exists(cf_str):
                # Path doesn't exist yet — may be created later by an
                # export-from-browser flow. Warning, not error.
                warnings.append(
                    f"'cookie_file' path does not exist yet: "
                    f"{cf_str!r}. Downloads will fail until cookies are "
                    f"exported there.")
        except (OSError, ValueError):
            # Bad path (unicode etc.) — let other validation surface it.
            pass

    # If credentials are set, the login form selectors should be too.
    if has_creds:
        blank_sel = any(_is_blank(cfg.get(f))
                        for f in ("user_field", "pass_field", "submit_btn"))
        if blank_sel:
            # 3a: a host with a CURATED login template gets its selectors
            # auto-filled on save (_auto_pick_templates), so the per-field
            # "login may fail" warnings are a false alarm. Suppress them and
            # surface a single informational line instead. With no curated
            # match, collapse the three near-identical bullets into one
            # actionable line (3b now lets the operator fill the fields inline).
            curated_name = ""
            try:
                from . import login_templates_data as _ltd
                _ids = _ltd.suggest_login_for_url(cfg.get("login_url"))
                if _ids:
                    _tpl = _ltd.get_login_template(_ids[0]) or {}
                    curated_name = _tpl.get("name") or _ids[0]
            except Exception:
                # Fail-open: if the lookup explodes, fall through to the
                # collapsed actionable warning below.
                curated_name = ""
            if curated_name:
                warnings.append(
                    f"Login selectors will be auto-filled from the "
                    f"{curated_name} login template on save.")
            else:
                warnings.append(
                    "Login credentials are set but the login form selectors "
                    "(username/password/submit) are empty — set them in the "
                    "Advanced login selectors section, or downloads may fail "
                    "to log in.")

    # ── Filename template sanity ───────────────────────────────────
    tpl = cfg.get("filename_template")
    if not _is_blank(tpl):
        tpl_s = str(tpl)
        # Unbalanced braces are the most common template typo.
        if tpl_s.count("{") != tpl_s.count("}"):
            errors.append(
                "Filename template has unbalanced { } braces.")
        # A template with no extension placeholder AND no literal dot
        # will produce extension-less files.
        if "{ext}" not in tpl_s and "." not in tpl_s:
            warnings.append(
                "Filename template has no {ext} placeholder and no "
                "literal extension — downloads may lack a file "
                "extension.")

    # ── Proxy format ───────────────────────────────────────────────
    proxy = cfg.get("proxy")
    if not _is_blank(proxy):
        # Expect scheme://[user:pass@]host:port
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", str(proxy)):
            errors.append(
                "Proxy must start with a scheme "
                "(e.g. http:// or socks5://).")

    # ── Scheduling consistency ─────────────────────────────────────
    if cfg.get("sched_enabled") and _is_blank(cfg.get("sched_time")):
        errors.append(
            "Scheduling is enabled but no schedule time is set.")

    return {"ok": not errors, "errors": errors, "warnings": warnings}


def export_config(cfg: dict, *, include_secrets: bool = False) -> dict:
    """Produce a portable snapshot of one site's config.

    Returns an envelope:
        {"schema": EXPORT_SCHEMA, "app_version": "...",
         "exported_at": <epoch>, "config": {...}}

    Secrets are stripped unless include_secrets=True. When stripped,
    a `_had_secrets` list records which fields were present, so a
    human reading the export knows credentials existed (and that
    they'll need re-entering on import).
    """
    import time
    try:
        from . import __version__ as app_version
    except Exception:
        app_version = "unknown"

    out_cfg = {}
    stripped: list[str] = []
    for k, v in cfg.items():
        # Skip UI-only / derived keys that shouldn't round-trip
        if k.startswith("has_") or k == "fingerprint":
            continue
        if k in SECRET_FIELDS:
            if include_secrets:
                # v3.66.326: secrets are stored as vault @cred: references. A
                # backup with include_secrets must contain the REAL secret, so
                # resolve each ref to plaintext here (requires an unlocked
                # vault). resolve_password() passes legacy plaintext through
                # unchanged and returns None for a @cred ref it can't decrypt
                # (locked/missing) -> in that case omit the field and record
                # that a secret was present, exactly as the stripped path does.
                try:
                    from . import secrets_store as _ss
                    resolved = _ss.resolve_password(v)
                except Exception:
                    resolved = v if not (isinstance(v, str)
                                         and v.startswith("@cred:")) else None
                if resolved in (None, ""):
                    if v:
                        stripped.append(k)
                    continue
                out_cfg[k] = resolved
            else:
                if v:  # only note non-empty secrets
                    stripped.append(k)
                continue
        else:
            out_cfg[k] = v

    envelope = {
        "schema": EXPORT_SCHEMA,
        "app_version": app_version,
        "exported_at": time.time(),
        "config": out_cfg,
    }
    if stripped:
        envelope["_had_secrets"] = sorted(stripped)
    return envelope


def import_config(payload: Any, *, known_fields: set | None = None) -> dict:
    """Validate + normalize an exported config envelope for import.

    Returns {ok, config, errors, warnings}. The returned `config` is
    suitable to feed straight into the site-create path. Anything not
    in `known_fields` (default: accept all) is dropped with a warning
    so a malformed or hostile import can't smuggle unexpected keys
    into sites_config.json.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(payload, dict):
        return {"ok": False, "config": {}, "warnings": [],
                "errors": ["Import payload must be a JSON object."]}

    # Envelope check. We accept either a full envelope OR a bare config
    # dict (older exports / hand-written), but warn on the bare form.
    if payload.get("schema") == EXPORT_SCHEMA and "config" in payload:
        cfg = payload["config"]
    elif "config" in payload and isinstance(payload["config"], dict):
        cfg = payload["config"]
        warnings.append(
            "Import envelope schema marker missing or unrecognized — "
            "importing the inner config anyway.")
    else:
        # Treat the whole payload as a bare config
        cfg = payload
        warnings.append(
            "No export envelope detected — treating the entire "
            "payload as a site config.")

    if not isinstance(cfg, dict):
        return {"ok": False, "config": {}, "warnings": warnings,
                "errors": ["The imported 'config' is not an object."]}

    # Drop unknown keys
    clean: dict = {}
    if known_fields:
        for k, v in cfg.items():
            if k in known_fields:
                clean[k] = v
            else:
                warnings.append(f"Dropped unknown field '{k}'.")
    else:
        clean = dict(cfg)

    # If the export stripped secrets, surface that the operator must
    # re-enter them.
    if payload.get("_had_secrets"):
        warnings.append(
            "This export had secrets removed: "
            + ", ".join(payload["_had_secrets"])
            + ". Re-enter them after import.")

    # Run full validation on the cleaned config
    v = validate_config(clean)
    errors.extend(v["errors"])
    warnings.extend(v["warnings"])

    return {"ok": not errors, "config": clean,
            "errors": errors, "warnings": warnings}


def diff_config(old: dict, new: dict) -> list[dict]:
    """Field-by-field diff for the 'preview changes' affordance.

    Returns a list of {field, old, new, kind} where kind is one of:
      'added'    — field present in new, absent/blank in old
      'removed'  — field blank in new, present in old
      'changed'  — different non-blank values
    Unchanged fields are omitted. Secret fields never expose their
    values — old/new are reported as '(set)' / '(empty)'.

    IMPORTANT: only keys PRESENT in `new` are considered. The site
    editor sends a partial config (just the form fields), so a key in
    `old` but absent from `new` means "this edit doesn't touch that
    field" — NOT "remove it". Treating absence as removal would report
    ~150 spurious changes on every edit. A genuine removal is the
    operator clearing a field, which arrives as the key present in
    `new` with a blank value.
    """
    old = old or {}
    new = new or {}
    diffs: list[dict] = []
    # Only iterate keys the proposed config actually carries.
    keys = sorted(
        k for k in new
        if not k.startswith("has_") and k != "fingerprint")
    for k in keys:
        ov = old.get(k)
        nv = new.get(k)
        if k in SECRET_FIELDS:
            had = bool(ov)
            has = bool(nv)
            if had != has:
                diffs.append({
                    "field": k,
                    "old": "(set)" if had else "(empty)",
                    "new": "(set)" if has else "(empty)",
                    "kind": "added" if has and not had else
                            "removed" if had and not has else "changed",
                    "secret": True,
                })
            continue
        # Normalize blanks so "" and None compare equal
        ob = _is_blank(ov)
        nb = _is_blank(nv)
        if ob and nb:
            continue
        if ob and not nb:
            diffs.append({"field": k, "old": "", "new": nv,
                          "kind": "added"})
        elif nb and not ob:
            diffs.append({"field": k, "old": ov, "new": "",
                          "kind": "removed"})
        elif ov != nv:
            diffs.append({"field": k, "old": ov, "new": nv,
                          "kind": "changed"})
    return diffs


# ── v3.59 (Phase 11, #58): JSON Schema for sites_config.json ─────────────

# Field → (json type, human description). Anything in CFG_FIELDS not
# listed here defaults to a permissive string. The schema is a
# convenience for editor autocomplete / validation — it is deliberately
# NOT strict (no `required`, `additionalProperties` stays true) because
# sites_config.json is hand-edited and partial configs are normal.
_FIELD_TYPES = {
    "wait": ("number", "Seconds to wait after page load"),
    "delay": ("number", "Seconds between downloads"),
    "max_concurrent": ("integer", "Parallel downloads for this site"),
    "max_retries": ("integer", "Retry attempts per URL"),
    "no_button_threshold": ("integer",
                            "Consecutive no-button hits before giving up"),
    "disk_threshold_gb": ("number",
                          "Pause downloads below this free space"),
    "chunk_size_mb": ("number", "HTTP download chunk size in MB"),
    "prelogin_minutes": ("number", "Pre-login this many minutes early"),
    "login_trigger": ("string", "Selector that reveals the login form"),
    "auto_relogin_interval_hours": ("number",
                                    "Hours between auto-relogins"),
    "warmup_every": ("number", "Seconds between warmup visits"),
    "headless": ("boolean", "Run the browser headless"),
    "verify_integrity": ("boolean", "Verify file integrity after download"),
    "skip_if_exists": ("boolean", "Skip URLs already on disk"),
    "use_http_dl": ("boolean", "Use direct HTTP download when possible"),
    "sched_enabled": ("boolean", "Enable the per-site schedule"),
    "auto_relogin_enabled": ("boolean", "Enable auto-relogin"),
    "predictive_relogin_enabled": ("boolean",
        "Relogin predictively at a fraction of the learned session-lifetime median"),
    "predictive_relogin_fraction": ("number",
        "Fraction of the learned lifetime median to relogin at (0..1; default 0.8)"),
    "use_real_chrome": ("boolean", "Use a real Chrome binary"),
    "use_stealth": ("boolean", "Enable stealth anti-detection"),
    "use_stealth_library": ("boolean", "Use the stealth library"),
    "use_persistent_profile": ("boolean", "Persist the browser profile"),
    "log_network": ("boolean", "Log every network response"),
    "use_curl_cffi": ("boolean", "TLS-impersonating HTTP downloads"),
    # v3.66.468 WS4b: type the download-backend selector + JD connection fields
    # so SiteSettings renders a dropdown + labelled inputs (they were free-text
    # before, so JD was config-file-only in practice). `backend` stays a STRING
    # type (a valid JSON-Schema type) with its choices in _FIELD_ENUMS below --
    # the GUI derives the <select> from the enum choices, and generate_json_schema
    # emits {"type":"string","enum":[...]}; "enum" is never used as a JSON-Schema
    # type.
    "backend": ("string", "Download backend for this site"),
    "jd_host": ("string", "JDownloader 2 Remote API host"),
    "jd_port": ("integer", "JDownloader 2 Remote API port"),
    # v3.66.702 (JD-3): supported-hosts endpoint path. Blank -> jd_bridge's
    # documented-default probe path. A per-build override for JD2's deprecated
    # Remote API, which has no single documented supported-hosts endpoint.
    "jd_supported_hosts_path": ("string", "JDownloader supported-hosts endpoint path (blank = default)"),
}

# v3.66.468 WS4b: explicit choices for enum-typed fields. Surfaced in each
# field descriptor so the GUI can render a <select>. Keep in sync with
# is_jd_backend / qb_bridge (the runner reads `backend` as teach|jd|qb).
_FIELD_ENUMS = {
    "backend": ["teach", "jd", "qb"],
}


def generate_json_schema(cfg_fields: list) -> dict:
    """#58 — build a Draft-07 JSON Schema describing sites_config.json.

    `cfg_fields` is the app's CFG_FIELDS list. The result is a schema
    whose top level is an object mapping site-id → site config, where
    each site config is an object with typed, described properties.

    Non-strict by design: `additionalProperties` is true and nothing is
    `required`, because the file is hand-edited and partial configs are
    valid. The value is editor autocomplete + type hints, not gatekeeping.
    """
    properties: dict = {}
    for field in cfg_fields:
        jtype, desc = _FIELD_TYPES.get(field, ("string", ""))
        prop: dict = {"type": jtype}
        # v3.66.468: enum-choice fields are JSON-Schema {"type":"string","enum":[...]}
        # ("enum" is a keyword, never a type); choices come from _FIELD_ENUMS.
        choices = _FIELD_ENUMS.get(field)
        if choices:
            prop["type"] = "string"
            prop["enum"] = list(choices)
        if desc:
            prop["description"] = desc
        properties[field] = prop
    site_schema = {
        "type": "object",
        "properties": properties,
        "additionalProperties": True,
    }
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "Bulk Downloader sites_config.json",
        "description": "Map of site-id to per-site configuration.",
        "type": "object",
        "additionalProperties": site_schema,
    }
