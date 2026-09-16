"""Row 772 residual -- a navigated login must settle before it succeeds."""

from types import SimpleNamespace


BD_GATE_SCOPE = "module"


def _drive(monkeypatch, tmp_path, *, landing, settle_timeout=False):
    """Run the real post-submit seam against a synthetic, same-origin page."""
    from bulk_downloader import cloak, interstitial, learn, stealth
    from bulk_downloader.login_impl import submit

    calls = {"submit": 0, "settle": 0}

    class Page:
        url = "https://login.example.invalid/login"

        def goto(self, url, **kwargs):
            assert url == "https://login.example.invalid/login"

        def content(self):
            return landing[1]

        def locator(self, selector):
            return SimpleNamespace(count=lambda: 0)

        def wait_for_load_state(self, *args, **kwargs):
            if calls["submit"]:
                calls["settle"] += 1
                if settle_timeout:
                    raise submit.PWTimeout("fixture post-submit timeout")

    page = Page()
    ctx = SimpleNamespace(new_page=lambda: page, cookies=lambda: [])
    browser = SimpleNamespace(new_context=lambda **kwargs: ctx, close=lambda: None)
    monkeypatch.setattr(cloak, "launch_browser", lambda **kwargs: (browser, None, "fixture"))
    monkeypatch.setattr(cloak, "log_choice", lambda *args, **kwargs: None)
    monkeypatch.setattr(learn, "install_recorder", lambda page: None)
    monkeypatch.setattr(stealth, "apply_to_page", lambda *args, **kwargs: None)
    monkeypatch.setattr(interstitial, "dismiss_gates", lambda *args, **kwargs: [])
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(submit, "replay_saved_login_flow", lambda *args: {"ran": False})
    monkeypatch.setattr(submit, "_fire_login_trigger_if_needed", lambda *args: (False, False, ""))
    monkeypatch.setattr(submit, "_wait_captcha_tokens", lambda *args, **kwargs: (None, 0))
    monkeypatch.setattr(submit, "_try_check_remember_me", lambda page: None)
    monkeypatch.setattr(submit, "_try_fill", lambda *args: (True, "fixture field"))

    def submit_form(*args):
        calls["submit"] += 1
        page.url = landing[0]
        return True, "fixture submit"

    monkeypatch.setattr(submit, "_submit_login", submit_form)
    result = submit.do_login({
        "login_url": "https://login.example.invalid/login",
        "username": "fixture", "password": "zero-entropy-password",
        "wait": 0, "use_real_chrome": False, "use_stealth": False,
        "use_stealth_library": False, "login_evidence_dir": str(tmp_path),
    })
    assert calls["submit"] == 1, calls
    return result, calls


def test_post_submit_challenge_is_a_distinct_non_success(monkeypatch, tmp_path):
    result, _ = _drive(
        monkeypatch, tmp_path,
        landing=("https://login.example.invalid/challenge",
                 "<title>Just a moment...</title><p>Checking your browser</p>"),
    )
    assert getattr(result[0], "status", None) == "settled-challenge", result
    assert bool(result[0]) is False, result


def test_post_submit_timeout_is_a_distinct_non_success(monkeypatch, tmp_path):
    result, calls = _drive(
        monkeypatch, tmp_path,
        landing=("https://login.example.invalid/members", "<p>members</p>"),
        settle_timeout=True,
    )
    assert getattr(result[0], "status", None) == "settled-timeout", result
    assert bool(result[0]) is False, result
    # Merged rows 722s+772 (count-correction, operator ruling 2026-09-16):
    # row 722s settles once before the post-login interstitial walk and
    # swallows that timeout; row 772's post-walk settle is the second call
    # and the one whose timeout is this outcome. Pinned 1 before 722s.
    assert calls["settle"] == 2, calls


def test_ordinary_navigation_without_success_url_still_succeeds(monkeypatch, tmp_path):
    result, _ = _drive(
        monkeypatch, tmp_path,
        landing=("https://login.example.invalid/members", "<p>members</p>"),
    )
    assert result[0] is True, result


def test_credential_rejection_waits_for_an_explicit_config_update(monkeypatch):
    from bulk_downloader import session_keeper

    calls = []
    keeper = session_keeper.SessionKeeper(
        "row772-fixture", 0, {"keep_alive_enabled": True, "password": "fixture"},
        lambda *args: calls.append(args) or (
            False, "login failed: Rejected login landing: /badlogin"),
    )
    monkeypatch.setattr(keeper, "_heartbeat", lambda: (session_keeper.DEAD, "expired"))
    monkeypatch.setattr(keeper, "_teardown_browser", lambda: None)
    monkeypatch.setattr(keeper, "_record_event", lambda *args: None)

    keeper._run_one_check()
    keeper._run_one_check()

    assert len(calls) == 1, "a known rejected credential retried into the cap"
    assert keeper.state["state"] == "needs_takeover"

    # A deliberate configuration write is the resume operation: it permits
    # one new attempt after the password is repaired, rather than making a
    # background timer keep replaying the rejected input.
    key = (keeper.site_id, keeper.account_idx)
    with session_keeper._state_lock:
        session_keeper._keepers[key] = keeper
    try:
        session_keeper.update_config(
            keeper.site_id, keeper.account_idx,
            {"keep_alive_enabled": True, "password": "repaired-fixture"},
        )
        keeper._run_one_check()
    finally:
        with session_keeper._state_lock:
            session_keeper._keepers.pop(key, None)
    assert len(calls) == 2, "a credential repair did not enable one new attempt"
