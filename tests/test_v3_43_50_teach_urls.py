"""v3.43.50: Teach wizard captures + saves login_url and success_url.

Sibling bug to v3.43.49 — same class of "wizard had the data, never
wrote it back to config." Cf. test_v3_43_49_teach_credentials.py.

The fix:
  1. classify_login now returns login_url_value and
     success_url_value alongside credential values.
  2. login_url comes from the password input record's URL (where
     the user typed creds); fallback to user record, then submit
     record.
  3. success_url comes from the first click/input record AFTER
     the submit whose URL differs from the submit's URL. Stripped
     to scheme://host/path (no query / fragment) to avoid
     session-specific noise.
  4. SiteRunner.finalize_manual_login persists both to
     cfg["login_url"] / cfg["success_url"].
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LEARN_PY = _REPO_ROOT / "bulk_downloader" / "learn.py"
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


def _bd_runner_src():
    """v3.66.403: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))


# ── classify_login surfaces captured URLs ───────────────────────


def test_classify_returns_login_url_from_password_record():
    """The password input's URL is the most reliable login_url
    signal — the user typed credentials on that page."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "text", "name": "user",
              "_input_value": "alice",
              "url": "https://example.com/login",
              "ts": 1},
            {"tag": "input", "type": "password", "name": "pass",
              "_input_value": "secret",
              "url": "https://example.com/login",
              "ts": 2},
        ],
        "clicks": [
            {"tag": "button", "text": "Sign in",
              "url": "https://example.com/login", "ts": 3},
            {"tag": "a", "text": "Welcome",
              "url": "https://example.com/dashboard", "ts": 4},
        ],
    }
    r = classify_login(harvest)
    assert r["login_url_value"] == "https://example.com/login"


def test_classify_returns_success_url_after_submit():
    """success_url is the first URL the page navigated to after
    the submit click."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "password",
              "_input_value": "secret",
              "url": "https://example.com/login", "ts": 1},
        ],
        "clicks": [
            {"tag": "button", "text": "Sign in",
              "url": "https://example.com/login", "ts": 2},
            {"tag": "a", "text": "Welcome",
              "url": "https://example.com/dashboard", "ts": 3},
        ],
    }
    r = classify_login(harvest)
    assert r["success_url_value"] == "https://example.com/dashboard"


def test_classify_success_url_strips_query_and_fragment():
    """Session-specific query/fragment must be stripped so the
    success_url match logic in do_login still works on fresh
    logins (next session has different session=...)."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "password",
              "_input_value": "x",
              "url": "https://example.com/login", "ts": 1},
        ],
        "clicks": [
            {"tag": "button", "url": "https://example.com/login", "ts": 2},
            {"tag": "a",
              "url": "https://example.com/dashboard?session=abc123&t=xyz#welcome",
              "ts": 3},
        ],
    }
    r = classify_login(harvest)
    # Query + fragment dropped
    assert r["success_url_value"] == "https://example.com/dashboard"


def test_classify_success_url_empty_when_no_post_submit_activity():
    """User clicks I'm Done immediately after submit → no post-
    submit record → success_url stays empty."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "password",
              "_input_value": "x",
              "url": "https://example.com/login", "ts": 1},
        ],
        "clicks": [
            {"tag": "button", "url": "https://example.com/login", "ts": 2},
        ],
    }
    r = classify_login(harvest)
    assert r["success_url_value"] == ""


def test_classify_login_url_falls_back_to_user_record():
    """If there's no password record (some sites have multi-step
    login), fall back to the username input's URL."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "text", "name": "user",
              "_input_value": "alice",
              "url": "https://example.com/step1", "ts": 1},
        ],
        "clicks": [],
    }
    r = classify_login(harvest)
    assert r["login_url_value"] == "https://example.com/step1"


def test_classify_login_url_empty_when_no_records():
    """No inputs, no clicks → both URLs empty, no crash."""
    from bulk_downloader.learn import classify_login
    r = classify_login({"inputs": [], "clicks": []})
    assert r["login_url_value"] == ""
    assert r["success_url_value"] == ""


def test_classify_login_url_handles_missing_url_field():
    """Defensive: record exists but has no `url` key. Must not crash."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "password",
              "_input_value": "x", "ts": 1},
        ],
        "clicks": [],
    }
    r = classify_login(harvest)
    # No URL data → empty
    assert r["login_url_value"] == ""


def test_classify_success_url_only_when_different_from_submit():
    """A post-submit record with the SAME url as submit (e.g. page
    still loading) shouldn't count as success — wait for the URL
    transition."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "password",
              "_input_value": "x",
              "url": "https://example.com/login", "ts": 1},
        ],
        "clicks": [
            {"tag": "button", "url": "https://example.com/login", "ts": 2},
            # Another click but URL hasn't changed yet
            {"tag": "div", "url": "https://example.com/login", "ts": 3},
            # Real transition
            {"tag": "a", "url": "https://example.com/members", "ts": 4},
        ],
    }
    r = classify_login(harvest)
    assert r["success_url_value"] == "https://example.com/members"


# ── Runner persists captured URLs ───────────────────────────────


def test_runner_writes_captured_login_url():
    """SiteRunner pulls login_url_value out of the classifier
    output and writes it to cfg['login_url']."""
    src = _bd_runner_src()
    assert "captured_login_url" in src
    assert 'self.config["login_url"] = captured_login_url' in src


def test_runner_writes_captured_success_url():
    src = _bd_runner_src()
    assert "captured_success_url" in src
    assert 'self.config["success_url"] = captured_success_url' in src


def test_runner_url_pop_runs_before_merge_learned():
    """login_url_value and success_url_value must be popped out of
    `sels` BEFORE merge_learned sees the dict. merge_learned
    doesn't know about these keys and would mishandle them as
    selector roles."""
    src = _bd_runner_src()
    # Both pops happen
    pop_login = src.find('sels.pop("login_url_value"')
    pop_success = src.find('sels.pop("success_url_value"')
    assert pop_login > 0
    assert pop_success > 0
    # And merge_learned call comes after both pops
    merge_pos = src.find("merge_learned(self.config, sels, kind=\"login\")")
    assert merge_pos > pop_login
    assert merge_pos > pop_success


def test_runner_skips_empty_url_writes():
    """If the wizard captured no URL (empty string), the runner
    must NOT clobber whatever was already there. Defense against
    a re-teach where the user only updated credentials, not URLs."""
    src = _bd_runner_src()
    # Look for `if captured_login_url:` (truthiness gate)
    assert "if captured_login_url:" in src
    assert "if captured_success_url:" in src


def test_runner_logs_url_capture():
    """Status message must mention url_captured so the user
    knows the wizard actually updated the URLs."""
    src = _bd_runner_src()
    assert "url_captured" in src
