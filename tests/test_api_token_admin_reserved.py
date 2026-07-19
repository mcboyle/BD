"""DEC-2 — F4.3 token scope taxonomy v2: admin reserved (defined but unmintable).

The ``{read, enqueue, admin}`` taxonomy already exists (v3.66.227). DEC-2 keeps
``admin`` DEFINED for level/ordering and the route policy, but makes it
RESERVED: ``create_token(scope="admin")`` is rejected, so no admin-capable
token can be minted in v1. ``read`` + ``enqueue`` mint + enforce exactly as
before. This is functionality-neutral (admin was unusable anyway) but means
adding a real admin mint later is NOT a breaking change, and nothing
admin-capable can be minted prematurely (no new attack surface).

Consequence for enforcement: admin-scope routes
(``/api/api_tokens``, ``/api/retention/apply``, …) are reachable ONLY by the
operator (master bearer / session), never by any mintable API token.

Mirrors the 227 file's harness contract: bd_module_wipe + zero-arg functions.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.bd_module_wipe

_MASTER = "test-master-bearer-dec2"


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# ── module layer: admin is defined but not mintable ──────────────────────

def test_admin_still_in_taxonomy_with_ordering():
    """admin stays DEFINED (level 3, top of the order) so the route policy
    and any future mint are not a breaking change."""
    from bulk_downloader import api_tokens as t
    assert "admin" in t.SCOPES
    assert t.scope_level("admin") == 3
    assert t.scope_level("read") < t.scope_level("enqueue") < t.scope_level("admin")


def test_create_admin_is_rejected_reserved():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="admin")
    assert res["ok"] is False
    assert "token" not in res          # no secret minted
    assert "reserved" in res["error"].lower()


def test_create_admin_with_label_or_ttl_still_rejected():
    from bulk_downloader import api_tokens as t
    assert t.create_token(scope="admin", label="x")["ok"] is False
    assert t.create_token(scope="admin", ttl_hours=24)["ok"] is False


def test_read_and_enqueue_still_mint():
    from bulk_downloader import api_tokens as t
    for sc in ("read", "enqueue"):
        res = t.create_token(scope=sc, label="ci")
        assert res["ok"] is True, f"{sc} must still mint"
        assert res["token"].startswith("bdapi_")
        assert res["scope"] == sc
        v = t.verify_token(res["token"])
        assert v["ok"] is True and v["scope"] == sc


def test_unknown_scope_still_rejected_distinctly_from_reserved():
    from bulk_downloader import api_tokens as t
    res = t.create_token(scope="superuser")
    assert res["ok"] is False
    # bad-scope message names the allowed set; reserved is a different path
    assert "reserved" not in res["error"].lower()


def test_no_mintable_scope_can_reach_an_admin_route():
    """The security upshot of DEC-2: with admin unmintable, the highest
    mintable scope (enqueue) is still 403 on an admin route — admin routes
    are operator-only via API tokens."""
    from bulk_downloader import api_tokens as t
    from bulk_downloader import app as a
    os.environ["BD_AUTH_TOKEN"] = _MASTER
    try:
        tok = t.create_token(scope="enqueue")["token"]
        c = a.app.test_client()
        r = c.get("/api/api_tokens", headers=_hdr(tok))
        assert r.status_code == 403
        assert r.get_json().get("required_scope") == "admin"
        # operator path still reaches it (unchanged)
        r2 = c.get("/api/api_tokens", headers=_hdr(_MASTER))
        assert r2.status_code == 200
    finally:
        os.environ.pop("BD_AUTH_TOKEN", None)
