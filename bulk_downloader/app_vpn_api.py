"""v3.43.60: Flask routes for the VPN feature.

23 endpoints split across:
  - Tunnels:        list / create / update / delete / start / stop / cycle
  - Providers:      list / test creds / list locations
  - Leak tests:     run / history / WebRTC result posting
  - Kill switch:    state / clear / trigger
  - System KS:      plan / apply / commit / revert / availability
  - Settings:       get / update global settings
  - Diagnostics:    backend availability

Wired into the main Flask app with:

    from .app_vpn_api import register_routes
    register_routes(app)

# Auth / CSRF

This module assumes app.py's existing CSRF mechanism is enforced upstream.
POST/PUT/DELETE handlers don't re-validate the CSRF token; that's the
main app's responsibility (Flask request hook).

# Response shape

Every endpoint returns JSON via jsonify(). Success: 200 with the requested
resource. Failure: 4xx/5xx with {"ok": False, "error": "<message>"}.
"""
from __future__ import annotations

import contextlib
import sys
import threading

try:
    from flask import Blueprint, request, jsonify
except ImportError:  # pragma: no cover — Flask must be installed in production
    Blueprint = None  # type: ignore
    request = None  # type: ignore
    jsonify = None  # type: ignore


# Single blueprint so we can register everything at once.
vpn_bp = Blueprint("vpn_api", __name__) if Blueprint else None


# ─── Helpers ────────────────────────────────────────────────────────

def _err(msg: str, status: int = 400):
    return jsonify({"ok": False, "error": msg}), status


def _ok(payload=None):
    if payload is None:
        return jsonify({"ok": True})
    if isinstance(payload, dict):
        return jsonify({"ok": True, **payload})
    return jsonify({"ok": True, "data": payload})


def _json_object():
    """(body, None) for a JSON object or an empty body; (None, 400) otherwise.

    dva K7: ``request.get_json(silent=True) or {}`` read ``[]``, ``null`` and
    unparseable JSON as ``{}``, so a malformed write was answered 200.
    """
    data = request.get_json(silent=True)
    if data is None and not request.get_data():
        return {}, None
    if not isinstance(data, dict):
        return None, _err("body must be a JSON object")
    return data, None


_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def vpn_admin_gate():
    """dva U20: with multi-user on, a VPN mutation (tunnels, settings, kill
    switch, system kill switch, leak tests, auto_blacklist) needs the admin
    role, as the /api/auth/users* routes do; reads stay open. Default-no-op:
    with multi-user off (the single-operator default) nothing changes. Fails
    CLOSED: a store/gate error refuses (403); it never skips the role check.
    Registered on this blueprint and on app_vpn's."""
    if request.method not in _MUTATING:
        return None
    try:
        from . import user_accounts as _ua
        if not _ua.multi_user_enabled():
            return None
        who = _ua.current_user_from_cookie(request.cookies.get("bd_user", ""))
        if who and who.get("role") == "admin":
            return None
    except Exception:
        return _err("VPN role check failed; refusing", 403)
    return _err("admin role required", 403)


if vpn_bp is not None:
    vpn_bp.before_request(vpn_admin_gate)


# vpn._redact_config's mask: what GET shows for every secret config value.
_REDACTED = "***"


def _unmask_config(new, old):
    """dva V8: a secret whose incoming value is the GET mask was echoed back
    unchanged, not edited -- keep the stored value instead of saving '***'."""
    from . import vpn_config
    old = old if isinstance(old, dict) else {}
    out = {}
    for k, v in new.items():
        if v == _REDACTED and vpn_config._vpn_key_is_secret(k):
            if k in old:
                out[k] = old[k]
            continue
        out[k] = _unmask_config(v, old.get(k)) if isinstance(v, dict) else v
    return out


def _saved_only_tunnel(tunnel_id):
    """dva V5: a tunnel saved enabled:false is not registered after a reload,
    but it is still the operator's record. Its redacted saved form, or None."""
    from . import vpn, vpn_config
    cfg = vpn_config.get_tunnel_config(tunnel_id)
    if cfg is None:
        return None
    enabled = cfg.get("enabled", True)
    return {
        "tunnel_id": cfg["tunnel_id"], "name": cfg.get("name"),
        "provider": cfg.get("provider"), "backend": cfg.get("backend"),
        "location": cfg.get("location"), "enabled": enabled,
        "state": "disabled" if enabled is False else "not registered",
        "config": vpn._redact_config(cfg.get("config") or {}),
    }


def _drop_new_tunnel(vpn, vpn_config, tid) -> None:
    """Undo a half-made create: the registration and any secret already
    stored under the new id (dva V3: a refused id left its secret behind)."""
    try:
        vpn.unregister_tunnel(tid)
    except Exception as e:
        sys.stderr.write(f"[vpn-api] rollback unregister failed: {e}\n")
    try:
        vpn_config._purge_secrets_for_tunnel(tid)
    except Exception as e:
        sys.stderr.write(f"[vpn-api] rollback secret purge failed for {tid} "
                         f"({type(e).__name__})\n")


# ─── Tunnel routes ──────────────────────────────────────────────────

@vpn_bp.route("/api/vpn/status", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_status():
    """Overall VPN status: tunnels, kill switch state, registered providers.

    `config_load_errors` reports tunnel records that vpn_config quarantined
    at load time. Without it, a tunnels.json that failed to parse is
    indistinguishable over the API from one with no tunnels in it: both show
    an empty `tunnels` list. That ambiguity is why a broken VPN config read as
    "nothing configured" everywhere outside the process, and it is why any
    client that writes tunnels must check this first -- vpn_config.save()
    serialises the in-memory list, so writing while records are quarantined is
    safe only because save() re-emits them.
    """
    from . import (vpn, vpn_config, vpn_kill_switch, vpn_kill_switch_system,
                   vpn_providers)
    tunnels = [t.to_dict() for t in vpn.list_tunnels()]
    kill_states = vpn_kill_switch.list_kill_states()
    sys_ok, sys_why = vpn_kill_switch_system.available()
    return jsonify({
        "ok": True,
        "tunnels": tunnels,
        "kill_states": kill_states,
        "config_load_errors": vpn_config.load_errors(),
        "providers": vpn_providers.list_providers(),
        "system_killswitch_available": sys_ok,
        "system_killswitch_reason": sys_why,
        "system_killswitch_active": vpn_kill_switch_system.list_active(),
    })


@vpn_bp.route("/api/vpn/tunnels", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_tunnels_list():
    from . import vpn
    return _ok({"tunnels": [t.to_dict() for t in vpn.list_tunnels()]})


@vpn_bp.route("/api/vpn/tunnels", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnels_create():
    from . import vpn, vpn_config
    data, bad = _json_object()
    if bad:
        return bad
    required = ("name", "provider", "backend")
    missing = [k for k in required if not data.get(k)]
    if missing:
        return _err(f"missing fields: {missing}")
    # dva V3/V4/V7/V9/V14: every field is checked before anything is
    # registered or written, so a refusal leaves no secret behind.
    config, extra = data.get("config", {}), data.get("extra", {})
    try:
        if data.get("tunnel_id") is not None:
            vpn_config.check_new_tunnel_id(data["tunnel_id"])
        vpn_config.check_tunnel_name(data["name"])
        if not isinstance(config, dict) or not isinstance(extra, dict):
            raise ValueError("config and extra must be JSON objects")
        if not isinstance(data.get("enabled", True), bool):
            raise ValueError("enabled must be true or false")
    except ValueError as e:
        return _err(str(e))
    asked = data.get("tunnel_id")
    if asked is not None and (vpn.get_tunnel(asked) is not None
                              or vpn_config.get_tunnel_config(asked) is not None):
        # dva V6: a saved (possibly disabled, unregistered) record is not
        # silently replaced by a create under the same id.
        return _err(f"tunnel {asked!r} already exists", 409)
    try:
        tid = vpn.register_tunnel(
            name=data["name"], provider=data["provider"], backend=data["backend"],
            location=data.get("location"), config=config,
            tunnel_id=asked,
        )
    except ValueError as e:
        return _err(str(e))
    # Persist to disk via vpn_config.
    # Audit 2026-05 (Phase 5): roll back the in-memory registration if
    # disk persist fails. Previously, register succeeded + persist failed
    # left the tunnel only in memory; the API reported success but the
    # tunnel vanished on next restart. Now we unregister to keep the
    # two state stores consistent.
    try:
        stored = vpn_config.store_secrets(tid, config)
        # dva V4: extra carries credentials too (account_number); it goes
        # through the same @cred: indirection instead of plaintext to disk.
        stored_extra = vpn_config.store_secrets(tid, extra)
    except Exception as e:
        # O1826 C01: the secrets store refused (or the store raised): nothing
        # is written and the registration is undone. Log the type only.
        sys.stderr.write(f"[vpn-api] secret store refused for {tid} "
                         f"({type(e).__name__}); rolling back register\n")
        _drop_new_tunnel(vpn, vpn_config, tid)
        return _err(_secret_store_refusal(e), 503)
    try:
        vpn_config.add_tunnel_config({
            "tunnel_id": tid,
            "name": data["name"],
            "provider": data["provider"],
            "backend": data["backend"],
            "location": data.get("location", ""),
            "enabled": data.get("enabled", True),
            "config": stored,
            "extra": stored_extra,
        })
    except Exception as e:
        sys.stderr.write(f"[vpn-api] persist failed for {tid} "
                         f"({type(e).__name__}); rolling back register\n")
        _drop_new_tunnel(vpn, vpn_config, tid)
        if isinstance(e, ValueError):
            return _err(str(e))
        return _err("persist failed; the tunnel was not created", 500)
    return _ok({"tunnel_id": tid, "tunnel": vpn.get_tunnel(tid).to_dict()})


def _secret_store_refusal(e) -> str:
    """Fixed 503 text for a refused secret write; never the exception text."""
    from . import secrets_store
    if not isinstance(e, secrets_store.SecretsBackendUnavailableError):
        try:
            if not secrets_store.get_backend().is_unlocked():
                return "vault locked: unlock to save credentials"
        except Exception:
            pass
    return "the secret store refused the write; the tunnel was not created"


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_get(tunnel_id):
    from . import vpn
    t = vpn.get_tunnel(tunnel_id)
    if t is None:
        saved = _saved_only_tunnel(tunnel_id)
        if saved is None:
            return _err("no such tunnel", 404)
        return _ok({"tunnel": saved})
    return _ok({"tunnel": t.to_dict()})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>", methods=["PUT", "PATCH"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_update(tunnel_id):
    from . import vpn, vpn_config
    t = vpn.get_tunnel(tunnel_id)
    if t is None:
        return _err("no such tunnel", 404)
    data, bad = _json_object()
    if bad:
        return bad
    # dva V9/V10/V14: a field this route cannot apply, a non-object config
    # or an unusable name is refused instead of answered 200 and dropped.
    fixed = sorted(set(data) - {"name", "location", "config"})
    if fixed:
        return _err(f"fields not updatable here: {fixed}")
    try:
        if "name" in data:
            vpn_config.check_tunnel_name(data["name"])
        if data.get("location") is not None and not isinstance(data["location"], str):
            raise ValueError("location must be a string or null")
        if "config" in data and not isinstance(data["config"], dict):
            raise ValueError("config must be a JSON object")
    except ValueError as e:
        return _err(str(e))
    # O1826 C08: snapshot -> mutate -> persist -> rollback runs under one
    # per-tunnel lock, so a failed PUT's restore cannot discard a concurrent
    # PUT that persisted in between.
    with _tunnel_update_lock(tunnel_id):
        prior = (t.name, t.location, t.config)
        new_config = t.config
        if "config" in data:
            # Merge into existing config (so PATCH-style partial updates work).
            new_config = {**(t.config or {}),
                          **_unmask_config(data["config"], t.config)}
        # Every secret this PUT may overwrite is read back first; one that
        # cannot be read could not be rolled back, so refuse before any write.
        try:
            backend, prior_secrets = _snapshot_tunnel_secrets(
                vpn_config, tunnel_id, new_config)
        except _SecretSnapshotError as e:
            sys.stderr.write(f"[vpn-api] update refused for {tunnel_id}: {e}\n")
            return _err("a stored secret could not be read; "
                        "the tunnel was not changed", 503)
        prior_saved = vpn_config.get_tunnel_config(tunnel_id)
        for f in ("name", "location"):
            if f in data:
                setattr(t, f, data[f])
        t.config = new_config
        # Mirror the create path: a persist failure restores the live tunnel,
        # the saved config and the secret store, and returns 500 instead of
        # reporting an unsaved edit as ok.
        persisting = False
        try:
            stored = vpn_config.store_secrets(tunnel_id, t.config)
            persisting = True
            vpn_config.update_tunnel_config(tunnel_id,
                name=t.name, location=t.location, config=stored,
            )
        except Exception as e:
            # Exception text can carry the secret being saved: log the type
            # only, and answer with a fixed message.
            sys.stderr.write(f"[vpn-api] update persist failed for {tunnel_id} "
                             f"({type(e).__name__}); restoring tunnel\n")
            if persisting and prior_saved is not None:
                _restore_saved_tunnel(vpn_config, tunnel_id, prior_saved)
            _restore_tunnel_secrets(backend, prior_secrets)
            t.name, t.location, t.config = prior
            return _err("persist failed; the tunnel was not changed", 500)
    return _ok({"tunnel": t.to_dict()})


# tunnel_id -> [lock, holders]. An entry lives only while a PUT holds or
# waits on it, so the map is bounded by in-flight PUTs, not by every id
# ever edited (deleted tunnels leave nothing behind).
_tunnel_update_locks: dict = {}
_tunnel_update_locks_guard = threading.Lock()


@contextlib.contextmanager
def _tunnel_update_lock(tunnel_id):
    with _tunnel_update_locks_guard:
        entry = _tunnel_update_locks.get(tunnel_id)
        if entry is None:
            entry = _tunnel_update_locks[tunnel_id] = [threading.Lock(), 0]
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _tunnel_update_locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _tunnel_update_locks[tunnel_id]


class _SecretSnapshotError(Exception):
    """A secret the PUT may overwrite could not be read back first."""


def _secret_cred_keys(vpn_config, tunnel_id, config) -> list:
    """The secrets_store keys ``vpn_config.store_secrets(tunnel_id, config)``
    may write: same walk, same key shape."""
    keys = []
    for k, v in (config or {}).items():
        if isinstance(v, dict):
            keys += _secret_cred_keys(vpn_config, tunnel_id, v)
        elif (isinstance(v, str) and v and not v.startswith(vpn_config._CRED_PREFIX)
              and vpn_config._vpn_key_is_secret(k)):
            keys.append(f"{tunnel_id}:{k}")
    return keys


def _snapshot_tunnel_secrets(vpn_config, tunnel_id, config):
    """Return (backend, {key: prior value or None}) for every secret this
    update may overwrite. No backend, or the plaintext one, means
    store_secrets writes nothing (same checks). A backend that cannot be
    obtained, or a key that cannot be read, raises _SecretSnapshotError:
    the PUT is refused before any write."""
    keys = _secret_cred_keys(vpn_config, tunnel_id, config)
    if not keys:
        return None, {}
    try:
        from . import secrets_store
        backend = secrets_store.get_backend()
    except Exception as e:
        raise _SecretSnapshotError(
            f"secret store unavailable ({type(e).__name__})") from None
    if backend is None or getattr(backend, "name", "") == "plaintext":
        return None, {}
    prior = {}
    for key in keys:
        try:
            prior[key] = backend.get(key)
        except Exception as e:
            raise _SecretSnapshotError(
                f"could not snapshot secret {key} ({type(e).__name__})") from None
    return backend, prior


def _restore_tunnel_secrets(backend, prior) -> None:
    for key, value in prior.items():
        try:
            if value is None:
                backend.delete(key)
            else:
                backend.set(key, value)
        except Exception as e:
            sys.stderr.write(f"[vpn-api] secret rollback failed for {key} "
                             f"({type(e).__name__})\n")


def _restore_saved_tunnel(vpn_config, tunnel_id, prior_saved) -> None:
    # update_tunnel_config assigns the fields before it validates and saves,
    # so the in-memory config is restored even when the save fails again.
    try:
        vpn_config.update_tunnel_config(tunnel_id, **{
            f: prior_saved[f] for f in ("name", "location", "config")
            if f in prior_saved})
    except Exception as e:
        sys.stderr.write(f"[vpn-api] saved-config rollback for {tunnel_id} "
                         f"incomplete ({type(e).__name__})\n")


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>", methods=["DELETE"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_delete(tunnel_id):
    from . import vpn, vpn_config, vpn_kill_switch
    registered = vpn.get_tunnel(tunnel_id) is not None
    if not registered and vpn_config.get_tunnel_config(tunnel_id) is None:
        return _err("no such tunnel", 404)
    if registered:
        # dva V11: a tunnel whose backend did not stop is still up. Purging
        # its record and secrets then would orphan a live tunnel, so nothing
        # is removed; it is unregistered only after the record is gone.
        try:
            stopped = vpn.stop_tunnel(tunnel_id)
        except Exception as e:
            sys.stderr.write(f"[vpn-api] stop before delete raised for "
                             f"{tunnel_id} ({type(e).__name__})\n")
            stopped = False
        if stopped is False:
            return jsonify({
                "ok": False, "state": "stop_failed",
                "error": "the tunnel did not stop, so nothing was removed; "
                         "stop it, then delete again",
            }), 409
    try:
        vpn_config.remove_tunnel_config(tunnel_id)
    except Exception as e:
        # Row 520: the purge failed closed, so the tunnel and its @cred:
        # references are still persisted and nothing was orphaned. Name the
        # step that failed and carry the store's own words -- "removed" here
        # would be a success report over an unpurged vault.
        from . import secrets_store
        if isinstance(e, vpn_config.VPNConfigNotSavedError):
            sys.stderr.write(f"[vpn-api] delete persist failed for {tunnel_id} "
                             f"({type(e).__name__})\n")
            return _err("persist failed; the tunnel was not removed", 500)
        if not isinstance(e, secrets_store.SecretsIntegrityError):
            raise
        return jsonify({
            "ok": False,
            "state": "secrets_unpurgeable",
            "error": (
                f"the tunnel was NOT removed: its stored secrets could not be "
                f"enumerated or purged, so removing it would orphan them. "
                f"{str(e)[:240]}"
            ),
        }), 409
    if registered and hasattr(vpn, "unregister_tunnel"):
        vpn.unregister_tunnel(tunnel_id)
    vpn_kill_switch.forget_tunnel(tunnel_id)  # dva K1
    return _ok({"removed": tunnel_id})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/start", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_start(tunnel_id):
    from . import vpn
    if vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)  # dva V12
    try:
        ok = vpn.start_tunnel(tunnel_id)
    except Exception as e:
        # dva V13: backend exception text can carry config values; log the
        # type, answer with a fixed message.
        sys.stderr.write(f"[vpn-api] start raised for {tunnel_id} "
                         f"({type(e).__name__})\n")
        return _err(f"start failed ({type(e).__name__})", 500)
    t = vpn.get_tunnel(tunnel_id)
    return _ok({"started": ok, "tunnel": t.to_dict() if t else None})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/stop", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_stop(tunnel_id):
    from . import vpn
    if vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)  # dva V12
    ok = vpn.stop_tunnel(tunnel_id)
    return _ok({"stopped": ok})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/cycle", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_tunnel_cycle(tunnel_id):
    from . import vpn
    if vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)  # dva V12
    ok = vpn.cycle_tunnel(tunnel_id)
    return _ok({"cycled": ok, "tunnel": vpn.get_tunnel(tunnel_id).to_dict() if vpn.get_tunnel(tunnel_id) else None})


# ─── Provider routes ────────────────────────────────────────────────

@vpn_bp.route("/api/vpn/providers", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_providers_list():
    from . import vpn_providers
    return _ok({"providers": vpn_providers.list_providers()})


@vpn_bp.route("/api/vpn/providers/<provider_id>/test_credentials", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_provider_test_creds(provider_id):
    from . import vpn_providers
    mod = vpn_providers.get_provider(provider_id)
    if mod is None:
        return _err(f"unknown provider: {provider_id}", 404)
    data = request.get_json(silent=True) or {}
    try:
        ok, msg = mod.test_credentials(data)
    except Exception as e:
        return _provider_failure(provider_id, "test_credentials", e)
    return jsonify({"ok": True, "valid": ok, "message": msg})


def _provider_failure(provider_id, op, e):
    """dva K12: a provider exception can quote the submitted credentials
    (password included); log the type only and answer with a fixed 502."""
    sys.stderr.write(f"[vpn-api] provider {provider_id} {op} raised "
                     f"({type(e).__name__})\n")
    return _err(f"provider {op} failed ({type(e).__name__})", 502)


@vpn_bp.route("/api/vpn/providers/<provider_id>/locations", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_provider_locations(provider_id):
    """POST not GET — credentials are required to list locations on some providers."""
    from . import vpn_providers
    mod = vpn_providers.get_provider(provider_id)
    if mod is None:
        return _err(f"unknown provider: {provider_id}", 404)
    data = request.get_json(silent=True) or {}
    try:
        locs = mod.list_locations(data)
    except Exception as e:
        return _provider_failure(provider_id, "list_locations", e)
    return _ok({"locations": locs})


# ─── Leak test routes ───────────────────────────────────────────────

@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/leak_test/run", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_leak_test_run(tunnel_id):
    from . import vpn_leak_tests, vpn
    if vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)
    data = request.get_json(silent=True) or {}
    expected_country = data.get("expected_country")
    expected_exit_ip = data.get("expected_exit_ip")
    try:
        agg = vpn_leak_tests.run_all_probes(
            tunnel_id,
            expected_country=expected_country,
            expected_exit_ip=expected_exit_ip,
        )
    except Exception as e:
        return _err(f"probe run raised: {e}", 500)
    return _ok({"result": agg.to_dict()})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/leak_test/history", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_leak_test_history(tunnel_id):
    from . import vpn_leak_tests
    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        return _err("limit must be an integer")
    return _ok({"history": vpn_leak_tests.get_history(tunnel_id, limit=limit)})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/leak_test/latest", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_leak_test_latest(tunnel_id):
    from . import vpn_leak_tests
    return _ok({"result": vpn_leak_tests.get_latest(tunnel_id)})


@vpn_bp.route("/api/vpn/tunnels/<tunnel_id>/webrtc_result", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_webrtc_post(tunnel_id):
    """Workers POST WebRTC probe results captured in browser contexts here."""
    from . import vpn, vpn_leak_tests
    if vpn.get_tunnel(tunnel_id) is None:
        # dva K11: every unknown id used to get its own stored result entry.
        return _err("no such tunnel", 404)
    data = request.get_json(silent=True) or {}
    vpn_leak_tests.record_external_probe_result(tunnel_id, "webrtc_leak", data)
    return _ok()


@vpn_bp.route("/api/vpn/webrtc_js", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_webrtc_js():
    """Return the JS snippet that workers inject into their Playwright contexts.
    Workers eval the snippet and POST the result to /api/vpn/tunnels/<id>/webrtc_result."""
    from . import vpn_leak_tests
    return vpn_leak_tests.WEBRTC_PROBE_JS, 200, {"Content-Type": "application/javascript"}


# ─── Kill switch routes ─────────────────────────────────────────────

@vpn_bp.route("/api/vpn/kill_switch/state", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_kill_state():
    from . import vpn_kill_switch
    return _ok({
        "states": vpn_kill_switch.list_kill_states(),
        "auto_recover": vpn_kill_switch.get_auto_recover(),
    })


# dva K2: reserved probe id, answered on the normal kill-switch path (200)
# whether or not a tunnel of that name exists; see vpn_kill_clear/trigger.
_PROBE_ID = "_probe"


@vpn_bp.route("/api/vpn/kill_switch/<tunnel_id>/clear", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_kill_clear(tunnel_id):
    """dva K2: an id that is neither a registered tunnel nor holding a kill
    state is 404 (a stale state of a removed tunnel stays clearable).
    "_probe" is a reserved probe id: it keeps the 200 path so the body-contract
    probe (tools/body_contract, test_v3_66_1160) can exercise the route."""
    from . import vpn, vpn_kill_switch
    if (tunnel_id != _PROBE_ID and vpn.get_tunnel(tunnel_id) is None
            and not any(s.get("tunnel_id") == tunnel_id
                        for s in vpn_kill_switch.list_kill_states())):
        return _err("no such tunnel", 404)
    vpn_kill_switch.clear_kill(tunnel_id)
    return _ok()


@vpn_bp.route("/api/vpn/kill_switch/<tunnel_id>/trigger", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_kill_trigger(tunnel_id):
    """dva K2: an unknown tunnel id is 404 and creates no kill state.
    "_probe" is a reserved probe id that keeps the 200 path (the probe harness
    restores the kill-switch state it touches)."""
    from . import vpn, vpn_kill_switch
    if tunnel_id != _PROBE_ID and vpn.get_tunnel(tunnel_id) is None:
        return _err("no such tunnel", 404)
    data, bad = _json_object()
    if bad:
        return bad
    vpn_kill_switch.kill_tunnel(tunnel_id, reason=data.get("reason", "manual trigger via API"))
    return _ok()


@vpn_bp.route("/api/vpn/kill_switch/auto_recover", methods=["PUT"]) if vpn_bp else (lambda f: f)
def vpn_kill_auto_recover():
    from . import vpn_config, vpn_kill_switch
    data, bad = _json_object()
    if bad:
        return bad
    if "enabled" not in data:
        return _err("missing 'enabled' boolean")
    if not isinstance(data["enabled"], bool):
        # dva K3: bool("false") is True.
        return _err("'enabled' must be true or false")
    # dva K4: persisted first (vpn_runtime.init reads it back at boot), then
    # applied live; a failed save changes neither.
    try:
        vpn_config.update_global_settings(kill_switch_auto_recover=data["enabled"])
    except OSError as e:
        sys.stderr.write(f"[vpn-api] auto_recover persist failed ({type(e).__name__})\n")
        return _err("persist failed; auto-recover was not changed", 500)
    vpn_kill_switch.set_auto_recover(data["enabled"])
    return _ok({"auto_recover": vpn_kill_switch.get_auto_recover()})


# ─── System kill switch routes ──────────────────────────────────────

@vpn_bp.route("/api/vpn/system_killswitch/available", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_sysks_available():
    from . import vpn_kill_switch_system
    ok, why = vpn_kill_switch_system.available()
    return _ok({"available": ok, "reason": why})


def _sysks_args(data):
    """(vpn_endpoint, allow_ports) or ValueError.

    dva K8: the rule builder keeps the text before the FIRST ':', so an IPv6
    endpoint became remoteip=2001 (or raised for '[...]'). Refused here until
    the builder parses IPv6 (vpn_kill_switch_system, owed).
    """
    endpoint = data.get("vpn_endpoint", "")
    if not endpoint:
        raise ValueError("vpn_endpoint required")
    if not isinstance(endpoint, str):
        raise ValueError("vpn_endpoint must be a string")
    if endpoint.startswith("[") or endpoint.count(":") > 1:
        raise ValueError("an IPv6 vpn_endpoint is not supported by the system "
                         "kill switch; use the IPv4 address or host name")
    allow = data.get("allow_ports")  # dict | None
    if allow is not None and not isinstance(allow, dict):
        raise ValueError("allow_ports must be an object or null")
    return endpoint, allow


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/plan", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_plan(tunnel_id):
    from . import vpn_kill_switch_system
    data, bad = _json_object()
    if bad:
        return bad
    try:
        endpoint, allow = _sysks_args(data)
        cmds = vpn_kill_switch_system.plan(tunnel_id, endpoint, allow_ports=allow)
    except ValueError as e:
        return _err(str(e))  # dva K9: invalid input is 400, not 500
    return _ok({"commands": cmds, "count": len(cmds)})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/apply", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_apply(tunnel_id):
    from . import vpn_kill_switch_system
    data, bad = _json_object()
    if bad:
        return bad
    dry_run = bool(data.get("dry_run", True))
    try:
        endpoint, allow = _sysks_args(data)
        result = vpn_kill_switch_system.apply(tunnel_id, endpoint, dry_run=dry_run, allow_ports=allow)
    except ValueError as e:
        return _err(str(e))  # dva K9
    return jsonify({**result, "ok": result.get("ok", False)})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/commit", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_commit(tunnel_id):
    from . import vpn_kill_switch_system
    ok = vpn_kill_switch_system.commit(tunnel_id)
    return _ok({"committed": ok})


@vpn_bp.route("/api/vpn/system_killswitch/<tunnel_id>/revert", methods=["POST"]) if vpn_bp else (lambda f: f)
def vpn_sysks_revert(tunnel_id):
    from . import vpn_kill_switch_system
    result = vpn_kill_switch_system.revert(tunnel_id)
    return jsonify({**result, "ok": result.get("ok", True)})


# ─── Settings routes ────────────────────────────────────────────────

@vpn_bp.route("/api/vpn/settings", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_settings_get():
    from . import vpn_config
    return _ok({"settings": vpn_config.get_global_settings()})


@vpn_bp.route("/api/vpn/settings", methods=["PUT"]) if vpn_bp else (lambda f: f)
def vpn_settings_update():
    from . import vpn_config, vpn_kill_switch
    data, bad = _json_object()
    if bad:
        return bad
    try:
        out = vpn_config.update_global_settings(**data)
    except ValueError as e:
        return _err(str(e))  # dva K6: nothing was saved
    except OSError as e:
        sys.stderr.write(f"[vpn-api] settings persist failed ({type(e).__name__})\n")
        return _err("persist failed; the settings were not changed", 500)
    if "kill_switch_auto_recover" in data:
        # dva K5: the saved preference is the live one, as at boot.
        vpn_kill_switch.set_auto_recover(out["kill_switch_auto_recover"])
    return _ok({"settings": out})


# ─── Backend availability ───────────────────────────────────────────

@vpn_bp.route("/api/vpn/backends/availability", methods=["GET"]) if vpn_bp else (lambda f: f)
def vpn_backends_availability():
    from . import vpn_wireguard, vpn_openvpn
    wg_ok, wg_why = vpn_wireguard.is_available()
    ov_ok, ov_why = vpn_openvpn.is_available()
    return _ok({
        "wireguard": {"available": wg_ok, "reason": wg_why},
        "openvpn": {"available": ov_ok, "reason": ov_why},
    })


# ─── Registration helper ────────────────────────────────────────────

def register_routes(app) -> int:
    """Call from app.py: register_routes(app). Returns route count."""
    if vpn_bp is None:
        sys.stderr.write("[vpn-api] Flask not installed; routes not registered\n")
        return 0
    try:
        app.register_blueprint(vpn_bp)
    except (ValueError, AssertionError) as e:
        # Already registered (hot-reload) — ignore.
        sys.stderr.write(f"[vpn-api] blueprint already registered: {e}\n")
        return 0
    # Count routes that match our prefix.
    n = sum(1 for r in app.url_map.iter_rules() if r.rule.startswith("/api/vpn/"))
    sys.stderr.write(f"[vpn-api] registered {n} VPN routes\n")
    return n


__all__ = ["register_routes", "vpn_bp"]
