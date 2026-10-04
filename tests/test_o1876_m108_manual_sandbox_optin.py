"""O1876 M108 -- opt-in Chromium sandbox for the manual-login browser.

--no-sandbox was hardcoded at the launch layer (cloak.STANDARD_LAUNCH_FLAGS, merged
into every launch, and again in manual._manual_launch_kwargs), cloakbrowser's own
stealth defaults add it, and Playwright adds it whenever chromium_sandbox is unset.
cloak.launch_browser / cloak.open_persistent_context now take sandbox=False; True
strips every --no-sandbox, passes chromium_sandbox=True on both backends and, on the
cloak backend, supplies cloakbrowser's stealth defaults minus --no-sandbox
(stealth_args=False). manual.py asks for it only when the per-site setting
manual_login_chromium_sandbox is literally True. Root (euid 0) falls back to the
unsandboxed launch with one log line.

No real browser: Playwright and the cloak launchers are fakes that record kwargs.
"""

BD_GATE_SCOPE = "module"

import sys
import types

import pytest

import bulk_downloader.cloak as cloak
from bulk_downloader.login_impl import manual

FAKE_STEALTH = ["--no-sandbox", "--fingerprint=12345", "--fingerprint-platform=windows"]


class _Obj:
    def add_init_script(self, *_a, **_k):
        pass


def _install_fakes(m, backend, calls, *, fake_cloak_config=True):
    """Route both cloak launch functions to recording fakes for ``backend``."""
    m.setattr(cloak, "_WARNED_LAUNCH_FALLBACK", True)
    m.setattr(cloak, "resolve_backend", lambda config=None, **_k: backend)

    class _Chromium:
        def launch(self, **kw):
            calls.append(("pw.launch", kw))
            return _Obj()

        def launch_persistent_context(self, **kw):
            calls.append(("pw.launch_persistent_context", kw))
            return _Obj()

    class _PW:
        chromium = _Chromium()

        def stop(self):
            pass

    class _Starter:
        def start(self):
            return _PW()

    pw_mod = types.ModuleType("playwright.sync_api")
    pw_mod.sync_playwright = lambda: _Starter()
    m.setitem(sys.modules, "playwright.sync_api", pw_mod)

    def _lpc(**kw):
        calls.append(("cloak.launch_persistent_context", kw))
        return _Obj()

    def _launch(**kw):
        calls.append(("cloak.launch", kw))
        return _Obj()

    m.setattr(cloak, "_CLOAK_LPC", _lpc)
    if fake_cloak_config:
        cb = types.ModuleType("cloakbrowser")
        cb_cfg = types.ModuleType("cloakbrowser.config")
        cb_cfg.get_default_stealth_args = lambda: list(FAKE_STEALTH)
        cb.config = cb_cfg
        cb.launch = _launch
        m.setitem(sys.modules, "cloakbrowser", cb)
        m.setitem(sys.modules, "cloakbrowser.config", cb_cfg)
    else:
        import cloakbrowser

        m.setattr(cloakbrowser, "launch", _launch)


def _call(fn_name, **kw):
    if fn_name == "open_persistent_context":
        return cloak.open_persistent_context(
            user_data_dir="/nonexistent/m108", headless=False, **kw
        )
    return cloak.launch_browser(headless=False, **kw)


BACKENDS = [cloak.CLOAKBROWSER, cloak.PLAYWRIGHT]
FUNCS = ["open_persistent_context", "launch_browser"]


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("fn_name", FUNCS)
def test_default_off_launch_unchanged(monkeypatch, backend, fn_name):
    with monkeypatch.context() as m:
        m.setattr(cloak.os, "geteuid", lambda: 1000)
        omitted, explicit = [], []
        _install_fakes(m, backend, omitted)
        _call(fn_name, args=["--no-sandbox", "--foo"])
        _install_fakes(m, backend, explicit)
        _call(fn_name, args=["--no-sandbox", "--foo"], sandbox=False)
    assert len(omitted) == 1 and omitted == explicit, (omitted, explicit)
    kw = omitted[0][1]
    assert "--no-sandbox" in kw["args"]
    assert (
        "chromium_sandbox" not in kw
        and "stealth_args" not in kw
        and "sandbox" not in kw
    )


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("fn_name", FUNCS)
def test_sandbox_on_strips_no_sandbox_and_enables(monkeypatch, backend, fn_name):
    calls = []
    with monkeypatch.context() as m:
        m.setattr(cloak.os, "geteuid", lambda: 1000)
        _install_fakes(m, backend, calls)
        _call(fn_name, args=["--no-sandbox", "--foo"], sandbox=True)
    assert len(calls) == 1, calls
    where, kw = calls[0]
    assert where.startswith("cloak." if backend == cloak.CLOAKBROWSER else "pw."), where
    assert "--no-sandbox" not in kw["args"], (
        f"M108: {where} still got --no-sandbox: {kw['args']}"
    )
    assert "--foo" in kw["args"]
    assert kw.get("chromium_sandbox") is True, (
        f"M108: {where} missing chromium_sandbox=True"
    )
    assert "sandbox" not in kw
    if backend == cloak.CLOAKBROWSER:
        assert kw.get("stealth_args") is False, (
            "cloak would re-add its own --no-sandbox"
        )
        assert "--fingerprint=12345" in kw["args"], (
            "stealth defaults must still be supplied"
        )


@pytest.mark.parametrize("fn_name", FUNCS)
def test_sandbox_on_real_cloakbrowser_build_args_has_no_no_sandbox(
    monkeypatch, fn_name
):
    browser_mod = pytest.importorskip("cloakbrowser.browser")
    calls = []
    with monkeypatch.context() as m:
        m.setattr(cloak.os, "geteuid", lambda: 1000)
        _install_fakes(m, cloak.CLOAKBROWSER, calls, fake_cloak_config=False)
        _call(fn_name, sandbox=True)
    kw = calls[0][1]
    built = browser_mod.build_args(kw["stealth_args"], kw["args"], headless=False)
    assert "--no-sandbox" not in built, built
    assert any(a.startswith("--fingerprint=") for a in built)
    # positive control: cloakbrowser's own defaults DO carry --no-sandbox
    assert "--no-sandbox" in browser_mod.build_args(True, [], headless=False)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize("fn_name", FUNCS)
def test_sandbox_on_as_root_falls_back_with_one_log_line(
    monkeypatch, capsys, backend, fn_name
):
    calls, base = [], []
    with monkeypatch.context() as m:
        m.setattr(cloak.os, "geteuid", lambda: 0)
        _install_fakes(m, backend, base)
        _call(fn_name, args=["--foo"])
        capsys.readouterr()
        _install_fakes(m, backend, calls)
        _call(fn_name, args=["--foo"], sandbox=True)
    err = capsys.readouterr().err
    assert calls == base, "root fallback must be the unsandboxed launch, unchanged"
    assert (
        "--no-sandbox" in calls[0][1]["args"] and "chromium_sandbox" not in calls[0][1]
    )
    lines = [ln for ln in err.splitlines() if "running as root" in ln]
    assert len(lines) == 1 and fn_name in lines[0], err


def test_manual_launch_kwargs_default_off_and_strict_opt_in():
    off = manual._manual_launch_kwargs({}, headless=False)
    assert "--no-sandbox" in off["args"] and "sandbox" not in off
    for v in ("true", 1, "yes", None, False):
        k = manual._manual_launch_kwargs({"manual_login_chromium_sandbox": v})
        assert k == off, f"only a literal True opts in, got {v!r}"
    on = manual._manual_launch_kwargs({"manual_login_chromium_sandbox": True})
    assert on["sandbox"] is True
    assert "--no-sandbox" not in on["args"]
    assert [a for a in off["args"] if a != "--no-sandbox"] == on["args"]


def _drive_manual_launch(monkeypatch, config):
    """Run ManualLoginSession._launch with every launch raising, so the whole chain
    runs: persistent(channel) -> persistent(bundled) -> non-persistent(channel) ->
    non-persistent(bundled). Returns the recorded (function, kwargs) calls."""
    calls = []

    def _opc(**kw):
        calls.append(("open_persistent_context", kw))
        raise RuntimeError("m108 fake launch")

    def _lb(**kw):
        calls.append(("launch_browser", kw))
        raise RuntimeError("m108 fake launch")

    sess = object.__new__(manual.ManualLoginSession)
    sess._config = config
    sess._banner_js = ""
    sess._manual_profile_dir = "/nonexistent/m108-profile"
    sess._headless = False
    sess._owning_site = "m108"
    sess._launched = (None, None, None)
    with monkeypatch.context() as m:
        m.setattr(cloak, "open_persistent_context", _opc)
        m.setattr(cloak, "launch_browser", _lb)
        m.setattr(cloak, "log_choice", lambda *a, **k: None)
        m.setattr(cloak, "note_channel_fallback", lambda **k: None)
        with pytest.raises(RuntimeError, match="m108 fake launch"):
            sess._launch()
    assert [c[0] for c in calls] == ["open_persistent_context"] * 2 + [
        "launch_browser"
    ] * 2, calls
    assert [c[1].get("channel") for c in calls] == ["chrome", None, "chrome", None]
    return calls


def test_manual_all_launch_paths_default_off(monkeypatch):
    for fn, kw in _drive_manual_launch(
        monkeypatch, {"name": "m108", "login_url": "http://127.0.0.1/"}
    ):
        assert "sandbox" not in kw, fn
        assert "--no-sandbox" in kw["args"], fn


def test_manual_all_launch_paths_setting_on(monkeypatch):
    cfg = {
        "name": "m108",
        "login_url": "http://127.0.0.1/",
        "manual_login_chromium_sandbox": True,
    }
    for fn, kw in _drive_manual_launch(monkeypatch, cfg):
        assert kw.get("sandbox") is True, (
            f"M108: manual {fn} (channel={kw.get('channel')}) not sandboxed"
        )
        assert "--no-sandbox" not in kw["args"], fn
