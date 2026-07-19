"""v3.66.287 — username/email field classification by exclusion.

Defect (observed live): in classify_login() the password detector is a
bare presence check (``type == "password"``) while the username detector
was a narrow POSITIVE whitelist (``type in {text, email, tel, ""}`` AND
``tag == "input"``). A same-page login form whose username/email field
uses any other text-like type (``search``, ``number``, ``url`` …) — or a
custom/blank type the browser coerces to text — was silently dropped:
``user_field == []`` and ``username_value == ""`` while the password
classified fine. Symptom: "password fills but not the username/email,
even though it was recorded."

Fix: detect the username by EXCLUSION (mirror the password posture).
A username candidate is the LAST recorded ``<input>``/``<textarea>`` that
is not the password and not a non-text control (checkbox/radio/submit/
button/reset/image/file/hidden/range/color), and is not flagged secret.

These are pure-function tests over classify_login — no browser.
"""

from bulk_downloader.learn import classify_login

LOGIN = "https://example.com/login"


def _pass_rec(**kw):
    base = {"tag": "input", "type": "password", "name": "password",
            "_input_value": "secret", "url": LOGIN}
    base.update(kw)
    return base


# ── text-like username types beyond the old whitelist ─────────────────

def test_username_type_search_is_classified():
    """A username field rendered as type=search must classify."""
    harvest = {"inputs": [
        {"tag": "input", "type": "search", "name": "login",
         "_input_value": "alice", "url": LOGIN},
        _pass_rec(),
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["user_field"], "type=search username should yield selectors"
    assert r["username_value"] == "alice"
    assert r["password_value"] == "secret"


def test_username_type_number_is_classified():
    """Phone-number / numeric account logins (type=number) must classify."""
    harvest = {"inputs": [
        {"tag": "input", "type": "number", "name": "account",
         "_input_value": "12345", "url": LOGIN},
        _pass_rec(),
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["user_field"], "type=number username should yield selectors"
    assert r["username_value"] == "12345"


def test_username_type_url_is_classified():
    """An unusual but text-like type=url username must classify."""
    harvest = {"inputs": [
        {"tag": "input", "type": "url", "name": "handle",
         "_input_value": "alice", "url": LOGIN},
        _pass_rec(),
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["user_field"], "type=url username should yield selectors"
    assert r["username_value"] == "alice"


# ── over-capture guards: non-text controls must NOT become the username ──

def test_checkbox_is_not_picked_as_username():
    """A 'remember me' checkbox after the real text username must not win."""
    harvest = {"inputs": [
        {"tag": "input", "type": "text", "name": "username",
         "_input_value": "alice", "url": LOGIN},
        _pass_rec(),
        {"tag": "input", "type": "checkbox", "name": "remember",
         "_input_value": "on", "url": LOGIN},
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["username_value"] == "alice", "checkbox must not clobber username"
    assert "remember" not in " ".join(r["user_field"])


def test_submit_input_is_not_picked_as_username():
    """A type=submit input must not be classified as the username field."""
    harvest = {"inputs": [
        {"tag": "input", "type": "email", "name": "email",
         "_input_value": "a@b.com", "url": LOGIN},
        _pass_rec(),
        {"tag": "input", "type": "submit", "name": "go",
         "_input_value": "Sign in", "url": LOGIN},
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["username_value"] == "a@b.com"


def test_secret_flagged_input_is_not_username():
    """An input flagged secret (autocomplete=current-password) must not be
    mistaken for the username even if its type isn't literally password."""
    harvest = {"inputs": [
        {"tag": "input", "type": "text", "name": "username",
         "_input_value": "alice", "url": LOGIN},
        {"tag": "input", "type": "text", "name": "pw", "secret": True,
         "autocomplete": "current-password",
         "_input_value": "secret", "url": LOGIN},
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["username_value"] == "alice", "secret field must not be the username"


# ── regression: the original whitelist cases still work ───────────────

def test_text_username_still_classified():
    harvest = {"inputs": [
        {"tag": "input", "type": "text", "name": "username",
         "_input_value": "alice", "url": LOGIN},
        _pass_rec(),
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["user_field"]
    assert r["username_value"] == "alice"


def test_email_username_still_classified():
    harvest = {"inputs": [
        {"tag": "input", "type": "email", "name": "email",
         "autocomplete": "email", "_input_value": "a@b.com", "url": LOGIN},
        _pass_rec(),
    ], "clicks": []}
    r = classify_login(harvest, login_url=LOGIN)
    assert r["user_field"]
    assert r["username_value"] == "a@b.com"
