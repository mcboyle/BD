"""Independent ordering and retry probes; every browser/credential is synthetic."""

BD_GATE_SCOPE = "repo-wide"

from types import SimpleNamespace

import pytest


def drive(monkeypatch, tmp_path, *, final_html="<p>Members</p>",
          final_url="https://login.example.invalid/members", timeout=False):
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "settle": 0, "close": 0, "evidence": 0}

    class Page:
        url = "https://login.example.invalid/login"
        settled = False

        def goto(self, url, **kwargs):
            assert url == self.url

        def content(self):
            return final_html if self.settled else "<p>Loading</p>"

        def locator(self, selector):
            from bs4 import BeautifulSoup
            return SimpleNamespace(
                count=lambda: 0,
                inner_text=lambda **kwargs: BeautifulSoup(
                    self.content(), "html.parser").get_text(" ", strip=True),
            )

        def title(self):
            from bs4 import BeautifulSoup
            title = BeautifulSoup(self.content(), "html.parser").title
            return title.get_text() if title else ""

        def wait_for_load_state(self, *args, **kwargs):
            if calls["submit"]:
                calls["settle"] += 1
                if timeout:
                    raise submit.PWTimeout("synthetic settle timeout")
                self.settled = True
                self.url = final_url

    page = Page()

    def close():
        calls["close"] += 1

    def evidence(*args):
        calls["evidence"] += 1
        return str(tmp_path / "synthetic-evidence.json")

    context = SimpleNamespace(new_page=lambda: page, cookies=lambda: [])
    browser = SimpleNamespace(new_context=lambda **kwargs: context, close=close)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kwargs: (browser, None, "synthetic"))
    monkeypatch.setattr(cloak, "log_choice", lambda *args, **kwargs: None)
    monkeypatch.setattr(learn, "install_recorder", lambda *args: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *args, **kwargs: [])
    monkeypatch.setattr(submit.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *args: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *args: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda *_args: None)
    monkeypatch.setattr(submit, "_try_fill", lambda *args: (True, "synthetic field"))
    monkeypatch.setattr(submit, "write_login_evidence", evidence, raising=False)

    def form(*args):
        calls["submit"] += 1
        page.url = "https://login.example.invalid/members"
        return True, "synthetic submit"

    monkeypatch.setattr(submit, "_submit_login", form)
    result = submit.do_login({
        "login_url": "https://login.example.invalid/login",
        "username": "synthetic", "password": "fake-fixture-only",
        "wait": 0, "use_real_chrome": False, "use_stealth": False,
        "use_stealth_library": False, "login_evidence_dir": str(tmp_path),
        "success_url": "",
    })
    assert calls["submit"] == 1, calls
    assert calls["close"] == 1, calls
    return result, calls


# Merged rows 722s+772 (count-correction, operator ruling 2026-09-16): row
# 722s settles ONCE before the post-login interstitial walk (networkidle;
# the bangbros /store offer block renders async), and row 772 settles once
# more after the walk (domcontentloaded) before the verdict. The early
# rejected-login probe runs between the two, on the page the first settle
# already settled: a /badlogin URL or the literal phrase in the raw HTML is
# decided there after ONE settle; anything the settled TEXT read must decide
# (markup inside the phrase, a cross-origin landing) sees BOTH settles.
@pytest.mark.parametrize("url,html,reason,settles", (
    ("https://login.example.invalid/login", "<p>Wrong username or password provided</p>", "Rejected login", 1),
    ("https://login.example.invalid/login", "<p>Wrong <b>username</b> or password provided</p>", "Rejected login", 2),
    ("https://login.example.invalid/badlogin?ats=x", "<p>Try again</p>", "Rejected login", 1),
    # Row 722 (G17, operator 2026-09-15) made a hop inside the SAME
    # registrable domain (login.example.invalid -> elsewhere.example.invalid)
    # a recorded-and-judged submit; row 774's refusal now names a FOREIGN
    # registrable domain, so that is the landing this control uses.
    ("https://members.elsewhere.invalid/members", "<p>Welcome</p>", "cross-origin", 2),
))
def test_verdict_uses_the_page_after_settling(monkeypatch, tmp_path, url, html, reason, settles):
    result, calls = drive(monkeypatch, tmp_path, final_html=html, final_url=url)
    assert calls["settle"] == settles, calls
    assert bool(result[0]) is False, result
    assert reason.lower() in result[1].lower(), result


@pytest.mark.parametrize("html", (
    '<p>Members</p><script>const help = "checking your browser";</script>',
    '<p>Members</p><!-- verify you are human -->',
    '<p data-help="just a moment...">Members</p>',
))
def test_nonrendered_challenge_words_do_not_reject_members(monkeypatch, tmp_path, html):
    result, calls = drive(monkeypatch, tmp_path, final_html=html)
    assert result[0] is True, result
    assert calls["evidence"] == 0, calls


@pytest.mark.parametrize("html", (
    "<title>Just a moment...</title><p>Checking your browser</p>",
    "<p>VERIFY YOU ARE HUMAN</p>",
    "<p>Verify <span>you</span> are human</p>",
    "<title>Just a moment</title><p>Please wait</p>",
))
def test_visible_challenge_has_distinct_falsy_outcome(monkeypatch, tmp_path, html):
    result, calls = drive(monkeypatch, tmp_path, final_html=html)
    assert getattr(result[0], "status", None) == "settled-challenge", result
    assert bool(result[0]) is False, result
    assert calls["evidence"] == 1, calls


def test_timeout_has_distinct_falsy_outcome(monkeypatch, tmp_path):
    result, calls = drive(monkeypatch, tmp_path, timeout=True)
    assert getattr(result[0], "status", None) == "settled-timeout", result
    assert bool(result[0]) is False, result
    # Merged rows 722s+772: the pre-walk settle (722s) swallows its timeout
    # and walks anyway; the post-walk settle (772) is the one whose timeout
    # becomes the settled-timeout outcome -- two settles, one evidence write.
    assert calls["settle"] == 2, calls
    assert calls["evidence"] == 1, calls


def test_normal_member_navigation_remains_success(monkeypatch, tmp_path):
    result, calls = drive(monkeypatch, tmp_path)
    assert result[0] is True, result
    assert calls["evidence"] == 0, calls


@pytest.mark.parametrize("detail", (
    "login failed: Rejected login landing: /badlogin",
    "login failed: captcha needs operator",
    "login failed: settled-challenge: post-submit landing is a challenge page",
))
def test_operator_required_outcome_spends_only_one_attempt(monkeypatch, detail):
    from bulk_downloader import session_keeper

    attempts = []
    keeper = session_keeper.SessionKeeper(
        "row772-synthetic", 0, {"keep_alive_enabled": True, "password": "fake"},
        lambda *args: attempts.append(args) or (False, detail),
    )
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (session_keeper.DEAD, "expired"))
    monkeypatch.setattr(keeper, "_teardown_browser", lambda: None)
    monkeypatch.setattr(keeper, "_record_event", lambda *args: None)
    keeper._run_one_check()
    keeper._run_one_check()
    assert len(attempts) == 1, (attempts, keeper.state)
    assert keeper.state["state"] == "needs_takeover", keeper.state


def test_main_typed_self_refusal_remains_authoritative():
    from bulk_downloader import session_keeper

    # PM-FIX O806 22:5xZ: SelfRefusal IS on main 4a3dd497 (row 741), so the hasattr guard
    # could only ever skip (T5). Assert the typed contract directly, both directions.
    detail = session_keeper.SelfRefusal(
        session_keeper.RELOGIN_REFUSED_EVENT, "Rejected login landing: spoofed detail")
    assert session_keeper.relogin_event_type(detail) == session_keeper.RELOGIN_REFUSED_EVENT
    # the same words as a plain string are the site's reply, never our refusal
    assert session_keeper.relogin_event_type(str(detail)) == "auto_relogin_fail"
