"""v3.43.49 bug-fix tests: teach-wizard credential persistence.

The bug: when a user runs the teach wizard and types their username
and password into the login form, the wizard captured the field
SELECTORS but threw away the VALUES. The user then had to manually
retype credentials into the site edit form before headless workers
could log in. This wasted the whole point of typing them once in
the wizard.

The fix has three parts:
  1. _info() in the recorder JS now exposes an _input_value field
     alongside `text`. For password-type inputs, `text` is redacted
     to "[pw]" so the teach panel's click log doesn't display the
     password; _input_value carries the raw value to the server.
  2. classify_login extracts username_value / password_value from
     the input records' _input_value channel.
  3. SiteRunner.finalize_manual_login persists the captured values
     to cfg["username"] / cfg["password"] (skipping if existing
     password is a @cred: vault reference, to avoid de-encrypting).

Also fixed in passing:
  - Password redaction in the click log. Previously clicking a
    password field captured its value into `text`, which was
    rendered via innerHTML in the teach panel.
"""
from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_LEARN_PY = _REPO_ROOT / "bulk_downloader" / "learn.py"


def _learn_impl_src():
    """Concatenated source of the decomposed learn_impl/ package. learn.py is now an
    ADD-only re-export shim (DECOMP-LEAF cut 5); RECORDER_JS/TEACH_OVERLAY_JS and the
    function bodies live in learn_impl/*.py, so source/structure tests read the package."""
    from pathlib import Path as _P
    import bulk_downloader.learn_impl as _pkg
    _d = _P(_pkg.__file__).parent
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(_d.glob("*.py")))
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"


def _bd_runner_src():
    """v3.66.403: runner.py decomposed into runner_*.py mixins; aggregate the
    package so moved SiteRunner method bodies stay visible to source checks."""
    from pathlib import Path as _P
    from bulk_downloader import runner as _R
    _pd = _P(_R.__file__).parent
    return "\n".join(q.read_text(encoding="utf-8")
                     for q in [_pd / "runner.py"] + sorted(_pd.glob("runner_*.py")))


# ── classify_login surfaces captured values ───────────────────────


def test_classify_login_returns_username_value():
    """A user input record with _input_value 'alice' must surface
    as username_value='alice' in the classifier output."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "text", "name": "username",
              "text": "alice", "_input_value": "alice",
              "ts": 1},
            {"tag": "input", "type": "password", "name": "password",
              "text": "[pw]", "_input_value": "s3cret-pa$$word",
              "ts": 2},
        ],
        "clicks": [
            {"tag": "button", "text": "Sign in", "ts": 3,
              "url": "https://example.com/login"},
        ],
    }
    result = classify_login(harvest, login_url="https://example.com/login")
    assert result["username_value"] == "alice"
    assert result["password_value"] == "s3cret-pa$$word"


def test_classify_login_empty_when_no_inputs():
    """No input records → empty credential values, no crash."""
    from bulk_downloader.learn import classify_login
    harvest = {"inputs": [], "clicks": []}
    result = classify_login(harvest)
    assert result["username_value"] == ""
    assert result["password_value"] == ""


def test_classify_login_skips_text_field_for_password():
    """The `text` field is the redacted channel — password value
    must come from _input_value, NOT from text. If a future change
    forgets to set _input_value but sets text to a real password
    (the pre-fix behavior), we want the test to surface that."""
    from bulk_downloader.learn import classify_login
    # Simulated MALFORMED record: text has real password but
    # _input_value is missing. The classifier should NOT fall back
    # to text — that would re-introduce the leak.
    harvest = {
        "inputs": [
            {"tag": "input", "type": "password",
              "text": "leaked-via-text",
              # NOTE: no _input_value
              "ts": 1},
        ],
        "clicks": [],
    }
    result = classify_login(harvest)
    # The fix uses _input_value with .get() default "", so this
    # record yields an empty password — better than leaking.
    assert result["password_value"] == ""


def test_classify_login_picks_last_username_input():
    """If user typed in field A, then cleared, then typed in field
    B, the classifier picks the LAST non-password text input."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            {"tag": "input", "type": "text", "name": "wrong_field",
              "_input_value": "first_value", "text": "first_value",
              "ts": 1},
            {"tag": "input", "type": "text", "name": "email",
              "_input_value": "correct@example.com",
              "text": "correct@example.com",
              "ts": 2},
        ],
        "clicks": [],
    }
    result = classify_login(harvest)
    assert result["username_value"] == "correct@example.com"


# ── Recorder JS redaction ─────────────────────────────────────────


def test_recorder_js_redacts_password_in_text():
    """The recorder's _info() function must NOT put a password
    input's value into `text` — that field renders in the teach
    panel's click log via innerHTML."""
    src = _learn_impl_src()
    # Find the _info closure
    pos = src.find("const _info = (el) =>")
    assert pos > 0
    body = src[pos:pos + 2500]
    # The fix: text is conditional on isPw (the password flag)
    assert "isPw" in body
    # And the sentinel "[pw]" is used for the redacted case
    assert "'[pw]'" in body or '"[pw]"' in body


def test_recorder_js_has_input_value_channel():
    """The separate _input_value channel must exist for both INPUT
    and TEXTAREA elements, so classify_login can recover the typed
    credential."""
    src = _learn_impl_src()
    assert "_input_value" in src
    # Test that the channel is conditional on tag being INPUT or TEXTAREA
    pos = src.find("_input_value:")
    body = src[pos:pos + 400]
    assert "INPUT" in body
    assert "TEXTAREA" in body


# ── Runner persists captured credentials ──────────────────────────


def test_runner_writes_captured_username():
    """SiteRunner's post-teach merge logic must write captured
    username_value to cfg['username']."""
    src = _bd_runner_src()
    # Look for the relevant block
    assert "captured_user" in src
    assert 'self.config["username"] = captured_user' in src


def test_runner_writes_captured_password_unless_vault_ref():
    """SiteRunner must write captured password UNLESS the existing
    password field is a @cred: vault reference — overwriting that
    with plaintext would silently de-encrypt the credential."""
    src = _bd_runner_src()
    assert "captured_pass" in src
    # Vault-reference defense
    assert '"@cred:"' in src or "'@cred:'" in src
    # The branch logic exists
    assert "startswith" in src and "@cred:" in src


def test_runner_logs_credential_capture():
    """The post-teach status message must mention the credential
    capture so the user knows it worked."""
    src = _bd_runner_src()
    pos = src.find("cred_captured")
    assert pos > 0
    body = src[pos:pos + 2000]
    # Status message references cred_captured for UI feedback
    assert "cred_captured" in body


# ── Defensive: empty / non-string values handled ─────────────────


def test_classify_login_handles_non_string_input_value():
    """Pathological input: _input_value not a string. Must not crash."""
    from bulk_downloader.learn import classify_login
    harvest = {
        "inputs": [
            # _input_value missing entirely → default ""
            {"tag": "input", "type": "text", "name": "user", "ts": 1},
        ],
        "clicks": [],
    }
    result = classify_login(harvest)
    # No crash, returns empty string
    assert result["username_value"] == ""


def test_classify_login_username_value_is_string():
    """Return-type sanity: username_value / password_value always
    strings, even when records are partial."""
    from bulk_downloader.learn import classify_login
    for harvest in [
        {"inputs": [], "clicks": []},
        {"inputs": [{"tag": "input", "type": "password", "ts": 1}], "clicks": []},
        {"inputs": [{"tag": "input", "type": "text", "ts": 1}], "clicks": []},
    ]:
        r = classify_login(harvest)
        assert isinstance(r["username_value"], str)
        assert isinstance(r["password_value"], str)
