"""v3.66.272  GCW-3 — "Setup site" wizard step.

`POST /api/captures/setup_site` creates a site and (optionally) stores its
login password as a ``@cred:`` reference — never plaintext, never in a
template (F2 posture). It returns the 8-hex ``site_id`` so the capture
wizard can auto-fill it into the Test/Promote steps, killing the
"unknown site_id" friction (the operator no longer hand-types the id).

Decision (this cut): the route lives in ``app.py`` at the ROOT path
``/api/captures/setup_site`` (NOT the ``/cockpit`` blueprint in
``tools/cockpit_console.py``, which is deploy-excluded). It reaches
``_create_site`` / ``s_cfg`` / ``secrets_store`` directly and overlays
normally on deploy.

Contract
--------
  {name, login_url}                      -> 200 {ok, id, login_url, cred_stored: false}
  {name, login_url, username, password}  -> on an UNLOCKED encrypted backend:
                                            200 {ok, id, cred_stored: true};
                                            cfg["password"] == @cred:bulkdl-site-<id>;
                                            the PLAINTEXT never lands in sites_config.json.
  {name, login_url, password} on a PLAINTEXT backend
                                         -> 400 BEFORE any create (atomic: no half-site).
  missing name OR login_url              -> 400.

(R) secret-input coverage — same-cut proof
-------------------------------------------
The credential this input introduces is fed into the target site's login
form during the held-open capture; that login body is captured. We assert
``capture_bodies.redact_body`` masks a ``password`` field by name, so the
secret is redacted in any captured body. (Demonstrating the redaction
covering this new secret-input ships in the SAME cut as the input.)

SPA contract (source-scan; mirrors the GCW-1/2 test style)
----------------------------------------------------------
``CaptureWorkflow.tsx`` calls ``/api/captures/setup_site`` with a FULL
``/api/`` literal (so gui_parity credits it ``spa_wired``), threads the
returned id into ``siteId`` via ``setSiteId``, and uses a write-only secret
input for the password (no plaintext round-trip).
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

PLAINTEXT_PW = "S3cret-Hunter-Pw!"          # the literal we must never see persisted
MASTER = "test-master-pw"


# ─── harness (mirrors test_secrets_endpoints._client_with_isolated_cwd) ──
@contextmanager
def _client(unlock_master: bool = False):
    """Booted Flask test client in an isolated cwd.

    When ``unlock_master`` is True, the (sandbox-default) master_password
    backend is unlocked with a fresh vault so the @cred storage path runs.
    """
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    from bulk_downloader import secrets_store as ss

    orig_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        # site-creation mkdir's screenshots/<sid>; the parent must exist.
        Path(td, "screenshots").mkdir(exist_ok=True)
        try:
            db_init()
            c = A.app.test_client()
            tok = c.get("/api/pair").get_json()["token"]
            csrf = c.post("/api/pair/redeem", json={"token": tok}).get_json()["csrf_token"]
            H = {"X-CSRF-Token": csrf}
            # Clean backend state for each test.
            ss._backend = None
            ss._backend_pref = None
            if unlock_master:
                be = ss.get_backend()
                # Fresh vault accepts any password (stamps verifier on 1st set).
                assert be.name != "plaintext", (
                    "expected an encrypted default backend in this sandbox")
                assert be.unlock(MASTER) is True
            yield c, H, A
        finally:
            try:
                ss._backend = None
                ss._backend_pref = None
            except Exception:
                pass
            os.chdir(orig_cwd)


def _sites_config_text(td_cwd: str) -> str:
    """Concatenate any sites_config*.json written under the cwd, so the
    no-plaintext assertion checks the actual persisted bytes."""
    out = []
    for p in Path(td_cwd).rglob("sites_config*.json"):
        try:
            out.append(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return "\n".join(out)


# ─── create (no creds) ───────────────────────────────────────────────────
def test_setup_site_creates_site_no_creds():
    with _client() as (c, H, A):
        r = c.post("/api/captures/setup_site",
                   json={"name": "DemoSite", "login_url": "https://demo.example/login"},
                   headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        d = r.get_json()
        assert d.get("ok") is True
        sid = d["id"]
        assert isinstance(sid, str) and len(sid) == 8       # 8-hex id
        assert d.get("cred_stored") is False
        assert d.get("login_url") == "https://demo.example/login"
        cfg = A.s_cfg[sid]
        assert cfg["name"] == "DemoSite"
        assert cfg["login_url"] == "https://demo.example/login"
        assert cfg.get("password", "") == ""                # no password set


def test_setup_site_missing_name_400():
    with _client() as (c, H, A):
        r = c.post("/api/captures/setup_site",
                   json={"login_url": "https://x.example/login"}, headers=H)
        assert r.status_code == 400


def test_setup_site_missing_login_url_400():
    with _client() as (c, H, A):
        r = c.post("/api/captures/setup_site",
                   json={"name": "NoUrl"}, headers=H)
        assert r.status_code == 400


# ─── create + store cred as @cred ref (no plaintext) ─────────────────────
def test_setup_site_stores_cred_ref_no_plaintext():
    from bulk_downloader import secrets_store as ss
    with _client(unlock_master=True) as (c, H, A):
        td_cwd = os.getcwd()
        r = c.post("/api/captures/setup_site",
                   json={"name": "CredSite",
                         "login_url": "https://cred.example/login",
                         "username": "alice",
                         "password": PLAINTEXT_PW},
                   headers=H)
        assert r.status_code == 200, r.get_data(as_text=True)
        d = r.get_json()
        assert d.get("ok") is True
        assert d.get("cred_stored") is True
        sid = d["id"]
        cfg = A.s_cfg[sid]

        # username is NOT secret -> stored on the site.
        assert cfg.get("username") == "alice"
        # password field holds the canonical @cred reference, never plaintext.
        ref = ss.make_password_reference(sid)
        assert cfg["password"] == ref == f"@cred:bulkdl-site-{sid}"
        # the ref resolves back to the real secret via the backend.
        assert ss.resolve_password(cfg["password"]) == PLAINTEXT_PW
        assert ss.get_backend().get(ss.site_password_key(sid)) == PLAINTEXT_PW

        # The plaintext must never appear in the persisted sites_config.json
        # NOR in the in-memory cfg snapshot.
        assert PLAINTEXT_PW not in json.dumps(A.s_cfg.get(sid))
        assert PLAINTEXT_PW not in _sites_config_text(td_cwd)


def test_setup_site_returns_id_threadable():
    """The returned id must be the created site's key (auto-fill contract)."""
    with _client() as (c, H, A):
        r = c.post("/api/captures/setup_site",
                   json={"name": "ThreadSite", "login_url": "https://t.example/login"},
                   headers=H)
        sid = r.get_json()["id"]
        assert sid in A.s_cfg


# ─── plaintext backend: refuse a password BEFORE creating (atomic) ───────
def test_setup_site_refuses_password_on_plaintext_backend_atomic():
    from bulk_downloader import secrets_store as ss
    with _client() as (c, H, A):
        assert ss.configure_backend("plaintext") is True
        before = len(A.s_cfg)
        r = c.post("/api/captures/setup_site",
                   json={"name": "PlainBad",
                         "login_url": "https://pb.example/login",
                         "password": PLAINTEXT_PW},
                   headers=H)
        assert r.status_code == 400
        body = r.get_data(as_text=True).lower()
        assert "backend" in body or "encrypted" in body
        # ATOMIC: no half-created site, and certainly no plaintext anywhere.
        assert len(A.s_cfg) == before
        assert not any((cfg or {}).get("name") == "PlainBad"
                       for cfg in A.s_cfg.values())
        assert PLAINTEXT_PW not in json.dumps(A.s_cfg)


# ─── (R) same-cut redaction proof ────────────────────────────────────────
def test_capture_body_redaction_masks_credential():
    """The credential this input introduces is redacted in a captured login
    body (key-name detector covers `password`)."""
    from bulk_downloader import capture_bodies as cb
    body = json.dumps({"username": "alice", "password": PLAINTEXT_PW})
    out = cb.redact_body(body, "application/json")
    assert PLAINTEXT_PW not in (out or "")
    form = f"user=alice&password={PLAINTEXT_PW}"
    out2 = cb.redact_body(form, "application/x-www-form-urlencoded")
    assert PLAINTEXT_PW not in (out2 or "")


# ─── SPA contract (source-scan) ──────────────────────────────────────────
def _capture_workflow_src() -> str:
    root = Path(__file__).resolve().parent.parent
    return (root / "frontend" / "src" / "routes"
            / "CaptureWorkflow.tsx").read_text(encoding="utf-8")


def test_spa_calls_setup_site_full_literal():
    src = _capture_workflow_src()
    # Full /api/ literal so gui_parity credits it spa_wired (NOT a base var).
    assert "/api/captures/setup_site" in src


def test_spa_threads_returned_id_into_siteId():
    src = _capture_workflow_src()
    # The setup step must push the id RETURNED BY setup_site into siteId state.
    # (`setSiteId(` alone already exists via the manual field; this asserts the
    # response is threaded — the auto-fill contract.)
    assert "setSiteId(created.id)" in src


def test_spa_password_input_is_write_only():
    src = _capture_workflow_src()
    # Either the shared write-only SecretField component, or an inline
    # password input with new-password autocomplete (no plaintext round-trip).
    assert ("SecretField" in src) or ('autoComplete="new-password"' in src)
