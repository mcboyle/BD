"""dev-exec-lows r1 -- dev-console-exec FINDINGS F8 F10 (S55-B probes p4-paths S23, p5 S30).

F8  secret_scan kept the text left of the first =/: as the finding label, so a
    secret used as a dict key ("xoxb-...": value) printed in clear.
F10 /api/dev/fixture_site/start: port "abc" / name [..] -> 500 with the raw
    exception text; port 99999 reported ok with a url on :99999 while the
    server bound an ephemeral port. /api/dev/runs?limit=abc and
    /api/dev/stuck_jobs?older_than=<20 digits> -> 500 with the exception text.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest
from flask import Flask

import os

from bulk_downloader import app_dev_runtime, app_dev_testci, db, dev_suite, dev_tools
from bulk_downloader.dev_suite import audit_security

SLACK = "xoxb-" + "CANARYSLACK0123456789"


# ── F8: secret_scan never echoes a secret-shaped label ───────────────

@pytest.mark.parametrize("line, secret", [
    (f'"{SLACK}": "x"', "CANARYSLACK"),
    (f"{SLACK} = 1", "CANARYSLACK"),
    ("'AKIA" + "ABCDEFGHIJKLMNOP': 1", "ABCDEFGHIJKLMNOP"),
    ("sk_live_0123456789abcdef: 1", "0123456789abcdef"),
])
def test_f8_secret_shaped_key_is_not_echoed(line, secret):
    out = audit_security._redact_secret_context(line)
    assert secret not in out, f"dev-exec-lows F8: label echoed the secret: {out!r}"
    assert "redacted" in out


@pytest.mark.parametrize("line, label", [
    ('password = "abcdefghijklmnopqrstu"', "password ="),
    ('"api_key": "abcdefghijklmnopqrstu"', '"api_key":'),
    ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIbPxRfiCYEXAMPLEKEY", "AWS_SECRET_ACCESS_KEY="),
])
def test_f8_control_plain_label_kept(line, label):
    out = audit_security._redact_secret_context(line)
    assert out == f"{label} <redacted>", out


def test_f8_secret_scan_end_to_end(tmp_path, monkeypatch):
    (tmp_path / "probe.py").write_text(
        f'password = "abcdefghijklmnopqrstu"\nd = {{\n"{SLACK}": 1}}\n', encoding="utf-8")
    monkeypatch.setattr(audit_security, "_repo_root", lambda: tmp_path)
    r = audit_security.secret_scan()
    pats = sorted(f["pattern"] for f in r["findings"])
    assert pats == ["generic_api_key_assign", "slack_token"], r["findings"]  # control: both seen
    leaked = [f["context"] for f in r["findings"] if "CANARYSLACK" in f["context"]]
    assert not leaked, f"dev-exec-lows F8: secret_scan echoed {leaked}"


# ── F10: fixture_site/start input validation ─────────────────────────

def _dev_client(monkeypatch):
    for mod in (app_dev_testci, app_dev_runtime):
        monkeypatch.setattr(mod, "_dev_mode_guard", lambda: None)
        monkeypatch.setattr(mod, "_check_csrf", lambda *a, **k: None, raising=False)
    app = Flask(__name__)
    app.register_blueprint(app_dev_testci.dev_bp)
    return app.test_client()


@pytest.fixture
def start(monkeypatch):
    calls = []

    def fake_start(name="site1", port=None):
        calls.append((name, port))
        return {"tool": "fixture_site_start", "ok": True, "name": name, "port": port}

    monkeypatch.setattr(dev_suite, "fixture_site_start", fake_start)
    client = _dev_client(monkeypatch)

    def post(body):
        r = client.post("/api/dev/fixture_site/start", json=body)
        return r.status_code, r.get_json(), calls
    return post


@pytest.mark.parametrize("bad", ["abc", 99999, 65536, 0, -1, 1.5, True, [], "80a", "٣",
                                 pytest.param("9" * 4301, id="digits4301")])
def test_f10_bad_port_is_400(start, bad):
    code, body, calls = start({"port": bad})
    assert code == 400 and not calls, \
        f"dev-exec-lows F10: port {bad!r:.40} -> {code} {body}, start calls {calls}"
    assert body == {"ok": False, "error": "port must be an integer 1-65535"}


@pytest.mark.parametrize("bad", [["site1"], {"a": 1}, 3])
def test_f10_bad_name_is_400(start, bad):
    code, body, calls = start({"name": bad})
    assert code == 400 and not calls, f"dev-exec-lows F10: name {bad!r} -> {code} {body}"


@pytest.mark.parametrize("port", [None, 18994, 65535])
def test_f10_control_good_port_reaches_start(start, port):
    body = {} if port is None else {"port": port}
    code, _, calls = start(body)
    assert code == 200 and calls == [("site1", port)], (code, calls)


def test_f10_digit_string_port_reaches_start_as_int(start):
    # "18994" kept working on BASE (int() in fixture_site_start); it now
    # arrives already validated as an int.
    code, _, calls = start({"port": "18994"})
    assert code == 200 and calls == [("site1", 18994)], (code, calls)


# ── F10: runs?limit / stuck_jobs?older_than ──────────────────────────

@pytest.fixture
def dev(monkeypatch):
    seen = {}
    monkeypatch.setattr(dev_tools, "recent_runs", lambda limit: seen.setdefault("limit", limit) and [])
    monkeypatch.setattr(dev_suite, "stuck_jobs", lambda older_than: seen.setdefault("older_than", older_than) and {})
    return _dev_client(monkeypatch), seen


@pytest.mark.parametrize("url, field", [
    ("/api/dev/runs?limit=abc", "limit"), ("/api/dev/runs?limit=1.5", "limit"),
    ("/api/dev/stuck_jobs?older_than=abc", "older_than"), ("/api/dev/stuck_jobs?older_than=1e30", "older_than"),
])
def test_f10_non_integer_query_is_400(dev, url, field):
    client, seen = dev
    r = client.get(url)
    assert r.status_code == 400 and not seen, f"dev-exec-lows F10: {url} -> {r.status_code} {r.get_json()}"
    assert r.get_json() == {"ok": False, "error": f"{field} must be an integer"}


@pytest.mark.parametrize("url, field, want", [
    ("/api/dev/runs", "limit", 20), ("/api/dev/runs?limit=999", "limit", 50),
    ("/api/dev/runs?limit=-3", "limit", 1), ("/api/dev/stuck_jobs", "older_than", 1800),
])
def test_f10_control_integer_query_reaches_handler(dev, url, field, want):
    client, seen = dev
    assert client.get(url).status_code == 200 and seen == {field: want}, seen


def test_f10_stuck_jobs_huge_older_than_is_clamped_not_500(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", os.path.join(str(tmp_path), "dev.db"))
    db.db_init()
    client = _dev_client(monkeypatch)
    ok = client.get("/api/dev/stuck_jobs?older_than=60")  # control: the real handler answers
    assert ok.status_code == 200 and ok.get_json()["threshold_seconds"] == 60, ok.get_json()
    r = client.get("/api/dev/stuck_jobs?older_than=99999999999999999999")
    assert r.status_code == 200, f"dev-exec-lows F10: stuck_jobs 20-digit older_than -> {r.status_code} {r.get_json()}"
    assert r.get_json()["threshold_seconds"] == 10 * 365 * 86400
