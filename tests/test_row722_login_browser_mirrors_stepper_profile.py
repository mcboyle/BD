"""Row 722 G23 -- the login browser launches with the SAME profile as the
stepper (``cloak.cloaked_page``), not a hand-built one.

Live evidence (test2, login_evidence manual-takeover-expected-url-*): the app's
login browser filled + submitted and the page answered "This site uses some
functionality that was blocked by your browser" (Castle.io request token
missing) -- twice -- while the harness stepper rendered the same page cleanly
via ``cloaked_page(headless=False)`` with no args, no config, no channel.

Measured difference (bulk_downloader/cloak.py + cloakbrowser.build_args):
  * ``launch_browser(args=[...hand-built...])`` -- cloakbrowser merges caller
    args OVER its stealth defaults by flag key, so the login's own
    ``--disable-features=...AutomationControlled`` / ``--enable-features``
    / ``--disable-blink-features`` replaced the profile the patched binary
    ships, and ``--window-size=1366,800`` suppressed its ``--start-maximized``
    (window/screen coherence).  ``cloaked_page`` passes ``args=None``.
  * ``channel="chrome"`` was added whenever ``use_real_chrome`` was ABSENT
    (implicit default True) -- on the Playwright backend that is stock system
    Chrome with none of the stealth profile.  ``cloaked_page`` passes none.

Operator decision: "Mirror stepper profile in product."  So the login launch
must record the same ``launch_browser`` kwargs as ``cloaked_page`` (asserted
against the cloak module itself, not a hand-copied list), and the system-Chrome
channel survives ONLY when ``use_real_chrome`` is explicitly True.
"""
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"

PROFILE_KEYS = ("headless", "args", "channel")


class _Sentinel(RuntimeError):
    """Raised by the recorder so do_login stops at the launch."""


def _profile(kw):
    return {k: kw.get(k) for k in PROFILE_KEYS}


def _record_login_launch(monkeypatch, tmp_path, config_extra):
    """Drive the real do_login up to cloak.launch_browser and record its kwargs.

    The recorder raises after recording; do_login's outer handler turns that into
    a ``login error`` result, so nothing after the launch runs.  Returns the list
    of recorded kwarg dicts (the base code retries once without the channel, so
    the FIRST call is the profile the login path asks for).
    """
    from bulk_downloader import cloak
    from bulk_downloader.login_impl import submit

    calls = []

    def recorder(**kw):
        calls.append(dict(kw))
        raise _Sentinel("recorder: stop at launch")

    monkeypatch.setattr(cloak, "launch_browser", recorder)
    monkeypatch.setattr(cloak, "log_choice", lambda *a, **kw: None)
    monkeypatch.setattr(submit.time, "sleep", lambda seconds: None)
    # Documented zero-entropy password; no vault reference or real credential.
    config = {"login_url": "https://login.example.invalid/login",
              "username": "fixture", "password": "zero-entropy-password",
              "wait": 0, "use_stealth": False, "use_stealth_library": False,
              "login_evidence_dir": str(tmp_path / "evidence")}
    config.update(config_extra)
    result = submit.do_login(config, allow_manual_takeover=False)
    assert calls, "do_login never reached cloak.launch_browser"
    assert result[0] is False and "recorder: stop at launch" in result[1], result
    return calls


def _record_stepper_launch(monkeypatch):
    """What cloaked_page (the stepper's entry point) hands launch_browser."""
    from bulk_downloader import cloak

    calls = []

    def recorder(**kw):
        calls.append(dict(kw))
        raise _Sentinel("recorder: stop at launch")

    monkeypatch.setattr(cloak, "launch_browser", recorder)
    with pytest.raises(_Sentinel):
        with cloak.cloaked_page(headless=False,
                               context_options={"accept_downloads": False,
                                                "viewport": {"width": 1600, "height": 1000}}):
            pass  # pragma: no cover - the recorder raises before a page exists
    assert len(calls) == 1, calls
    return calls[0]


# ── THE ROW: login launch == stepper launch (no channel, cloak default args) ──

def test_login_launch_mirrors_cloaked_page_profile(monkeypatch, tmp_path):
    stepper = _profile(_record_stepper_launch(monkeypatch))
    login_calls = _record_login_launch(monkeypatch, tmp_path, {})
    login = _profile(login_calls[0])
    assert "channel" not in login_calls[0], (
        f"G23: login launch adds channel={login_calls[0]['channel']!r} while "
        f"use_real_chrome is NOT explicitly configured (stepper passes none)")
    assert login == stepper, (
        f"G23: login browser profile {login} != stepper/cloaked_page profile "
        f"{stepper} -- Castle.io blacks the hand-built one")
    assert stepper["args"] is None and stepper["channel"] is None, stepper


# ── negative control (a): explicit use_real_chrome=True keeps the channel ──

def test_explicit_use_real_chrome_true_keeps_system_chrome_channel(monkeypatch, tmp_path):
    login_calls = _record_login_launch(monkeypatch, tmp_path, {"use_real_chrome": True})
    assert login_calls[0].get("channel") == "chrome", login_calls[0]
    # ...and the retry-without-channel fallback still runs after a failed launch.
    assert len(login_calls) == 2 and "channel" not in login_calls[1], login_calls
    # the args are STILL the cloak default -- only the channel differs
    assert login_calls[0].get("args") is None, login_calls[0]


def test_explicit_use_real_chrome_false_adds_no_channel(monkeypatch, tmp_path):
    login_calls = _record_login_launch(monkeypatch, tmp_path, {"use_real_chrome": False})
    assert len(login_calls) == 1 and "channel" not in login_calls[0], login_calls


# ── negative control (b): cloaked_page itself is unchanged ──

def test_cloaked_page_profile_unchanged(monkeypatch):
    kw = _record_stepper_launch(monkeypatch)
    assert kw == {"headless": False, "args": None, "config": None}, kw


# ── the one-line profile log names the backend / the explicit choice ──

def test_login_logs_browser_profile_line(monkeypatch, tmp_path, capsys):
    _record_login_launch(monkeypatch, tmp_path, {})
    err = capsys.readouterr().err
    assert "login: browser profile = cloak default (" in err, err
    _record_login_launch(monkeypatch, tmp_path, {"use_real_chrome": True})
    err = capsys.readouterr().err
    assert "login: browser profile = system chrome (use_real_chrome explicit)" in err, err
