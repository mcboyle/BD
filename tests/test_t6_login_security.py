"""T6 (Tier 3) — D-28 manual-takeover log + D-30 CSRF-token inspector.
Two read-only login/security inspection tools in Group C. D-28 is a
classified filtered view of the app log's manual-login/takeover lines;
D-30 inspects the CSRF token mechanism (the HMAC double-submit scheme)
rather than route enforcement.
"""
import os
from pathlib import Path

from bulk_downloader import dev_suite as ds


# ── D-28 manual_takeover_log ───────────────────────────────────────

def _write_log(lines):
    """Seed logs/bulk_downloader.log with the given raw lines."""
    logdir = Path("logs")
    logdir.mkdir(exist_ok=True)
    (logdir / "bulk_downloader.log").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def test_manual_takeover_log_absent_file(clean_workdir):
    r = ds.manual_takeover_log()
    assert r["ok"] is True
    assert r["tool"] == "manual_takeover_log"
    assert r["exists"] is False
    assert r["events"] == []


def test_manual_takeover_log_extracts_and_classifies(clean_workdir):
    _write_log([
        "2026-05-19 10:00:00 INFO  bulk_downloader.runner: "
        "manual login started for siteA: https://a.example/login",
        "2026-05-19 10:00:05 INFO  bulk_downloader.runner: "
        "manual_dl: harvest captured 3 cookies",
        "2026-05-19 10:00:10 INFO  bulk_downloader.app: "
        "finish_manual_login completed for siteA",
        # an unrelated line must NOT be picked up
        "2026-05-19 10:00:12 INFO  bulk_downloader.runner: "
        "download finished for https://a.example/v1",
    ])
    r = ds.manual_takeover_log()
    assert r["exists"] is True
    assert r["match_count"] == 3
    phases = {e["phase"] for e in r["events"]}
    assert "started" in phases
    assert "finished" in phases
    assert "cookies" in phases
    # newest first
    assert r["events"][0]["phase"] == "finished"


def test_manual_takeover_log_classifies_abort_and_error(clean_workdir):
    _write_log([
        "2026-05-19 11:00:00 INFO  bulk_downloader.app: "
        "user abandoned the manual login for siteB",
        "2026-05-19 11:00:01 ERROR bulk_downloader.runner: "
        "manual_dl: session never became ready: timeout",
    ])
    r = ds.manual_takeover_log()
    phases = [e["phase"] for e in r["events"]]
    assert "aborted" in phases
    assert "error" in phases
    assert r["phase_counts"]["aborted"] == 1
    assert r["phase_counts"]["error"] == 1


def test_manual_takeover_log_clamps_limit(clean_workdir):
    _write_log(["2026-05-19 12:00:00 INFO  x: manual login started "
                f"for site{i}" for i in range(20)])
    r = ds.manual_takeover_log(limit=5)
    assert r["match_count"] == 5
    # an absurd limit is clamped, never unbounded
    assert ds.manual_takeover_log(limit=99999)["match_count"] <= 2000


def test_manual_takeover_log_in_scope_line_is_activity(clean_workdir):
    # a takeover-vocabulary line that fits no specific phase falls
    # back to the generic 'activity' classification
    _write_log(["2026-05-19 13:00:00 INFO  x: "
                "manual login keeper pause requested"])
    r = ds.manual_takeover_log()
    assert r["events"][0]["phase"] == "activity"


# ── D-30 csrf_token_inspect ────────────────────────────────────────

def _app():
    os.environ["BD_DEV_MODE"] = "1"
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    from bulk_downloader.app import app
    return app


def test_csrf_token_inspect_reports_mechanism(clean_workdir):
    app = _app()
    r = ds.csrf_token_inspect(app)
    assert r["ok"] is True
    assert r["tool"] == "csrf_token_inspect"
    assert r["key_present"] is True
    assert r["key_bytes"] == 32
    assert r["csrf_header_name"] == "X-CSRF-Token"
    assert r["session_cookie_name"] == "bd_session"
    assert "POST" in r["enforced_for"]
    assert "healthy" in r["verdict"]


def test_csrf_token_inspect_never_emits_the_key(clean_workdir):
    app = _app()
    import bulk_downloader.app as appmod
    r = ds.csrf_token_inspect(app)
    # the raw HMAC key bytes must not appear anywhere in the result
    blob = repr(r)
    assert appmod._csrf_key.hex() not in blob
    assert str(appmod._csrf_key) not in blob


def test_csrf_token_inspect_derives_token_for_session(clean_workdir):
    app = _app()
    import bulk_downloader.app as appmod
    sess = "test-session-token-abc123"
    r = ds.csrf_token_inspect(app, session_cookie=sess)
    assert r["derived_for_session"] is True
    # the derived token must match what the app itself would compute
    assert r["derived_csrf_token"] == appmod._csrf_token_for(sess)
    assert len(r["derived_csrf_token"]) == 32


def test_csrf_token_inspect_no_session_no_derive(clean_workdir):
    app = _app()
    r = ds.csrf_token_inspect(app)
    assert r["derived_for_session"] is False
    assert "derived_csrf_token" not in r


# ── dev routes ─────────────────────────────────────────────────────

def test_manual_takeover_log_route(fresh_app):
    j = fresh_app.get("/api/dev/manual_takeover_log").get_json()
    assert j["ok"] is True
    assert j["tool"] == "manual_takeover_log"


def test_csrf_token_route(fresh_app):
    j = fresh_app.get("/api/dev/csrf_token").get_json()
    assert j["ok"] is True
    assert j["tool"] == "csrf_token_inspect"
    assert j["key_present"] is True


def test_t6_routes_are_dev_gated():
    os.environ.pop("BD_DEV_MODE", None)
    os.environ["BD_DEV_MODE_DISABLE"] = "1"
    try:
        from bulk_downloader.app import app
        c = app.test_client()
        for ep in ("/api/dev/manual_takeover_log",
                   "/api/dev/csrf_token"):
            assert c.get(ep).status_code == 404
    finally:
        os.environ.pop("BD_DEV_MODE_DISABLE", None)
