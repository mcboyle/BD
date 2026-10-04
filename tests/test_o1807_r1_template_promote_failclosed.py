"""O1807 R1 -- /api/template_manager/promote role gate must FAIL CLOSED.

With multi-user enabled, an error raised while resolving/checking the caller's
role (user-store error) used to be swallowed by ``except Exception: pass`` and
the promote ran anyway. It must refuse 403 and never reach ``promote_draft``.

Controls: a reviewer still promotes, a role-less caller is still 403, and the
single-operator default (multi-user off) is unchanged.
"""
from __future__ import annotations

import pytest
from flask import Flask

from bulk_downloader import app_template_manager as atm
from bulk_downloader import template_manager as tm
from bulk_downloader import user_accounts as ua

BD_GATE_SCOPE = "module"

@pytest.fixture
def client(monkeypatch):
    calls = []
    monkeypatch.setattr(atm, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(tm, "promote_draft",
                        lambda *a, **k: calls.append((a, k)) or {"ok": True})
    app = Flask(__name__)
    app.register_blueprint(atm.template_manager_bp)
    c = app.test_client()
    c.promote_calls = calls
    return c


def _post(c):
    return c.post("/api/template_manager/promote",
                  json={"file": "example.com.template-draft.json"})


def test_store_error_fails_closed(client, monkeypatch):
    monkeypatch.setattr(ua, "multi_user_enabled", lambda *a, **k: True)

    def _boom(*_a, **_k):
        raise OSError("O1807 simulated user-store error")
    monkeypatch.setattr(ua, "current_user_from_cookie", _boom)
    r = _post(client)
    assert client.promote_calls == [], (
        "O1807 FAIL-OPEN: promote_draft ran after the role gate raised "
        f"(status {r.status_code})")
    assert r.status_code == 403, r.get_data(as_text=True)
    assert r.get_json()["ok"] is False


def test_reviewer_still_promotes(client, monkeypatch):
    monkeypatch.setattr(ua, "multi_user_enabled", lambda *a, **k: True)
    monkeypatch.setattr(ua, "current_user_from_cookie",
                        lambda *a, **k: {"username": "r", "role": "reviewer"})
    r = _post(client)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(client.promote_calls) == 1


def test_roleless_caller_still_refused(client, monkeypatch):
    monkeypatch.setattr(ua, "multi_user_enabled", lambda *a, **k: True)
    monkeypatch.setattr(ua, "current_user_from_cookie", lambda *a, **k: None)
    r = _post(client)
    assert r.status_code == 403
    assert client.promote_calls == []


def test_single_operator_default_unchanged(client, monkeypatch):
    monkeypatch.setattr(ua, "multi_user_enabled", lambda *a, **k: False)
    r = _post(client)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert len(client.promote_calls) == 1
