"""Row 367 — the historically reserved admin scope is now operational.

The path retains its DEC-2-era filename because test inventory baselines refer
to it, but its contract is current: ``read``, ``enqueue``, and ``admin`` are all
mintable, and the existing fail-closed route policy gives admin tokens the
destructive/management boundary that the name promises.

Mirrors the 227 file's harness contract: bd_module_wipe + zero-arg functions.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.bd_module_wipe

_MASTER = "test-master-bearer-dec2"


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── module layer: admin is defined and mintable ───────────────────────

def test_admin_still_in_taxonomy_with_ordering():
    """admin remains level 3 at the top of the enforced hierarchy."""
    from bulk_downloader import api_tokens as t
    assert "admin" in t.SCOPES
    assert t.scope_level("admin") == 3
    assert t.scope_level("read") < t.scope_level("enqueue") < t.scope_level("admin")


def test_create_admin_is_mintable_and_verifiable():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="admin")
    assert res["ok"] is True, res
    assert res["scope"] == "admin"
    assert res["token"].startswith("bdapi_")
    verified = t.verify_token(res["token"])
    assert verified["ok"] is True
    assert verified["scope"] == "admin"
    assert verified["scope_level"] == 3


def test_create_admin_with_label_or_ttl_still_mints():
    from bulk_downloader import api_tokens as t
    labelled = t.create_token(scope="admin", label="x")
    expiring = t.create_token(scope="admin", ttl_hours=24)
    assert labelled["ok"] is True, labelled
    assert labelled["scope"] == "admin"
    assert expiring["ok"] is True, expiring
    assert expiring["scope"] == "admin"
    assert expiring["expires_at"] is not None


def test_read_and_enqueue_still_mint():
    from bulk_downloader import api_tokens as t
    for sc in ("read", "enqueue"):
        res = t.create_token(scope=sc, label="ci")
        assert res["ok"] is True, f"{sc} must still mint"
        assert res["token"].startswith("bdapi_")
        assert res["scope"] == sc
        v = t.verify_token(res["token"])
        assert v["ok"] is True and v["scope"] == sc


def test_unknown_scope_still_rejected_with_the_complete_scope_set():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="superuser")
    assert res["ok"] is False
    assert res["error"] == "scope must be one of: read, enqueue, admin"


def test_admin_reaches_admin_route_while_enqueue_names_missing_scope():
    from bulk_downloader import api_tokens as t
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        enqueue = t.create_token(scope="enqueue")
        admin = t.create_token(scope="admin")
        assert enqueue["ok"] is True, enqueue
        assert admin["ok"] is True, admin
        assert t.verify_token(enqueue["token"])["scope"] == "enqueue"
        assert t.verify_token(admin["token"])["scope"] == "admin"
        c = a.app.test_client()
        denied = c.get("/api/api_tokens", headers=_hdr(enqueue["token"]))
        assert denied.status_code == 403
        assert denied.get_json() == {
            "error": "insufficient token scope",
            "required_scope": "admin",
        }
        allowed = c.get("/api/api_tokens", headers=_hdr(admin["token"]))
        assert allowed.status_code == 200
        assert allowed.get_json()["ok"] is True
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)
