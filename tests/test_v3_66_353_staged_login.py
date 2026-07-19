"""test_v3_66_353_staged_login.py — two-step (staged) login password recovery.

Backlog PXL-PWSEL: a comprehensive password-selector list (which includes the
catch-all input[type=password]) failing on EVERY selector is the signature of a
two-step login — e.g. Pexels: enter email, click Continue, and only THEN does a
separate password screen render. The password field is simply not in the DOM
when the fill runs, so no selector can match. The general (not Pexels-specific)
fix: on a password-fill miss, click the continue/submit affordance, wait for a
password field to appear, and re-attempt the fill ONCE before manual takeover.

The live fill needs a real browser (not sandbox-runtime-testable), so the
orchestration is factored into login._staged_password_retry(page, sb, pf, pw)
and exercised here with a fake page that withholds the password field until the
continue button is clicked.

run_tests.py conventions: zero-arg test_* functions, plain asserts.
"""
from bulk_downloader import login


# --- a minimal fake Playwright page -----------------------------------------
class _FakeLocator:
    def __init__(self, sel, page):
        self.sel = sel
        self.page = page

    @property
    def first(self):
        return self

    def _is_password(self):
        return "pass" in self.sel.lower()

    def wait_for(self, state="visible", timeout=0):
        if self._is_password():
            if not self.page.pw_visible:
                raise Exception(f"password field not present: {self.sel}")
            return
        # a submit/continue selector
        if not self.page.has_continue:
            raise Exception(f"no continue affordance: {self.sel}")
        return

    def fill(self, value):
        self.page.calls.append(("fill", self.sel, value))

    def click(self, timeout=0, force=False):
        self.page.calls.append(("click", self.sel, force))
        if not self._is_password():
            # clicking the continue/submit button advances to the pw screen
            self.page.pw_visible = True


class _FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def type(self, ch, delay=0):
        self.page.typed += ch


class _FakePage:
    def __init__(self, has_continue=True, pw_visible=False):
        self.has_continue = has_continue
        self.pw_visible = pw_visible
        self.calls = []
        self.typed = ""
        self.keyboard = _FakeKeyboard(self)

    def locator(self, sel):
        return _FakeLocator(sel, self)


def test_helper_exists():
    assert hasattr(login, "_staged_password_retry"), \
        "login._staged_password_retry must exist (staged-login recovery)"


def test_next_in_submit_texts():
    # "Next" broadens step-1 advance coverage for staged logins; "Continue"
    # (the Pexels/Auth0 affordance) was already present.
    assert "Next" in login._SUBMIT_TEXTS, login._SUBMIT_TEXTS
    assert "Continue" in login._SUBMIT_TEXTS, login._SUBMIT_TEXTS


def test_staged_retry_recovers_two_step_login():
    # Password field absent until the continue button is clicked.
    page = _FakePage(has_continue=True, pw_visible=False)
    ok, info = login._staged_password_retry(
        page, login.SUBMIT_FALLBACKS, login.PASS_FIELD_FALLBACKS, "s3cret")
    assert ok is True, info
    # the continue button was clicked, then the password got typed in
    assert any(c[0] == "click" for c in page.calls), page.calls
    assert page.typed == "s3cret", page.typed


def test_staged_retry_gives_up_without_continue():
    # No continue affordance -> can't advance -> returns False (caller then
    # falls back to manual takeover, exactly as before).
    page = _FakePage(has_continue=False, pw_visible=False)
    ok, info = login._staged_password_retry(
        page, login.SUBMIT_FALLBACKS, login.PASS_FIELD_FALLBACKS, "s3cret")
    assert ok is False, info


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"ok  {fn.__name__}")
    print(f"\n{passed}/{len(fns)} passed")
