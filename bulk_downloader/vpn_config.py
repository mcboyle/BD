"""v3.43.60: VPN config persistence.

Saves and loads tunnel configurations from disk. Secrets are stored by
reference (@cred:<key>) — the actual private keys, passwords, and account
numbers live in the existing secrets_store (v3.43.14). The on-disk
tunnels.json file holds only:

  - Tunnel id, name, provider, backend, location, enabled flag
  - Non-secret config fields (address, dns, endpoint, allowed_ips, ...)
  - Reference markers like "@cred:tun-abc123:private_key" for secrets

# File location

  Linux/macOS: ~/.config/bulk-downloader/vpn/tunnels.json
  Windows:     %APPDATA%\\BulkDownloader\\vpn\\tunnels.json

Overridable via BD_VPN_CONFIG_PATH for tests/Docker.

# Schema

{
  "schema_version": 1,
  "global_settings": {
    "leak_test_interval_s": 1800,
    "kill_switch_auto_recover": true,
    "system_killswitch_default": false,
    "system_killswitch_allow_ports": {...}   # if user customized
  },
  "tunnels": [
    {
      "tunnel_id": "tun-abc123",
      "name": "Mullvad NYC",
      "provider": "mullvad",
      "backend": "wireguard",
      "location": "us-nyc-wg-101",
      "enabled": true,
      "config": {
        "private_key": "@cred:tun-abc123:private_key",
        "address": "10.66.123.45/32",
        "peer_public_key": "<base64>",
        "endpoint": "1.2.3.4:51820",
        "dns": "10.64.0.1"
      },
      "extra": {
        "account_number": "@cred:tun-abc123:account_number"
      }
    }
  ]
}

# Public API

  load() -> dict                   - Read tunnels.json, register tunnels with vpn.py
  save() -> None                   - Atomic write of current state to disk
  resolve_secrets(config) -> dict  - Replace @cred:* refs with real values for backend.start()
  store_secrets(tunnel_id, config) -> dict
                                   - Move secret values into secrets_store, return config
                                     with @cred:* refs
  get_global_settings() -> dict
  update_global_settings(**fields) -> dict

# Threading

A single module-level lock guards the in-memory copy. Disk writes are
atomic (write-then-rename).
"""
# Load-bearing invariants tagged inline as # INV-<ID>; see DANGER_MAP.md.
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional


SCHEMA_VERSION = 1

# Substring-match for which config keys are considered secret. Same hints
# REDACT-SOT: the config-secret decision routes through the shared SoT
# (site_editor.is_secret_config_key) UNIONed with the VPN-domain conservative
# hints. Option A (documented policy): vpn deliberately over-redacts anything
# containing these bare hints -- false positives are cheaper than leaking key
# material. ``account_number`` is a VPN credential (e.g. Mullvad IDs). The shared
# floor adds the leak-closure the old tuple missed (cookies / passphrase); no
# coverage is removed.
def _vpn_key_is_secret(k) -> bool:
    from .site_editor import is_secret_config_key  # lazy: no static import edge
    # v3.66.773: derive the VPN secret-key hints from the canonical source in vpn
    # (uses the already-existing vpn_config->vpn edge; lazy, so no import cycle at
    # load). vpn_config used to keep its own copy, which drifted from vpn's.
    from .vpn import _SECRET_KEY_HINTS
    kl = str(k).lower()
    return is_secret_config_key(str(k)) or any(h in kl for h in _SECRET_KEY_HINTS)

_CRED_PREFIX = "@cred:"


# ─── Config file location ───────────────────────────────────────────

def _default_config_dir() -> Path:
    override = os.environ.get("BD_VPN_CONFIG_PATH")
    if override:
        return Path(override).parent
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "BulkDownloader" / "vpn"
    return Path(os.path.expanduser("~")) / ".config" / "bulk-downloader" / "vpn"


def _config_path() -> Path:
    override = os.environ.get("BD_VPN_CONFIG_PATH")
    if override:
        return Path(override)
    return _default_config_dir() / "tunnels.json"


# ─── Default global settings ────────────────────────────────────────

_DEFAULT_GLOBAL_SETTINGS: dict[str, Any] = {
    "leak_test_interval_s": 1800,
    "kill_switch_auto_recover": True,
    "system_killswitch_default": False,
    "system_killswitch_allow_ports": None,  # None = use vpn_kill_switch_system.DEFAULT_ALLOWLIST
    "enable_per_site_tunnels": True,
    "max_concurrent_tunnels": 32,
}


# ─── State ──────────────────────────────────────────────────────────

_state: dict[str, Any] = {
    "schema_version": SCHEMA_VERSION,
    "global_settings": dict(_DEFAULT_GLOBAL_SETTINGS),
    "tunnels": [],
}
_lock = threading.RLock()
_loaded = False

# Records load() could not validate, kept as (original_index, raw_dict) so
# save() can write them back exactly where they were. They are deliberately
# NOT in _state["tunnels"]: nothing registers, starts or routes them. They
# exist only so BD never silently deletes a record the operator wrote.
_quarantined: list[tuple[int, Any]] = []

# One dict per quarantined record: {index, tunnel_id, error, path}. Surfaced
# by load_errors(), by register_loaded_tunnels() (so app.py prints it at boot)
# and by /api/vpn/status (so the fault is visible over HTTP rather than only
# in a single boot-time stderr line).
_load_errors: list[dict] = []


# ─── Public API ─────────────────────────────────────────────────────

def load() -> dict:
    """Read tunnels.json from disk into memory. Idempotent.

    Returns the loaded state. If the file doesn't exist, initializes
    with defaults and returns those. Tunnels are NOT auto-registered
    with vpn.py here — call register_loaded_tunnels() for that, so
    the caller controls when registration happens (e.g. only after the
    backends and providers are loaded).

    A record that fails validation is QUARANTINED, not fatal. This used to be
    a list comprehension, so the first bad record aborted the whole file: one
    tunnel missing `name` made every other tunnel invisible, left _state
    half-mutated (global_settings assigned, tunnels not) and left _loaded
    False. Worse, vpn_runtime.init() calls load() before
    vpn_kill_switch.set_auto_recover(), so the raise silently discarded the
    operator's kill-switch preference too.

    Quarantine is deliberately NOT repair. BD does not invent a missing
    `name` — that would be silently rewriting operator config — and it does
    not drop the record either, since save() writes quarantined entries back
    verbatim. It isolates the record and reports it via load_errors().
    """
    global _loaded
    path = _config_path()
    with _lock:
        if not path.exists():
            _loaded = True
            _quarantined.clear()
            _load_errors.clear()
            return dict(_state)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            sys.stderr.write(f"[vpn-config] could not read {path}: {e}\n")
            _loaded = True
            return dict(_state)
        _state["schema_version"] = int(data.get("schema_version", SCHEMA_VERSION))
        gs = data.get("global_settings", {}) or {}
        # Merge with defaults so new settings get sensible values
        merged = dict(_DEFAULT_GLOBAL_SETTINGS)
        for k, v in gs.items():
            merged[k] = v
        _state["global_settings"] = merged
        tunnels = data.get("tunnels", []) or []
        good: list[dict] = []
        bad: list[tuple[int, Any]] = []
        errors: list[dict] = []
        for index, raw in enumerate(tunnels):
            if not isinstance(raw, dict):
                bad.append((index, raw))
                errors.append({
                    "index": index, "tunnel_id": None, "path": str(path),
                    "error": f"tunnel entry is {type(raw).__name__}, expected object",
                })
                continue
            try:
                good.append(_validate_tunnel_dict(raw))
            except ValueError as e:
                bad.append((index, raw))
                # Name the record and the file. The old message said only
                # "tunnel config missing required field: name", which told the
                # operator neither which tunnel nor which file to edit.
                errors.append({
                    "index": index,
                    "tunnel_id": raw.get("tunnel_id"),
                    "path": str(path),
                    "error": str(e),
                })
        _state["tunnels"] = good
        _quarantined[:] = bad
        _load_errors[:] = errors
        for err in errors:
            sys.stderr.write(
                f"[vpn-config] quarantined tunnel #{err['index']} "
                f"({err['tunnel_id'] or 'no tunnel_id'}) in {path}: "
                f"{err['error']} -- it is left on disk untouched and is NOT "
                f"registered; fix it in the raw store editor\n")
        _loaded = True
        return dict(_state)


def load_errors() -> list[dict]:
    """Records the last load() could not validate, as
    [{index, tunnel_id, path, error}, ...]. Empty when the stored config is
    clean.

    Callers use this to distinguish "no tunnels configured" from "the tunnel
    config did not load" — two states that were previously indistinguishable
    from outside the process, which is why a broken tunnels.json read as an
    empty one.
    """
    with _lock:
        return [dict(e) for e in _load_errors]


def save() -> None:
    """Write current state to tunnels.json atomically.

    Quarantined records are re-emitted verbatim at their original index.
    Without that, the first save() after a quarantining load() would write a
    tunnel list that silently omits them — BD deleting operator config it
    merely failed to parse. Quarantine only holds a record back from being
    REGISTERED; it never removes it from disk.
    """
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tunnels = list(_state["tunnels"])
        for index, raw in _quarantined:
            tunnels.insert(min(index, len(tunnels)), raw)
        snapshot = {
            "schema_version": _state["schema_version"],
            "global_settings": dict(_state["global_settings"]),
            "tunnels": tunnels,
            "_saved_at": time.time(),
        }
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(snapshot, indent=2, sort_keys=False), encoding="utf-8")
        # Atomic rename. On Windows, os.replace handles the cross-FS case too.
        os.replace(tmp, path)
    except OSError as e:
        sys.stderr.write(f"[vpn-config] could not save {path}: {e}\n")
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def list_tunnel_configs() -> list[dict]:
    """Return shallow copies of stored tunnel config dicts."""
    with _lock:
        return [dict(t) for t in _state["tunnels"]]


def get_tunnel_config(tunnel_id: str) -> Optional[dict]:
    with _lock:
        for t in _state["tunnels"]:
            if t.get("tunnel_id") == tunnel_id:
                return dict(t)
    return None


def add_tunnel_config(tunnel_dict: dict) -> dict:
    """Add (or replace) a tunnel config in memory + on disk."""
    cfg = _validate_tunnel_dict(tunnel_dict)
    with _lock:
        existing_idx = None
        for i, t in enumerate(_state["tunnels"]):
            if t["tunnel_id"] == cfg["tunnel_id"]:
                existing_idx = i
                break
        if existing_idx is not None:
            _state["tunnels"][existing_idx] = cfg
        else:
            _state["tunnels"].append(cfg)
    save()
    return cfg


def remove_tunnel_config(tunnel_id: str) -> bool:
    """Remove a tunnel and every secret keyed under its id.

    Row 520: the purge runs BEFORE the tunnel is dropped from ``_state``, so a
    vault that cannot be enumerated leaves the tunnel and its ``@cred:``
    references in place instead of orphaning the entries behind a removal that
    reported success. Raises the store's own named refusal in that case; the
    caller and the DELETE endpoint report unpurgeable rather than removed.
    """
    with _lock:
        present = any(t.get("tunnel_id") == tunnel_id for t in _state["tunnels"])
    if not present:
        return False
    # Purge first: this may raise, and it must raise before any mutation.
    _purge_secrets_for_tunnel(tunnel_id)
    with _lock:
        before = len(_state["tunnels"])
        _state["tunnels"] = [t for t in _state["tunnels"] if t.get("tunnel_id") != tunnel_id]
        removed = len(_state["tunnels"]) < before
    if removed:
        save()
    return removed


def update_tunnel_config(tunnel_id: str, **fields: Any) -> Optional[dict]:
    with _lock:
        for t in _state["tunnels"]:
            if t.get("tunnel_id") == tunnel_id:
                for k, v in fields.items():
                    t[k] = v
                t = _validate_tunnel_dict(t)
                save()
                return dict(t)
    return None


def get_global_settings() -> dict:
    with _lock:
        return dict(_state["global_settings"])


def tunnel_ids_with_secrets() -> set:
    """tunnel_ids that currently have at least one entry in secrets_store
    (cred keys are stored as f"{tunnel_id}:{field}"). Used by the raw store
    editor's R1 guard: a tunnel_id change/removal for an id in this set would
    orphan secrets, so the raw editor blocks it (rename via rekey_tunnel)."""
    from . import secrets_store
    # Row 520 / A7 self-audit: this FAILS CLOSED, exactly like its sibling
    # _purge_secrets_for_tunnel. A bare ``except Exception`` turned an
    # unreadable vault into an EMPTY SET, and the R1 guard reads an empty set
    # as "no tunnel owns secrets, nothing to protect" -- so the single
    # condition under which orphaning is most likely also DISABLED the guard
    # that exists to prevent it. An inventory that cannot be measured is not
    # an empty inventory (CLAUDE.md A7); the caller must decide, not this
    # function silently.
    backend = secrets_store.get_backend()
    if backend is None:
        raise secrets_store.SecretsUnreadableError(
            "no credential backend is available, so tunnel secret ownership "
            "cannot be enumerated"
        )
    out: set = set()
    for k in (backend.list_keys() or []):
        if ":" in k:
            out.add(k.split(":", 1)[0])
    return out


def rekey_tunnel(old_id: str, new_id: str) -> bool:
    """Atomically rename a tunnel's primary key old_id -> new_id WITHOUT
    orphaning its secrets. Steps: (1) copy each secrets_store entry
    f"{old_id}:*" to f"{new_id}:*" then delete the old; (2) rewrite the
    embedded @cred:{old_id}:* references in the tunnel's fields to
    @cred:{new_id}:*; (3) set the tunnel_id field and persist.

    Returns True when a tunnel named old_id existed and was renamed; False
    otherwise. tunnel_id is a primary + secrets foreign key, so this is the
    ONLY safe rename path (a raw-editor tunnel_id edit is blocked by R1)."""
    if not old_id or not new_id or old_id == new_id:
        return False
    with _lock:
        target = None
        for t in _state["tunnels"]:
            if t.get("tunnel_id") == old_id:
                target = t
                break
        if target is None:
            return False
        if any(t.get("tunnel_id") == new_id for t in _state["tunnels"]):
            raise ValueError(f"tunnel_id {new_id!r} already exists")

        # 1. move secrets old_id:* -> new_id:* (copy then delete; secrets_store
        #    has no rename primitive).
        try:
            from . import secrets_store
            backend = secrets_store.get_backend()
            if backend is not None:
                for k in list(backend.list_keys() or []):
                    if k.startswith(f"{old_id}:"):
                        suffix = k[len(old_id) + 1:]
                        val = backend.get(k)
                        if val is not None:
                            backend.set(f"{new_id}:{suffix}", val)
                        backend.delete(k)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"[vpn-config] rekey secrets move failed: {e}\n")
            raise

        # 2. rewrite embedded @cred:old_id:* refs -> @cred:new_id:*
        old_ref = f"{_CRED_PREFIX}{old_id}:"
        new_ref = f"{_CRED_PREFIX}{new_id}:"

        def _rewrite(obj):
            if isinstance(obj, dict):
                return {k: _rewrite(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_rewrite(v) for v in obj]
            if isinstance(obj, str) and obj.startswith(old_ref):
                return new_ref + obj[len(old_ref):]
            return obj

        rewritten = _rewrite(target)
        rewritten["tunnel_id"] = new_id
        # replace in place
        _state["tunnels"] = [rewritten if t is target else t
                             for t in _state["tunnels"]]
    save()
    return True


def update_global_settings(**fields: Any) -> dict:
    with _lock:
        for k, v in fields.items():
            if k in _DEFAULT_GLOBAL_SETTINGS:
                _state["global_settings"][k] = v
            else:
                sys.stderr.write(f"[vpn-config] ignored unknown global setting: {k}\n")
    save()
    return get_global_settings()


# ─── Secret resolution / storage ────────────────────────────────────

def store_secrets(tunnel_id: str, config: dict) -> dict:
    """For each secret-looking field in `config`, move the value into
    secrets_store and replace it with a @cred:<key> reference. Returns
    the modified config dict (caller still owns it)."""
    if not isinstance(config, dict):
        return config
    out = {}
    for k, v in config.items():
        is_secret = _vpn_key_is_secret(k)
        if is_secret and isinstance(v, str) and v and not v.startswith(_CRED_PREFIX):
            cred_key = f"{tunnel_id}:{k}"
            try:
                from . import secrets_store
                backend = secrets_store.get_backend()
                # Audit 2026-05 (Phase 3): only indirect through @cred: when
                # the backend has real storage. PlaintextBackend.set() is a
                # no-op and get() returns None — indirecting through it would
                # turn the password into a dangling reference that resolves
                # to an empty string. Keep inline plaintext in that case.
                if backend is not None and getattr(backend, "name", "") != "plaintext":
                    backend.set(cred_key, v)
                    out[k] = f"{_CRED_PREFIX}{cred_key}"
                    continue
            except Exception as e:
                sys.stderr.write(f"[vpn-config] could not store secret {cred_key}: {e}\n")
            # Fallback: keep plaintext (legacy behavior, same as v3.43.13 sites)
            out[k] = v
        elif isinstance(v, dict):
            out[k] = store_secrets(tunnel_id, v)
        else:
            out[k] = v
    return out


def resolve_secrets(config: dict) -> dict:  # INV-006
    """Inverse of store_secrets: replace @cred:<key> refs with the actual
    value from secrets_store. Used at tunnel-start time only — the result
    is passed to the backend and never persisted."""
    if not isinstance(config, dict):
        return config
    out = {}
    for k, v in config.items():
        if isinstance(v, str) and v.startswith(_CRED_PREFIX):
            cred_key = v[len(_CRED_PREFIX):]
            try:
                from . import secrets_store
                backend = secrets_store.get_backend()
                if backend is not None:
                    resolved = backend.get(cred_key)
                    out[k] = resolved if resolved is not None else ""
                    continue
            except Exception as e:
                sys.stderr.write(f"[vpn-config] could not resolve {cred_key}: {e}\n")
            out[k] = ""
        elif isinstance(v, dict):
            out[k] = resolve_secrets(v)
        else:
            out[k] = v
    return out


def credentials_resolver_for_vpn(credentials_ref: str) -> dict:
    """Helper for VPNManager.set_credentials_resolver. Given a credentials_ref
    (which for our model equals the tunnel_id), look up the tunnel config and
    return resolved credentials."""
    # credentials_ref convention for this module: it's the tunnel_id itself.
    cfg = get_tunnel_config(credentials_ref)
    if cfg is None:
        return {}
    # Combine top-level config + extra into a flat dict; resolve secrets.
    merged = {}
    merged.update(cfg.get("extra", {}) or {})
    merged.update(cfg.get("config", {}) or {})
    return resolve_secrets(merged)


def _purge_secrets_for_tunnel(tunnel_id: str) -> int:
    """Remove all secrets_store entries for tunnel_id. Called on remove.

    Row 520: this FAILS CLOSED. Two stacked bare ``except Exception`` handlers
    used to turn an unmeasurable inventory into an empty one -- the inner
    substituted ``keys = []`` for the ``SecretsUnreadableError`` a damaged
    vault raises, so the delete loop ran 0 times, and the outer wrote one
    stderr line, so no purge failure reached a caller by any path. The saved
    config no longer carries the ``@cred:`` references to those entries, so a
    silent failure orphans them with nothing left pointing at them.

    The sibling :func:`rekey_tunnel` already re-raises its secrets failure;
    this caller now matches it. CLAUDE.md A7: an inventory that cannot be
    measured is not an empty inventory.

    Returns the number of entries deleted. Raises the store's own named
    refusal when the inventory could not be read or a delete did not land.
    """
    from . import secrets_store
    backend = secrets_store.get_backend()
    if backend is None:
        raise secrets_store.SecretsUnreadableError(
            f"no credential backend is available, so the secrets for "
            f"{tunnel_id!r} cannot be enumerated or purged"
        )
    keys = list(backend.list_keys() or [])
    deleted = 0
    for k in keys:
        if k.startswith(f"{tunnel_id}:"):
            backend.delete(k)
            deleted += 1
    return deleted


# ─── Validation / migration ─────────────────────────────────────────

_REQUIRED_TUNNEL_FIELDS = ("tunnel_id", "name", "provider", "backend")
_OPTIONAL_TUNNEL_FIELDS = ("location", "enabled", "config", "extra")


def _validate_tunnel_dict(d: dict) -> dict:
    """Apply defaults + drop unexpected keys."""
    out = {}
    for f in _REQUIRED_TUNNEL_FIELDS:
        if f not in d:
            raise ValueError(f"tunnel config missing required field: {f}")
        out[f] = d[f]
    # B19 (v3.66.43): the secrets-store key namespace is
    # "{tunnel_id}:{config_key}", so a ':' in tunnel_id lets one tunnel's
    # purge prefix-match another's secrets. Reject it at validate time —
    # the collision becomes impossible by construction.
    tid = out["tunnel_id"]
    if not isinstance(tid, str) or not tid:
        raise ValueError(f"tunnel_id must be a non-empty string, got {tid!r}")
    if ":" in tid:
        raise ValueError(
            f"tunnel_id must not contain ':' (got {tid!r}); the "
            f"secrets-store key namespace uses ':' as a separator")
    for f in _OPTIONAL_TUNNEL_FIELDS:
        if f in d:
            out[f] = d[f]
    out.setdefault("location", "")
    out.setdefault("enabled", True)
    out.setdefault("config", {})
    out.setdefault("extra", {})
    return out


# ─── Wire-up to VPNManager ──────────────────────────────────────────

def register_loaded_tunnels() -> tuple[int, list[str]]:
    """Register every loaded tunnel config with vpn.py. Idempotent.

    Returns (count_registered, errors). Errors are formatted strings.
    """
    from . import vpn
    # Load faults come first: app.py prints this list at boot, and a
    # quarantined record that never reached registration would otherwise be
    # invisible there.
    errors: list[str] = []
    for e in load_errors():
        who = e["tunnel_id"] or "tunnel #{}".format(e["index"])
        errors.append(
            "{}: {} (quarantined from {}; not registered)".format(
                who, e["error"], e["path"]))
    count = 0
    with _lock:
        tunnels = list(_state["tunnels"])
    for t in tunnels:
        if not t.get("enabled", True):
            continue
        # Skip if already registered.
        if vpn.get_tunnel(t["tunnel_id"]) is not None:
            continue
        try:
            vpn.register_tunnel(
                name=t.get("name", t["tunnel_id"]),
                provider=t["provider"],
                backend=t["backend"],
                location=t.get("location"),
                config=t.get("config", {}),
                tunnel_id=t["tunnel_id"],
            )
            count += 1
        except Exception as e:
            errors.append(f"{t['tunnel_id']}: {e}")
    return count, errors


def install_credentials_resolver() -> None:
    """Hook our resolver into vpn.py. vpn.py's runtime config is the
    redacted form; this resolver provides the live, secrets-included
    version that backends need at start() time.

    vpn.py doesn't ship with a credentials_resolver injection point yet
    (the existing code reads tunnel.config directly), so this function
    is a NO-OP for v3.43.60 — the secrets are kept on the tunnel.config
    dict directly. If a future version adds the injection point, this
    function gets implemented."""
    # No-op for now. See module docstring for the wider design.
    pass


# ─── Test helpers ───────────────────────────────────────────────────

def _reset_for_tests() -> None:
    global _loaded
    with _lock:
        _state["schema_version"] = SCHEMA_VERSION
        _state["global_settings"] = dict(_DEFAULT_GLOBAL_SETTINGS)
        _state["tunnels"] = []
        _quarantined.clear()
        _load_errors.clear()
    _loaded = False


__all__ = [
    "SCHEMA_VERSION",
    "load", "save", "load_errors",
    "list_tunnel_configs", "get_tunnel_config",
    "add_tunnel_config", "remove_tunnel_config", "update_tunnel_config",
    "get_global_settings", "update_global_settings",
    "tunnel_ids_with_secrets", "rekey_tunnel",
    "store_secrets", "resolve_secrets",
    "credentials_resolver_for_vpn",
    "register_loaded_tunnels", "install_credentials_resolver",
]
