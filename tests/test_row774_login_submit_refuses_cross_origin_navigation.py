"""Row 774: the login submit sweep accepts a cross-origin navigation as
success.

On bang.com the text-matched candidate button:has-text("LOGIN") matches the
"LOGIN WITH GOOGLE" button, which navigates to accounts.google.com. At
c9dd4176 both acceptance seams read a bare `page.url != initial_url`:

  * _submit_login's `_navigated()` -- the "navigated before {label}" pre-check
    and the post-click `return True, label`;
  * do_login's final acceptance (`success not in cur` -> "OK -- N cookies").

The rule the cut states on _submit_login's docstring: success after a click
requires SAME-ORIGIN navigation (scheme+host+port, default ports normalised;
bulk_downloader.interstitial._origin, the predicate the gate-action path
already verifies every click with) or a captured multi-step flow EXPLICITLY
declared for the site (replay_saved_login_flow ran, or an absolute
success_url naming the destination origin). login.example.com ->
www.example.com is cross-origin unless declared that way.

Every page here is a duck-typed fixture. NO LIVE SITE IS TOUCHED and no
login is ever started anywhere.
"""
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

LOGIN_URL = "https://www.bang.com/login"
GOOGLE = ("https://accounts.google.com/o/oauth2/v2/auth"
          "?client_id=fixture&redirect_uri=https%3A%2F%2Fwww.bang.com%2Fauth")
TEXT_MATCHED = 'button:has-text("LOGIN")'
SAFE = "form[action*=login_check] button[type=submit]"
CANDIDATES = [TEXT_MATCHED, SAFE]


def _raise(*a, **kw):
    raise RuntimeError("fixture: not available")


class _Locator:
    """Only the selectors in `page.on_click` are visible; clicking one moves
    the page to the URL it maps to (a callable may raise AFTER moving it,
    the way a Playwright click times out once navigation has begun)."""

    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    @property
    def first(self):
        return self

    def wait_for(self, **kw):
        if self._selector not in self._page.on_click:
            raise RuntimeError(f"fixture: {self._selector} not visible")

    def click(self, **kw):
        if self._selector not in self._page.on_click:
            raise RuntimeError(f"fixture: {self._selector} not visible")
        self._page.clicks.append(self._selector)
        target = self._page.on_click[self._selector]
        if callable(target):
            target(self._page)
        else:
            self._page.url = target

    def count(self):
        return 1 if self._selector in self._page.on_click else 0

    press = _raise


class _Page:
    def __init__(self, url=LOGIN_URL, on_click=None):
        self.url = url
        self.on_click = dict(on_click or {})
        self.clicks = []
        self.keyboard = SimpleNamespace(press=_raise)
        self.mouse = SimpleNamespace(move=lambda *a, **kw: None)

    def is_closed(self):
        return False

    def locator(self, selector):
        return _Locator(self, selector)

    def wait_for_load_state(self, *a, **kw):
        return None

    evaluate = _raise

    def content(self):
        return "<html><body>fixture page</body></html>"

    def goto(self, url, **kw):
        return None


class _Clock:
    """time.time advances 5s per read so the 8s post-click poll makes exactly
    one pass; sleep is free."""

    def __init__(self):
        self.now = 1_000.0

    def time(self):
        self.now += 5.0
        return self.now

    def sleep(self, seconds):
        return None


@pytest.fixture
def sweep(monkeypatch):
    """The real _submit_login with time stubbed and the origin predicate
    counted."""
    from bulk_downloader.login_impl import submit

    monkeypatch.setattr(submit, "time", _Clock())
    counted = {"origin": 0}
    real_origin = getattr(submit, "_origin", None)
    if real_origin is not None:
        def counting(url):
            counted["origin"] += 1
            return real_origin(url)
        monkeypatch.setattr(submit, "_origin", counting)

    def run(page, candidates=CANDIDATES):
        return submit._submit_login(page, list(candidates), [])
    run.counted = counted
    return run


# ── RED 1: the post-click return ───────────────────────────────────────────

def test_row774_a_click_that_leaves_the_origin_is_not_a_submit(sweep):
    page = _Page(on_click={TEXT_MATCHED: GOOGLE})
    ok, label = sweep(page)
    # The fixture built the shape: the text-matched button was clicked once
    # and the page really is on Google now.
    assert page.clicks == [TEXT_MATCHED], page.clicks
    assert page.url == GOOGLE
    assert ok is False, (
        f"a cross-origin navigation was accepted as submit success: "
        f"{(ok, label)!r}")
    assert "cross-origin" in label and "accounts.google.com" in label, label
    # Exact count: the sweep stops at the first candidate that navigates,
    # so the safe candidate is never clicked on Google's page and the origin
    # predicate fired once for the login URL and once for the refusal.
    assert page.clicks == [TEXT_MATCHED]
    assert sweep.counted["origin"] == 2, sweep.counted


# ── RED 2: the "navigated before {label}" pre-check ────────────────────────

def _click_moves_then_times_out(page):
    page.url = GOOGLE
    raise TimeoutError("fixture: Timeout 2000ms exceeded (navigation began)")


def test_row774_a_page_already_on_another_origin_is_not_an_earlier_submit(sweep):
    page = _Page(on_click={TEXT_MATCHED: _click_moves_then_times_out})
    ok, label = sweep(page)
    # Method 1 reported failure (its click raised) yet the page moved, so the
    # sweep reaches method 2's pre-check with page.url already on Google.
    assert page.clicks and page.url == GOOGLE, (page.clicks, page.url)
    assert ok is False, (
        f"a cross-origin URL was accepted as 'navigated before': "
        f"{(ok, label)!r}")
    assert "cross-origin" in label and "before" in label, label


class _LateRedirectPage(_Page):
    """The click itself leaves the URL alone; the redirect lands while the
    sweep waits for networkidle (the second observation site in the poll)."""

    def wait_for_load_state(self, *a, **kw):
        self.url = GOOGLE
        return None


def test_row774_a_redirect_that_lands_during_networkidle_is_still_refused(sweep):
    page = _LateRedirectPage(on_click={TEXT_MATCHED: lambda page: None})
    ok, label = sweep(page)
    assert page.clicks == [TEXT_MATCHED] and page.url == GOOGLE, (page.clicks, page.url)
    assert ok is False, (
        f"a cross-origin navigation seen after networkidle was accepted: "
        f"{(ok, label)!r}")
    assert "cross-origin" in label and "accounts.google.com" in label, label


# ── The rule is stated, and the predicate is the one the gate path has ─────

def test_row774_the_rule_is_stated_on_the_predicate_it_reuses():
    """Reads the docstring and the import only; never drives a sweep. This is
    the transform-control band node for tests/mutants/row774_*_transform_control.json."""
    from bulk_downloader import interstitial
    from bulk_downloader.login_impl import submit
    doc = submit._submit_login.__doc__ or ""
    assert "within the login page's origin" in doc.lower(), doc[:200]
    assert "accounts.google.com" in doc, doc[:200]
    assert submit._origin is interstitial._origin


# ── Negative controls ──────────────────────────────────────────────────────

def test_row774_a_same_origin_navigation_is_still_the_submit_it_was(sweep):
    page = _Page(on_click={SAFE: "https://www.bang.com/members?welcome=1"})
    ok, label = sweep(page)
    assert page.clicks == [SAFE]
    assert (ok, label) == (True, "click submit selector"), (ok, label)
    assert sweep.counted["origin"] == 2, sweep.counted


def test_row774_a_default_port_is_the_same_origin(sweep):
    page = _Page(url="https://www.bang.com:443/login",
                 on_click={SAFE: "https://WWW.bang.com/members"})
    ok, label = sweep(page)
    assert (ok, label) == (True, "click submit selector"), (ok, label)


def test_row774_no_navigation_reads_exactly_as_before(sweep):
    page = _Page(on_click={SAFE: LOGIN_URL})
    ok, label = sweep(page)
    assert page.clicks == [SAFE]
    assert (ok, label) == (False, "no submit method produced navigation"), (ok, label)
    assert sweep.counted["origin"] <= 1, sweep.counted


def test_row774_a_subdomain_is_another_origin_until_declared(sweep):
    page = _Page(url="https://login.bang.com/login",
                 on_click={SAFE: "https://www.bang.com/members"})
    ok, label = sweep(page)
    assert ok is False and "cross-origin" in label, (ok, label)


# ── RED 3: do_login's own acceptance ───────────────────────────────────────

def _drive(monkeypatch, tmp_path, *, final_url, success_url=None,
           flow_ran=False, page=None, submit_result=True, submit_btn=None):
    """Drive the real do_login; replace only the browser/UI boundaries.
    `page` may carry a real click map, in which case the real _submit_login
    runs; otherwise _submit_login is stubbed to `submit_result`."""
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "fill": [], "close": 0}
    jar = [{"name": n, "value": "0" * 16} for n in ("pref_a", "pref_b")]
    real_sweep = page is not None
    page = page or _Page(url=LOGIN_URL)
    if not real_sweep:
        page.on_click = {}

    class _DrivenPage:
        """`url` is the login URL until the submit ran, then `final_url`
        (unless the real sweep already moved the fixture page)."""
        def __getattr__(self, name):
            return getattr(page, name)

        @property
        def url(self):
            if real_sweep:
                return page.url
            return final_url if calls["submit"] else LOGIN_URL

    driven = _DrivenPage()
    ctx = SimpleNamespace(new_page=lambda: driven, cookies=lambda: jar)
    browser = SimpleNamespace(new_context=lambda **kw: ctx,
                              close=lambda: calls.__setitem__("close", calls["close"] + 1))
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **kw: [])
    monkeypatch.setattr(submit, "time", _Clock())
    monkeypatch.setattr(submit, "replay_saved_login_flow",
                        lambda *a: {"ran": flow_ran, "steps": 3, "ok": True})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *a: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *a, **kw: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)

    def fill(page, selectors, value, role):
        calls["fill"].append(role)
        return True, "fixture field"
    monkeypatch.setattr(submit, "_try_fill", fill)

    if real_sweep:
        real_submit = submit._submit_login

        def counted_submit(page, sb, pf):
            calls["submit"] += 1
            return real_submit(page, sb, pf)
        monkeypatch.setattr(submit, "_submit_login", counted_submit)
    else:
        def submit_form(page, selectors, password_selectors):
            calls["submit"] += 1
            return submit_result, "fixture submit"
        monkeypatch.setattr(submit, "_submit_login", submit_form)

    # Documented zero-entropy password; no vault reference or real credential.
    config = {"login_url": LOGIN_URL, "username": "fixture",
              "password": "zero-entropy-password", "wait": 0,
              "use_real_chrome": False, "use_stealth": False,
              "use_stealth_library": False,
              "login_evidence_dir": str(tmp_path / "evidence")}
    if success_url:
        config["success_url"] = success_url
    if submit_btn:
        config["submit_btn"] = submit_btn
    result = submit.do_login(config, allow_manual_takeover=False)
    assert calls["submit"] == 1, calls
    assert calls["fill"] == ["username", "password"], calls
    return result, calls


def test_row774_do_login_refuses_a_final_url_on_another_origin(monkeypatch, tmp_path):
    result, _ = _drive(monkeypatch, tmp_path, final_url=GOOGLE)
    assert result[0] is not True, (
        f"do_login accepted a cross-origin final URL as OK: {result[:2]!r}")
    assert "cross-origin" in result[1] and "accounts.google.com" in result[1], result[1]
    assert result[2] == [], "no cookies are handed back from a refused login"


def test_row774_do_login_cookie_count_never_outranks_the_origin(monkeypatch, tmp_path):
    # success_url is a path fragment that the Google URL happens to contain:
    # it declares no origin, so the substring match cannot admit it.
    result, _ = _drive(monkeypatch, tmp_path, final_url=GOOGLE, success_url="/o/oauth2")
    assert result[0] is not True, result[:2]
    assert "cross-origin" in result[1], result[1]


def test_row774_do_login_same_origin_change_is_still_ok(monkeypatch, tmp_path):
    result, _ = _drive(monkeypatch, tmp_path,
                       final_url="https://www.bang.com/members?welcome=1",
                       success_url="/members")
    assert result[0] is True, result[:2]
    assert result[1].startswith("OK"), result[1]
    assert len(result[2]) == 2


def test_row774_a_declared_cross_origin_flow_still_passes(monkeypatch, tmp_path):
    # v3.66.302: the captured multi-step flow ran for this site -- that is
    # the explicit declaration, so ending on the flow's origin is accepted.
    result, _ = _drive(monkeypatch, tmp_path, flow_ran=True,
                       final_url="https://members.bang.com/home")
    assert result[0] is True, result[:2]
    assert result[1].startswith("OK"), result[1]


def test_row774_an_absolute_success_url_declares_the_destination_origin(monkeypatch, tmp_path):
    # login.example.com -> www.example.com is a DECLARED allow: the operator
    # named the destination origin in success_url.
    result, _ = _drive(monkeypatch, tmp_path,
                       final_url="https://members.bang.com/home?x=1",
                       success_url="https://members.bang.com/home")
    assert result[0] is True, result[:2]


def test_row774_the_product_sweep_reaching_the_google_button_does_not_log_in(monkeypatch, tmp_path):
    # The runbook's operator selector for bang.com (SITE_RUNBOOK.md L1145-1158)
    # is the text-matched one; it reaches LOGIN WITH GOOGLE first.
    page = _Page(on_click={TEXT_MATCHED: GOOGLE})
    result, calls = _drive(monkeypatch, tmp_path, final_url=GOOGLE, page=page,
                           submit_btn=TEXT_MATCHED)
    assert page.clicks == [TEXT_MATCHED], page.clicks
    assert page.url == GOOGLE
    assert result[0] is not True, (
        f"the sweep reaching LOGIN WITH GOOGLE was reported as a login: "
        f"{result[:2]!r}")
    assert "cross-origin" in result[1], result[1]
