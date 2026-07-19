"""Template marketplace — export, import, and verify template bundles
(Phase 114, Block P).

Operators build complex site templates (CSS selectors, auth flows,
quality preferences, post-process hooks). Sharing them between
operators today means screen-sharing JSON over Discord. This module
formalizes the exchange:

  • export_template(site_id) — produce a portable .bdtpl bundle:
      {
        "schema": "bd-template-v1",
        "exported_at": "2026-01-15T12:00:00Z",
        "site_id": "vixen",
        "template": {...},          # full site config
        "learned": {...},            # learned selectors (optional)
        "metadata": {                # human-friendly
          "name": "Vixen 1080p+",
          "description": "...",
          "author": "operator handle",
          "compatible_bd_version": "3.43.80",
        },
        "signature": "<hmac-sha256>"  # if exported with a signing key
      }

  • import_template(bundle_bytes, *, target_site_id) — validate,
    verify signature (if present), and emit the importable site config

  • verify_signature(bundle, public_key) — independent verify, e.g.
    "is this from the operator I trust?"

  • bundle_to_json / bundle_from_json — handle the file format

Trust model: HMAC-SHA256 with a pre-shared secret between collaborating
operators. Public-key crypto would be nicer but requires bringing in a
heavy dep (cryptography). HMAC needs only stdlib hashlib.

Compatibility: schema version is encoded so future versions can refuse
or upgrade. Bundle never includes cookies, credentials, or VPN keys —
only the structural template. Operator must re-auth on the importing
side.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import re
from typing import Optional


SCHEMA_VERSION = "bd-template-v1"

# Keys we always strip from the template before export — sensitive
# secrets that shouldn't travel between operators.
_REDACT_KEYS = {
    "username", "password", "auth_email", "auth_pass",
    "cookies", "cookies_b64", "tpdb_key", "auth_token",
    "api_key", "vpn_username", "vpn_password",
    "_account_pool",  # account pool state — operator-specific
}

# Keys that are operator-environment-specific and should be reset on
# import (so the importer's local paths don't reference the exporter's
# disk layout).
_LOCAL_PATH_KEYS = {
    "download_dir", "cookies_path", "log_path",
    "ramdisk_path", "stash_db_path",
}


# ─── Sanitize ─────────────────────────────────────────────────────────

# F-CORE_BD14-01: the export redaction set MUST be a superset of the
# authoritative config-secret SoT (site_editor.SECRET_FIELDS) so a portable
# bundle can never fall behind a newly added secret field. marketplace's own
# _REDACT_KEYS historically covered only 2/10 of SECRET_FIELDS (and name-
# mismatched tpdb_key vs tpdb_api_key), leaking plex_token / *_api_key / etc.
# into the downloadable EOL/marketplace export. SECRET_FIELDS is imported
# lazily + memoized so no static import edge is added to the module graph
# (matching the 540-542 lazy-import guard pattern).
_EFFECTIVE_REDACT_KEYS = None


def _redact_key_set() -> set:
    """_REDACT_KEYS UNION site_editor.SECRET_FIELDS -- the effective secret-field
    set used by every export/preview redaction (superset of SECRET_FIELDS per
    I-CORE_BD14)."""
    global _EFFECTIVE_REDACT_KEYS
    if _EFFECTIVE_REDACT_KEYS is None:
        from .site_editor import SECRET_FIELDS  # lazy: no static import edge
        _EFFECTIVE_REDACT_KEYS = set(_REDACT_KEYS) | set(SECRET_FIELDS)
    return _EFFECTIVE_REDACT_KEYS


def _export_key_is_secret(k) -> bool:
    """A key is stripped from a portable export if it is in the effective redact
    set OR matches the shared config-domain SoT (REDACT-SOT Cut 2). The exact set
    alone (SECRET_FIELDS) missed the substring-floor credentials (private_key /
    cookies / passphrase / preshared_key / master_password / vault_token /
    client_secret), which then leaked into the downloadable bundle. Routing
    through is_secret_config_key keeps the export in lock-step with the SoT."""
    from .site_editor import is_secret_config_key  # lazy: no static import edge
    return k in _redact_key_set() or is_secret_config_key(k)


def _redact(cfg: dict) -> dict:
    """Return a copy of cfg with sensitive keys stripped + local paths
    reset to placeholders."""
    out = {}
    for k, v in (cfg or {}).items():
        if _export_key_is_secret(k):
            continue
        if k in _LOCAL_PATH_KEYS:
            out[k] = ""  # operator fills in on import
            continue
        if isinstance(v, dict):
            # Recursively redact nested dicts
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


# ─── Sign / Verify ────────────────────────────────────────────────────

def _canonical_bytes(bundle: dict) -> bytes:
    """Stable bytes representation for signing. JSON with sorted keys,
    no spaces, signature field excluded."""
    sanitized = {k: v for k, v in bundle.items() if k != "signature"}
    return json.dumps(sanitized, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def sign_bundle(bundle: dict, *, secret: str) -> str:
    """Compute HMAC-SHA256 over the canonical bytes of `bundle`.
    Returns hex digest. Bundle is not modified."""
    if not secret:
        raise ValueError("signing secret required")
    canonical = _canonical_bytes(bundle)
    return hmac.new(secret.encode("utf-8"), canonical,
                    hashlib.sha256).hexdigest()


def verify_signature(bundle: dict, *, secret: str) -> dict:
    """Verify the bundle's signature. Returns:
      {ok: bool, signed: bool, expected: str, actual: str}
    `signed=False` means the bundle had no signature field. `ok=True`
    when either unsigned OR signed-and-valid."""
    actual = bundle.get("signature", "")
    if not actual:
        return {"ok": True, "signed": False, "expected": "", "actual": ""}
    if not secret:
        return {"ok": False, "signed": True, "expected": "",
                "actual": actual, "error": "secret required"}
    expected = sign_bundle(bundle, secret=secret)
    return {
        "ok": hmac.compare_digest(expected, actual),
        "signed": True,
        "expected": expected,
        "actual": actual,
    }


# ─── Export ───────────────────────────────────────────────────────────

def export_template(
    site_id: str,
    site_cfg: dict,
    *,
    name: Optional[str] = None,
    description: str = "",
    author: str = "",
    bd_version: str = "3.43.80",
    include_learned: bool = True,
    sign_with: Optional[str] = None,
) -> dict:
    """Build a portable bundle for one site.

    `include_learned` controls whether `learned.*` selectors are
    included — usually yes (these are valuable), but operators may
    want to ship pristine configs without their custom teach
    overrides.

    `sign_with`, when set, HMAC-signs the bundle. Receivers with the
    same secret can verify provenance."""
    if not isinstance(site_cfg, dict):
        raise ValueError("site_cfg must be a dict")
    cfg_copy = _redact(site_cfg)
    if not include_learned:
        cfg_copy.pop("learned", None)
    bundle = {
        "schema": SCHEMA_VERSION,
        "exported_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site_id": site_id,
        "template": cfg_copy,
        "metadata": {
            "name": name or site_cfg.get("name", site_id),
            "description": description,
            "author": author,
            "compatible_bd_version": bd_version,
        },
    }
    if sign_with:
        bundle["signature"] = sign_bundle(bundle, secret=sign_with)
    return bundle


def bundle_to_bytes(bundle: dict) -> bytes:
    """Serialize a bundle to the on-wire format. JSON UTF-8 with
    2-space indent for human reading."""
    return json.dumps(bundle, indent=2, sort_keys=False,
                      ensure_ascii=False).encode("utf-8")


def bundle_to_filename(bundle: dict) -> str:
    """Recommend a filename for the bundle. e.g. 'vixen-2025-12-15.bdtpl'."""
    sid = (bundle.get("site_id") or "site").strip()
    sid = re.sub(r"[^a-zA-Z0-9_-]", "", sid)[:32]
    ts = (bundle.get("exported_at") or "")[:10]  # YYYY-MM-DD
    return f"{sid}-{ts}.bdtpl"


# ─── Import ───────────────────────────────────────────────────────────

def bundle_from_bytes(data: bytes) -> dict:
    """Parse on-wire bytes back to a bundle dict. Raises ValueError
    on malformed data."""
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ValueError(f"bundle parse failed: {e}")


def validate_bundle(bundle: dict) -> dict:
    """Sanity check shape + version. Returns
        {ok, schema, site_id, errors: [str]}.
    Doesn't verify signatures — that's verify_signature's job."""
    out = {"ok": True, "errors": []}
    if not isinstance(bundle, dict):
        return {"ok": False, "errors": ["bundle is not a dict"]}
    if bundle.get("schema") != SCHEMA_VERSION:
        out["errors"].append(
            f"unknown schema: {bundle.get('schema')!r} "
            f"(expected {SCHEMA_VERSION})")
    if not bundle.get("site_id") or not isinstance(bundle["site_id"], str):
        out["errors"].append("site_id missing or not a string")
    if not isinstance(bundle.get("template"), dict):
        out["errors"].append("template field missing or not a dict")
    if out["errors"]:
        out["ok"] = False
    out["schema"] = bundle.get("schema", "")
    out["site_id"] = bundle.get("site_id", "")
    return out


def import_template(
    bundle: dict,
    *,
    target_site_id: Optional[str] = None,
    verify_with: Optional[str] = None,
) -> dict:
    """Validate + (optionally) verify-signature + emit importable
    config. The returned dict shape:
      {
        ok: bool,
        site_id: str,             # the target id (override or original)
        config: dict,             # ready to drop into s_cfg
        metadata: dict,           # from the bundle
        signature_check: dict,    # output of verify_signature
        warnings: [str]
      }
    Caller integrates `config` into BD's site dict and saves."""
    out = {"ok": False, "warnings": []}
    v = validate_bundle(bundle)
    if not v["ok"]:
        return {**out, "errors": v["errors"]}
    sig_check = verify_signature(bundle, secret=verify_with or "")
    if not sig_check["ok"]:
        return {**out, "errors": ["signature verification failed"],
                "signature_check": sig_check}
    cfg = dict(bundle["template"])
    sid = target_site_id or bundle["site_id"]
    # Warn if any required-but-empty fields
    for path_key in _LOCAL_PATH_KEYS:
        if path_key in cfg and not cfg[path_key]:
            out["warnings"].append(
                f"{path_key} is empty — set it before enabling the site")
    # Reset operator-specific runtime state that may have leaked
    cfg.pop("_account_pool", None)
    return {
        "ok": True,
        "site_id": sid,
        "config": cfg,
        "metadata": bundle.get("metadata", {}),
        "signature_check": sig_check,
        "warnings": out["warnings"],
    }


def preview_import_template(
    bundle: dict,
    *,
    target_site_id: Optional[str] = None,
    verify_with: Optional[str] = None,
    existing_site_ids=(),
) -> dict:
    """Read-only preview mirror of :func:`import_template`.

    Validates the bundle, verifies its signature, classifies the target as
    ``new`` or ``changed`` (relative to ``existing_site_ids``), and returns a
    REDACTED config preview plus the list of secret keys that were omitted —
    WITHOUT emitting a config a caller would persist. The shape lines up with
    the user-templates import preview (status + secrets_omitted) so the SPA can
    render both with one preview component.

      {ok, site_id, status, config_preview, secrets_omitted, signature_check,
       metadata, warnings, errors}
    """
    out = {"ok": False, "site_id": target_site_id or "", "status": None,
           "config_preview": {}, "secrets_omitted": [], "metadata": {},
           "warnings": [], "errors": []}
    v = validate_bundle(bundle)
    out["site_id"] = target_site_id or v.get("site_id") or ""
    if not v["ok"]:
        out["errors"] = v["errors"]
        return out
    sig_check = verify_signature(bundle, secret=verify_with or "")
    out["signature_check"] = sig_check
    if not sig_check["ok"]:
        out["errors"] = ["signature verification failed"]
        return out
    template = bundle.get("template") or {}
    sid = target_site_id or bundle["site_id"]
    out["site_id"] = sid
    out["status"] = "changed" if sid in set(existing_site_ids) else "new"
    out["secrets_omitted"] = sorted(k for k in template if _export_key_is_secret(k))
    out["config_preview"] = _redact(template)
    out["metadata"] = bundle.get("metadata", {})
    for path_key in _LOCAL_PATH_KEYS:
        if path_key in template and not template[path_key]:
            out["warnings"].append(
                f"{path_key} is empty — set it before enabling the site")
    out["ok"] = True
    return out
