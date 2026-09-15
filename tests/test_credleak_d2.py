"""D2: JS-only login fallbacks must not turn password forms into GETs."""


BD_GATE_SCOPE = "module"


def test_js_form_fallback_refuses_unset_or_get_password_forms():
    from bulk_downloader.login_impl.submit import _form_submit_is_safe

    # A browser treats an omitted method as GET, so both shapes would put a
    # typed password in the navigation URL if requestSubmit()/submit() ran.
    assert _form_submit_is_safe(None, has_password=True) is False
    assert _form_submit_is_safe("GET", has_password=True) is False
    assert _form_submit_is_safe(" post ", has_password=True) is False


def test_js_form_fallback_keeps_post_and_noncredential_controls():
    from bulk_downloader.login_impl.submit import _form_submit_is_safe

    assert _form_submit_is_safe("POST", has_password=True) is True
    assert _form_submit_is_safe("get", has_password=False) is True
    assert _form_submit_is_safe(None, has_password=False) is True


def test_js_fallback_propagates_get_password_refusal(monkeypatch):
    """The real m2/m3 call seam must honor the browser-form refusal result."""
    from bulk_downloader.login_impl import submit

    class GetPasswordFormPage:
        url = "https://login.example.invalid/login"

        def __init__(self):
            self.calls = []

        def is_closed(self):
            return len(self.calls) >= 2

        def evaluate(self, script, _password_selectors):
            self.calls.append(script)
            return {
                "submitted": False,
                "method": "get",
                "hasPassword": True,
                "reason": "refused password form without POST",
            }

    page = GetPasswordFormPage()
    monkeypatch.setattr(submit, "_try_click", lambda *_: (False, "no button"))
    result, _detail = submit._submit_login(page, [], ["input[type=password]"])

    assert result == "PAGE_CLOSED"  # m2 and m3 both declined; no submit fired
    assert len(page.calls) == 2
    for script in page.calls:
        assert "hasPassword && method.toLowerCase() !== 'post'" in script
        assert "refused password form without POST" in script
