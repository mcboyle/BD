"""D1: credential-bearing login URLs never escape as status or evidence."""

import json
from pathlib import Path


BD_GATE_SCOPE = "module"


def test_credential_query_is_redacted_from_login_evidence(tmp_path):
    from bulk_downloader.login_impl.replay import member_state_check

    secret = "d1-password-not-for-evidence"
    final_url = (
        "https://login.example.invalid/complete?username=alice&password="
        f"{secret}&continue=members")

    class Page:
        url = final_url

        def content(self):
            return f'<html><a href="{final_url}">member page</a></html>'

    matched, why, evidence_path = member_state_check(
        Page(), {"login_evidence_dir": str(tmp_path)}, tag="login-d1")

    assert matched is False
    evidence = Path(evidence_path).read_text(encoding="utf-8")
    assert secret not in why
    assert secret not in evidence
    assert "username=<REDACTED>" in evidence
    assert "password=<REDACTED>" in evidence
    assert "continue=members" in evidence  # noncredential positive control


def test_credential_query_is_redacted_from_api_status(fresh_app):
    from bulk_downloader.app_state import runners

    secret = "d1-password-not-for-status"
    response = fresh_app.post("/api/sites", json={
        "name": "d1-status",
        "username": "alice",
        "password": "stored-password",
    })
    sid = response.get_json()["id"]
    runners[sid]._login_status = (
        "Expected URL contains '/members', got "
        f"https://login.example.invalid/complete?username=alice&password={secret}&next=home")

    payload = fresh_app.get("/api/status").get_json()[sid]
    rendered = json.dumps(payload)
    assert secret not in rendered
    assert "username=<REDACTED>" in payload["login_status"]
    assert "password=<REDACTED>" in payload["login_status"]
    assert "next=home" in payload["login_status"]  # noncredential positive control
