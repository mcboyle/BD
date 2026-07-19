"""Cut v3.66.636 / C5-finish: mutating replication control routes.

Completes the C5 replication surface begun at v3.66.635 (the read-only
/api/replication/status). Adds the operator-facing lifecycle controls that were
deliberately deferred, wrapping the already-built, fail-closed db_replication
primitives:

  POST /api/replication/start    -> db_replication.start_replication()
  POST /api/replication/stop     -> db_replication.stop_replication()
  POST /api/replication/restore  -> db_replication.restore_store(db_name, <server-chosen dest>)

Security posture pinned here:
  * All three are POST-only (mutating).
  * restore requires a `db_name` that is one of the CONFIGURED stores (rejects an
    arbitrary replica name), and restores to a SERVER-CHOSEN staging path -- the
    caller never supplies a filesystem destination (no file-write-anywhere, the
    dual of the F-APP03-02 file-read fix).
  * Charter default-OFF / fail-closed is preserved: with replication disabled and
    no litestream binary (the sandbox), start returns ok=False and restore returns
    ok=False -- never a partial success.

CSRF note: _check_csrf skips sessionless requests (no bd_session cookie), so the
test client reaches the handlers directly; CSRF enforcement for real browser
sessions is unchanged (these are /api/ POSTs and go through the same gate).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.bd_module_wipe

_ROUTES = ("/api/replication/start", "/api/replication/stop", "/api/replication/restore")


def test_control_routes_registered_as_post():
    from bulk_downloader import app as a
    rules = {(r.rule): {m for m in r.methods if m not in ("HEAD", "OPTIONS")}
             for r in a.app.url_map.iter_rules()}
    for path in _ROUTES:
        assert path in rules, f"{path} not registered"
        assert "POST" in rules[path], f"{path} must accept POST"


def test_control_routes_are_post_only():
    """Mutating routes must reject GET (405) -- a GET must never trigger a
    start/stop/restore side effect."""
    from bulk_downloader import app as a
    c = a.app.test_client()
    for path in _ROUTES:
        assert c.get(path).status_code == 405, f"{path} must be POST-only"


def test_start_is_fail_closed_default_off():
    """start returns ok=False in the default-OFF / no-binary sandbox -- never a
    fabricated success."""
    from bulk_downloader import app as a
    body = a.app.test_client().post("/api/replication/start", json={}).get_json()
    assert body is not None and "ok" in body, body
    assert body["ok"] is False, "replication is default-OFF -> start must fail closed"
    assert "reason" in body


def test_stop_is_idempotent_ok():
    """stop is idempotent: with nothing running it returns ok=True, stopped=False."""
    from bulk_downloader import app as a
    body = a.app.test_client().post("/api/replication/stop", json={}).get_json()
    assert body is not None and body.get("ok") is True, body
    assert body.get("stopped") is False


def test_restore_requires_db_name():
    from bulk_downloader import app as a
    r = a.app.test_client().post("/api/replication/restore", json={})
    assert r.status_code == 400, r.status_code
    assert "db_name" in (r.get_json() or {}).get("error", "")


def test_restore_rejects_unknown_store():
    """A db_name that is not a configured store is refused (400) BEFORE any
    filesystem access -- prevents reading an arbitrary replica path."""
    from bulk_downloader import app as a
    r = a.app.test_client().post("/api/replication/restore",
                                 json={"db_name": "definitely_not_a_store.db"})
    assert r.status_code == 400, r.status_code
    msg = (r.get_json() or {}).get("error", "").lower()
    assert "unknown" in msg or "store" in msg, msg
