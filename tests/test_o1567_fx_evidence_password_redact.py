"""O1567 fx-evidence-password-redact (test3big + bd4, 2026-09-29).

``login_evidence/manual-takeover-*.html`` on test3big held the account's
username (14 chars) and password (8 chars) in cleartext: the page the takeover
was read from carried them as ``<input value="...">`` attributes and
``write_login_evidence`` redacted only URL query values before writing
``page.content()``.

Now every user-typed input value (password, text, email, ...) and any input
whose name/id names a credential is written as ``<REDACTED>``; buttons,
checkboxes and non-credential hidden fields are left as read.
"""
from __future__ import annotations

from pathlib import Path

from bulk_downloader.login_impl.replay import write_login_evidence

BD_GATE_SCOPE = "module"

USER = "someone@example.test"
SECRET = "Hunter2!x"
CSRF = "csrf-2f8a1c0e-token"

PAGE_HTML = f"""<html><body><form method="post" action="/login">
<input type="hidden" name="_csrf" value="{CSRF}">
<input id="username" name="username" type="text" value="{USER}">
<input id="password" name="password" type="password" value="{SECRET}">
<input name='pass2' type='PASSWORD' value='{SECRET}' autocomplete="current-password">
<input name="identifier" value="{USER}">
<input type="hidden" name="auth_password" value="{SECRET}">
<input type="checkbox" name="remember" value="on" checked>
<input type="submit" value="Sign in">
</form></body></html>"""


class _Page:
    def __init__(self, html):
        self._html = html

    def content(self):
        return self._html

    def screenshot(self, path):
        Path(path).write_bytes(b"\x89PNG\r\n")


def _write(tmp_path, html=PAGE_HTML):
    path = write_login_evidence(_Page(html), {"login_evidence_dir": str(tmp_path)},
                                "https://members.fixture.test/login",
                                "manual takeover couldn't submit form")
    assert path, "no evidence written"
    return Path(path).read_text(encoding="utf-8")


def test_typed_credentials_never_reach_the_evidence_file(tmp_path):
    text = _write(tmp_path)
    assert SECRET not in text, "CLEARTEXT-PASSWORD in login evidence"
    assert USER not in text, "CLEARTEXT-USERNAME in login evidence"
    assert text.count('value="<REDACTED>"') + text.count("value='<REDACTED>'") == 5


def test_non_credential_values_are_kept_as_read(tmp_path):
    text = _write(tmp_path)
    assert f'value="{CSRF}"' in text
    assert 'name="remember" value="on"' in text
    assert 'type="submit" value="Sign in"' in text


def test_page_without_inputs_is_unchanged(tmp_path):
    html = "<html><body><p>value=\"not an input\"</p></body></html>"
    assert _write(tmp_path, html).endswith(html)


# Lens bd-worker-B18-B REFUTE (O1568): three shapes the first cut wrote in clear.
def test_f1_a_data_value_attribute_does_not_shield_the_real_value(tmp_path):
    html = f'<form><input data-value="x" type="password" name="pw" value="{SECRET}"></form>'
    text = _write(tmp_path, html)
    assert SECRET not in text, "F1 CLEARTEXT-PASSWORD: data-value= took the redaction, value= kept the secret"
    assert 'data-value="x"' in text, "F1: data-value is not the field's value and stays as read"


def test_f2_a_gt_inside_a_quoted_attribute_does_not_end_the_tag(tmp_path):
    html = f'<form><input placeholder="a > b" type="password" value="{SECRET}"></form>'
    text = _write(tmp_path, html)
    assert SECRET not in text, "F2 CLEARTEXT-PASSWORD: the tag was cut at a quoted '>' before value="
    assert 'placeholder="a > b"' in text


def test_f3_a_credential_named_textarea_is_redacted(tmp_path):
    html = (f'<form><textarea name="password">{SECRET}</textarea>'
            '<textarea name="comment">hello there</textarea></form>')
    text = _write(tmp_path, html)
    assert SECRET not in text, "F3 CLEARTEXT-PASSWORD in a credential-named textarea"
    assert "hello there" in text, "F3: an ordinary textarea stays as read"
