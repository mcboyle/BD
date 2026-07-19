"""Cut v3.66.635 / C5-followup: the /api/replication/status route surface.

``db_replication.py`` (Cut 622 / C5) shipped the continuous-SQLite-replication
engine -- config generation, store enumeration, ``replication_status``, the
fail-closed lifecycle (start/stop) and the restore-then-verify path -- fully
unit-tested (tests/test_db_replication.py), but as an ISLAND: nothing outside
the tests imported it and no API route ever reached ``replication_status()``.

This pins the read-only status route that finally exposes the durability signal
that "strengthens the A0 gold-backup the automation program gates L2 autonomy
on" (db_replication docstring). It mirrors the ``app_semantic_search``
``GET /api/semantic/status`` pattern: read-only, safe whether or not the
``litestream`` binary is present, charter default-OFF.

Deliberately GET-only: no mutating start/stop/restore controls here, so the
route stays non-operator-facing-write (no SPA-wiring obligation). Those controls
are a separate, larger cut (they trip operator_facing_unwired and need real
frontend wiring). Live WAL shipping stays an on-stash concern (needs the binary).
"""
from __future__ import annotations

import pytest

# Opt into the sys.modules wipe so the package re-reads env at import (matches
# the other route tests; see tests/conftest.py for the rationale).
pytestmark = pytest.mark.bd_module_wipe


def test_replication_status_route_registered():
    """The route must exist on the app. If it silently disappears the
    durability signal becomes unreachable again (island regression)."""
    from bulk_downloader import app as a
    rules = {r.rule for r in a.app.url_map.iter_rules()}
    assert "/api/replication/status" in rules, \
        "GET /api/replication/status not registered (db_replication back to an island)"


def test_replication_status_get_returns_durability_snapshot():
    """GET returns 200 with the full replication_status() durability snapshot
    wrapped as ok=True. Charter default is OFF."""
    from bulk_downloader import app as a
    resp = a.app.test_client().get("/api/replication/status")
    assert resp.status_code == 200, resp.status_code
    body = resp.get_json()
    assert body.get("ok") is True, body
    # the exact fields db_replication.replication_status() reports
    for k in ("enabled", "binary_present", "configured_stores", "replica_root", "running"):
        assert k in body, f"missing durability field {k!r} in {sorted(body)}"
    assert body["enabled"] is False, "replication must default OFF (charter)"
    assert isinstance(body["configured_stores"], list), "configured_stores must be a list"
    assert isinstance(body["binary_present"], bool)
    assert body["running"] is False, "no sidecar -> not running"


def test_replication_status_is_get_only():
    """Read-only durability signal: POST must not be accepted (405). This is
    what keeps the route non-mutating / non-operator-facing (no SPA wiring)."""
    from bulk_downloader import app as a
    resp = a.app.test_client().post("/api/replication/status")
    assert resp.status_code == 405, f"status route must be GET-only, got {resp.status_code}"
