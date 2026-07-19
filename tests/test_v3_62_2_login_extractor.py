"""v3.62.2 — tests for extract_login_from_html().

Covers the login-page extractor that produces a learned.login block
(user_field / pass_field / submit_btn) from pasted HTML, sitting next
to the download-selector extractor. The tricky cases — honeypot fields
and JS-wired submit elements — each get a dedicated test.
"""
from bulk_downloader.template_extractor import extract_login_from_html


_CLEAN = """
<form id="login" method="post" action="/auth">
  <input type="text" name="username" id="username" placeholder="Email">
  <input type="password" name="password" id="password">
  <button type="submit" id="go">Log in</button>
</form>
"""

# A honeypot text input before the real one — off-screen, tabindex -1.
_HONEYPOT = """
<form method="post" action="/login">
  <div style="position:absolute;left:-10000px;">
    <input type="text" name="website_url" id="website_url" tabindex="-1">
  </div>
  <input type="email" name="email" id="real_email" placeholder="E-Mail">
  <input type="password" name="password" id="real_pass">
  <div class="submit-button" onclick="document.forms[0].submit();">Go</div>
</form>
"""

# Submit is a styled <div>, JS-wired, no onclick, class contains submit.
_JS_SUBMIT = """
<form id="login-form" method="post" action="/login">
  <input type="text" name="username" id="user-email">
  <input type="password" name="password" id="user-password">
  <div class="loginform-submit-button">Get Inside</div>
</form>
"""


def test_clean_login_form_extracts_all_three():
    r = extract_login_from_html(_CLEAN)
    assert r["ok"]
    assert "#username" in r["login"]["user_field"]
    assert "#password" in r["login"]["pass_field"]
    assert "#go" in r["login"]["submit_btn"]
    assert r["form_action"] == "/auth"


def test_honeypot_field_is_skipped():
    r = extract_login_from_html(_HONEYPOT)
    assert r["ok"]
    # The off-screen website_url trap must NOT become the user field.
    assert "#real_email" in r["login"]["user_field"]
    assert all("website_url" not in s
               for s in r["login"]["user_field"])
    assert any("honeypot" in w.lower() for w in r["warnings"])


def test_js_wired_submit_div_is_found():
    r = extract_login_from_html(_JS_SUBMIT)
    assert r["ok"]
    # A styled <div>, not a <button>, still resolves to a selector.
    assert r["login"]["submit_btn"]
    assert "div.loginform-submit-button" in r["login"]["submit_btn"]


def test_no_password_input_fails_cleanly():
    r = extract_login_from_html("<form><input type='text'></form>")
    assert not r["ok"]
    assert "password" in r["error"].lower()


def test_empty_input_fails_cleanly():
    r = extract_login_from_html("")
    assert not r["ok"]


def test_selectors_drop_hashed_classes():
    # Classes with digits (hashed/volatile) should not appear as the
    # class-based selector; id/name still do.
    html = ("<form><input type='text' name='u' class='ab12cd'>"
            "<input type='password' name='p' class='xy99'>"
            "<button type='submit'>Log in</button></form>")
    r = extract_login_from_html(html)
    assert r["ok"]
    assert "input[name='u']" in r["login"]["user_field"]
    assert all("12" not in s for s in r["login"]["user_field"])


def test_button_submit_preferred_over_input_submit():
    # A form with BOTH an <input type=submit> and a <button type=submit>
    # should resolve to the <button> — the bare input is usually a
    # secondary/nav quick-login control.
    html = ("<form action='/login'>"
            "<input type='text' name='username'>"
            "<input type='password' name='password'>"
            "<input type='submit' name='go' value='Quick'>"
            "<button type='submit' name='sign-in'>Sign In</button>"
            "</form>")
    r = extract_login_from_html(html)
    assert r["ok"]
    assert "button[name='sign-in']" in r["login"]["submit_btn"]


def test_username_type_input_is_accepted():
    # Some sites use the non-standard type="username"; it must still be
    # picked up as the user field.
    html = ("<form action='/auth'>"
            "<input type='username' name='username' id='u'>"
            "<input type='password' name='password' id='p'>"
            "<button type='submit'>Log in</button></form>")
    r = extract_login_from_html(html)
    assert r["ok"]
    assert "#u" in r["login"]["user_field"]


def test_shared_class_inputs_get_distinct_selectors():
    # A Vue-style form where username and password inputs share the
    # SAME class and have no id/name (e.g. Tiny4K). The extractor must
    # still produce DISTINCT selectors — otherwise the password gets
    # typed into the username box.
    html = ("<form class='login'>"
            "<input type='text' class='app-input__field' placeholder='Username'>"
            "<input type='password' class='app-input__field' placeholder='Password'>"
            "<button type='submit'>Member Login</button></form>")
    r = extract_login_from_html(html)
    assert r["ok"]
    u, p = r["login"]["user_field"], r["login"]["pass_field"]
    assert u and p
    # primary selectors must differ
    assert u[0] != p[0]
    # and no selector may appear in both lists
    assert not (set(u) & set(p))
    # the pass selector targets the password input specifically
    assert any("password" in s for s in p)
