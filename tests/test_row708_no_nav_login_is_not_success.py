"""Row 708 -- a login with NO NAV SIGNAL must never be SUCCEEDED.

v3.66.1497 (row B) narrowed WHICH cookie jars pass the no-nav recovery
branches: a jar must now show four NEW substantial cookie names, not merely
four cookies. That is the floor this row builds on, and it is not the fix.
The cookie jar was still the thing that DECIDED, so a trial session that
sets four new cookies -- or any bot-detection layer that seeds a jar on the
POST -- was reported as a successful login, and the failure surfaced later,
on the download.

Row 708's acceptance, verbatim from the register: "a login with NO NAV
SIGNAL must never be SUCCEEDED; require a navigation or a declared
success_url, and degrade the cookie-count-only outcome to a DISTINCT
settled-no-nav state that is not success, so the two are not one value."
Its stronger clause: a positive MEMBER-STATE check on the page the run
actually read, with the rendered page kept as evidence.

So this gate asserts three separable things, and each has its own mutant:

  1. THE STATE EXISTS AND IS DISTINCT. The no-nav recovery paths return a
     `LoginOutcome` whose status is "settled-no-nav" and whose `ok` is
     False. It is not True, it is not False, and it is not equal to either
     -- "the two are not one value" is an assertion about VALUES, so a
     falsy alias of False would satisfy the letter and lose the row.
  2. TRUE IS REACHABLE ONLY THROUGH A POSITIVE CHECK. Navigation, or a
     declared success_url that MATCHES the URL actually read, or a declared
     member indicator that is PRESENT on the page actually read. No cookie
     signal -- neither the row-B delta nor an auth-named cookie -- reaches
     True on a no-nav path any more.
  3. THE PAGE READ IS KEPT. When a member-state check is performed, the
     rendered page and its final URL are written to the run record and the
     path is reported, because a verdict about a page nobody can re-read is
     not evidence.

The three no-nav recovery branches are all covered, because the defect is
the SHAPE and not the branch: "ajax" (submit reported not-ok), "closed"
(the page vanished mid-submit) and "post_closed" (the URL became unreadable
after an apparently successful submit). Fixing one and leaving the other
two is the sibling-seam escape CLAUDE.md M44 names.
"""
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

SETTLED = "settled-no-nav"


def _jar(names):
    # Documented zero-entropy cookie fixtures, never live session values.
    return [{"name": name, "value": "0" * 16} for name in names]


class _Locator:
    def __init__(self, n):
        self._n = n

    def count(self):
        return self._n


def _drive(monkeypatch, tmp_path, *, branch="ajax", before=None, after=None,
           success_url="/members", final_url="https://login.example.invalid/login",
           submit_result=None, member_indicator=None, indicator_present=False,
           allow_manual=False, unreadable_content=False):
    """Drive the real do_login; replace only the browser/UI boundaries.

    `submit_result` is what the stubbed _submit_login returns: False /
    "PAGE_CLOSED" for the no-nav branches, True for a navigating submit.
    """
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    before = _jar(["pref_a", "pref_b", "pref_c", "pref_d"]) if before is None else before
    after = (before + _jar(["member_a", "member_b", "member_c", "member_d"])
             if after is None else after)
    if submit_result is None:
        submit_result = {"ajax": False, "closed": "PAGE_CLOSED",
                         "post_closed": True}[branch]

    calls = {"submit": 0, "fill": [], "reads": [], "close": 0,
             "content": 0, "locator": [], "handoff": 0}
    jar = [dict(cookie) for cookie in before]
    login_url = "https://login.example.invalid/login"

    class Page:
        @property
        def url(self):
            if not calls["submit"]:
                return login_url
            if branch == "post_closed":
                raise RuntimeError("fixture page closed after submit")
            return final_url

        def goto(self, url, **kwargs):
            assert url == login_url

        def content(self):
            calls["content"] += 1
            if unreadable_content:
                raise RuntimeError("fixture page content unavailable")
            return "<html><body>fixture members page</body></html>"

        def locator(self, selector):
            calls["locator"].append(selector)
            return _Locator(1 if indicator_present else 0)

        def wait_for_load_state(self, *a, **kw):
            return None

    page = Page()

    def cookies():
        phase = "after" if calls["submit"] else "before"
        calls["reads"].append(phase)
        return jar

    def close():
        calls["close"] += 1

    ctx = SimpleNamespace(new_page=lambda: page, cookies=cookies)
    browser = SimpleNamespace(new_context=lambda **kw: ctx, close=close)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kw: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *a: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *a, **kw: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *a: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *a: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *a, **kw: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)

    def fill(page, selectors, value, role):
        calls["fill"].append(role)
        return True, "fixture field"

    def submit_form(page, selectors, password_selectors):
        calls["submit"] += 1
        jar[:] = [dict(cookie) for cookie in after]
        return submit_result, "fixture submit"

    monkeypatch.setattr(submit, "_try_fill", fill)
    monkeypatch.setattr(submit, "_submit_login", submit_form)

    # Documented zero-entropy password; no vault reference or real credential.
    config = {"login_url": login_url, "username": "fixture",
              "password": "zero-entropy-password", "wait": 0,
              "use_real_chrome": False, "use_stealth": False,
              "use_stealth_library": False,
              "login_evidence_dir": str(tmp_path / "evidence")}
    if success_url:
        config["success_url"] = success_url
    if member_indicator:
        config["learned"] = {"login": {"member_indicator": member_indicator}}

    result = submit.do_login(config, allow_manual_takeover=allow_manual)
    # Preconditions: the fixture really built the shape this gate is about.
    assert calls["submit"] == 1, calls
    assert calls["fill"] == ["username", "password"], calls
    assert calls["reads"] == ["before", "after"], calls
    return result, calls


def _status(outcome):
    return getattr(outcome, "status", None)


# ── 1. the state exists, is distinct, and is what the no-nav paths return ──

@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_no_nav_cookie_delta_is_settled_no_nav_not_success(monkeypatch, tmp_path, branch):
    """THE ROW. Four NEW substantial cookies, no navigation, a declared
    success_url that the page actually read does not match -> the cookie
    jar must not decide. Base returns True here; that is the defect."""
    result, calls = _drive(monkeypatch, tmp_path, branch=branch)
    outcome = result[0]
    assert _status(outcome) == SETTLED and getattr(outcome, "ok", None) is False, (
        f"[{branch}] a no-nav login was decided by the cookie jar alone: "
        f"do_login returned {outcome!r} / {result[1]!r}")
    assert SETTLED in result[1], result[1]
    assert "NOT success" in result[1], result[1]
    assert calls["close"] == 1, calls


@pytest.mark.parametrize("branch", ["ajax", "closed", "post_closed"])
def test_settled_no_nav_is_not_the_same_value_as_success_or_failure(
        monkeypatch, tmp_path, branch):
    """"...so the two are not one value." A falsy alias of False would pass
    every `if ok:` in the tree and still lose the row: the state has to be
    READABLE as its own value, not merely falsy."""
    outcome = _drive(monkeypatch, tmp_path, branch=branch)[0][0]
    assert outcome is not True and outcome is not False, repr(outcome)
    assert not (outcome == True), repr(outcome)      # noqa: E712 -- value identity is the assertion
    assert not (outcome == False), repr(outcome)     # noqa: E712
    assert bool(outcome) is False, (
        "settled-no-nav must be falsy so every existing `if ok:` consumer "
        "treats it as NOT success")
    assert _status(outcome) == SETTLED


def test_an_auth_named_cookie_alone_is_also_not_a_navigation(monkeypatch, tmp_path):
    """The row-B delta is not the only cookie signal. An explicitly
    auth-named cookie is still a COOKIE, and on a no-nav path it is still
    the jar deciding. Without this case the fix would be a narrowing of
    which cookies decide rather than a removal of cookies as the decider."""
    jar = [{"name": "logged_in", "value": "1"}]
    outcome = _drive(monkeypatch, tmp_path, branch="ajax", before=jar, after=jar)[0][0]
    assert _status(outcome) == SETTLED, repr(outcome)


# ── 2. True is reachable only through a positive member-state check ──

def test_navigation_with_matching_success_url_still_succeeds(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: a navigating submit that lands on the success URL
    is untouched by this cut."""
    result, _ = _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True,
                       success_url="/members",
                       final_url="https://login.example.invalid/members/home")
    assert result[0] is True, result[1]
    assert len(result[2]) == 8


def test_navigation_without_any_declared_success_url_still_succeeds(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: row 708 requires "a navigation OR a declared
    success_url". A navigation with no success_url declared is success."""
    result, _ = _drive(monkeypatch, tmp_path, branch="ajax", submit_result=True,
                       success_url=None,
                       final_url="https://login.example.invalid/whatever")
    assert result[0] is True, result[1]


def test_no_nav_but_success_url_matches_the_page_read_succeeds(monkeypatch, tmp_path):
    """NEGATIVE CONTROL and the positive-check path: no navigation event,
    but the page the run ACTUALLY read is the declared success URL. That is
    a positive member-state check, so it succeeds -- and the page it read
    is kept."""
    result, calls = _drive(monkeypatch, tmp_path, branch="ajax",
                           success_url="/members",
                           final_url="https://login.example.invalid/members/home")
    assert result[0] is True, result[1]
    assert calls["content"] == 1, calls
    assert "evidence" in result[1], result[1]


def test_no_nav_with_declared_success_url_that_does_not_match_is_settled(
        monkeypatch, tmp_path):
    """NEGATIVE CONTROL: declared but unmatched is not a positive check."""
    outcome = _drive(monkeypatch, tmp_path, branch="ajax", success_url="/members",
                     final_url="https://login.example.invalid/login?error=1")[0][0]
    assert _status(outcome) == SETTLED, repr(outcome)


def test_member_indicator_present_on_the_page_read_succeeds(monkeypatch, tmp_path):
    """The template's learned member indicator is the second positive
    check the row allows. It is read from the page the run actually read,
    never invented from cookie names or page length."""
    result, calls = _drive(monkeypatch, tmp_path, branch="ajax", success_url=None,
                           member_indicator="a.logout", indicator_present=True)
    assert result[0] is True, result[1]
    assert calls["locator"] == ["a.logout"], calls


def test_member_indicator_absent_on_the_page_read_is_settled(monkeypatch, tmp_path):
    outcome = _drive(monkeypatch, tmp_path, branch="ajax", success_url=None,
                     member_indicator="a.logout", indicator_present=False)[0][0]
    assert _status(outcome) == SETTLED, repr(outcome)


def test_no_declared_check_at_all_can_never_reach_success(monkeypatch, tmp_path):
    """UNKNOWN is not permission (CLAUDE.md A2). With neither a success_url
    nor a member indicator there is nothing positive to check, so the
    no-nav path settles -- it does not fall back to the cookie jar."""
    outcome = _drive(monkeypatch, tmp_path, branch="ajax", success_url=None)[0][0]
    assert _status(outcome) == SETTLED, repr(outcome)


def test_unreadable_final_url_is_unknown_and_never_success(monkeypatch, tmp_path):
    """UNKNOWN branch: the member-state check cannot be performed at all.
    This is the arm that stays green when it is deleted unless it is
    asserted directly."""
    outcome = _drive(monkeypatch, tmp_path, branch="post_closed",
                     success_url="/members")[0][0]
    assert _status(outcome) == SETTLED, repr(outcome)
    assert getattr(outcome, "evidence_path", "sentinel") is None, (
        "a page whose URL could not be read has no rendered evidence")


def test_manual_takeover_is_not_offered_for_a_settled_no_nav_login(monkeypatch, tmp_path):
    """NEGATIVE CONTROL: settled-no-nav is a settled outcome, not a
    failure to submit, so it must not divert into the interactive
    hand-off that unconvincing cookies take."""
    result, calls = _drive(monkeypatch, tmp_path, branch="ajax", allow_manual=True)
    assert _status(result[0]) == SETTLED, repr(result[0])
    assert result[0] != "MANUAL_PENDING"
    assert calls["close"] == 1, "the browser must still be closed"


def test_unconvincing_cookies_still_fail_exactly_as_before(monkeypatch, tmp_path):
    """NEGATIVE CONTROL that fails for the intended reason: this cut
    changes only the branch that used to return True. A jar that never
    convinced anyone still returns literal False."""
    before = _jar(["pref_a"])
    result, _ = _drive(monkeypatch, tmp_path, branch="ajax", before=before, after=before)
    assert result[0] is False, repr(result[0])
    assert "Submit failed" in result[1], result[1]


# ── 3. the page read is kept as evidence ──

def test_the_page_actually_read_is_written_to_the_run_record(monkeypatch, tmp_path):
    """A verdict about a page nobody can re-read is not evidence."""
    result, calls = _drive(monkeypatch, tmp_path, branch="ajax", success_url="/members",
                           final_url="https://login.example.invalid/login?error=1")
    outcome = result[0]
    assert _status(outcome) == SETTLED
    path = getattr(outcome, "evidence_path", None)
    assert path, f"no rendered page kept for {outcome!r}"
    assert path in result[1], result[1]
    body = open(path, encoding="utf-8").read()
    assert "https://login.example.invalid/login?error=1" in body, body[:200]
    assert "fixture members page" in body, body[:200]
    assert calls["content"] == 1, calls


def test_evidence_failure_is_not_upgraded_to_success(monkeypatch, tmp_path):
    """If the rendered page cannot be captured the verdict does not
    improve: the check still decides on the URL actually read."""
    result, _ = _drive(monkeypatch, tmp_path, branch="ajax", success_url="/members",
                       final_url="https://login.example.invalid/login",
                       unreadable_content=True)
    assert _status(result[0]) == SETTLED, repr(result[0])


def test_transform_control_only_imports_the_seam(monkeypatch, tmp_path):
    """Transform control: imports the subject, asserts no behaviour. Every
    mutant pointed here must ESCAPE, which proves the CAUGHTs above are
    assertion failures and not import breaks."""
    from bulk_downloader.login_impl import submit
    assert callable(submit.do_login)
