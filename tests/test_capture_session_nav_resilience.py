"""D2(a) — capture_session navigation timeout resilience.

Covers the Capture Navigation Blocker fix: page.goto(start_url) keeps its
default wait_until="load", but a Playwright TimeoutError is tolerated *only*
when the DOM is already usable (interactive capture; recorders attached). An
unusable page, or any non-timeout exception, must still surface.

These are pure unit tests on the `_goto_or_continue_if_usable` helper plus the
CLI surface — no real browser.
"""
import contextlib
import os

import pytest

from playwright.sync_api import TimeoutError as PWTimeoutError
from tools.capture_session import (
    _goto_or_continue_if_usable, _build_parser, _resolve_capture_wait_until,
    _normalize_pick)


class FakePage:
    """Minimal stand-in for a Playwright Page."""
    def __init__(self, goto_exc=None, readystate="complete", body_len=100,
                 title="Title", eval_raises=False):
        self._goto_exc = goto_exc
        self._rs = readystate
        self._body_len = body_len
        self._title = title
        self._eval_raises = eval_raises
        self.goto_called_with = None

    def goto(self, url):
        self.goto_called_with = url
        if self._goto_exc is not None:
            raise self._goto_exc
        return None

    def evaluate(self, expr):
        if self._eval_raises:
            raise RuntimeError("evaluate failed")
        if "readyState" in expr:
            return self._rs
        if "innerText" in expr:
            return self._body_len
        if "title" in expr:
            return self._title
        return None


# 1. Normal success — unchanged behaviour, no warning.
def test_normal_success_unchanged(capsys):
    page = FakePage(goto_exc=None)
    assert _goto_or_continue_if_usable(page, "https://example.com/") is True
    assert page.goto_called_with == "https://example.com/"
    assert "WARNING" not in capsys.readouterr().err


# 2. TimeoutError + usable DOM (complete) — continues, warning emitted.
def test_timeout_usable_complete_continues(capsys):
    page = FakePage(goto_exc=PWTimeoutError("Page.goto: Timeout 30000ms exceeded"),
                    readystate="complete", body_len=415)
    assert _goto_or_continue_if_usable(page, "https://app.reptyle.com/") is False
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "did not reach 'load'" in err
    assert "CONTROLLED continue" in err
    assert "NOT a normal page load" in err


# 2b. TimeoutError + usable DOM (interactive, empty body) — body exists → usable.
def test_timeout_usable_interactive_empty_body_continues(capsys):
    page = FakePage(goto_exc=PWTimeoutError("Timeout"),
                    readystate="interactive", body_len=0)
    assert _goto_or_continue_if_usable(page, "u") is False
    assert "WARNING" in capsys.readouterr().err


# 3. TimeoutError + unusable DOM (still loading) — re-raise TimeoutError.
def test_timeout_unusable_loading_reraises():
    page = FakePage(goto_exc=PWTimeoutError("Timeout"),
                    readystate="loading", body_len=-1)
    with pytest.raises(PWTimeoutError):
        _goto_or_continue_if_usable(page, "u")


# 3b. TimeoutError + readyState complete but NO body (-1) — re-raise.
def test_timeout_no_body_reraises():
    page = FakePage(goto_exc=PWTimeoutError("Timeout"),
                    readystate="complete", body_len=-1)
    with pytest.raises(PWTimeoutError):
        _goto_or_continue_if_usable(page, "u")


# 3c. TimeoutError + usability probe itself fails — re-raise (not swallowed).
def test_timeout_probe_failure_reraises():
    page = FakePage(goto_exc=PWTimeoutError("Timeout"), eval_raises=True)
    with pytest.raises(PWTimeoutError):
        _goto_or_continue_if_usable(page, "u")


# 4. Non-timeout exception — never swallowed; propagates unchanged.
def test_non_timeout_exception_propagates():
    page = FakePage(goto_exc=ValueError("boom"))
    with pytest.raises(ValueError):
        _goto_or_continue_if_usable(page, "u")


def test_non_timeout_runtimeerror_propagates():
    page = FakePage(goto_exc=RuntimeError("nav crashed"))
    with pytest.raises(RuntimeError):
        _goto_or_continue_if_usable(page, "u")


# 5/7. No --wait-until option was added (D2(b) NOT implemented).
def test_no_wait_until_option():
    parser = _build_parser()
    opt_strings = [s for a in parser._actions for s in a.option_strings]
    assert "--wait-until" not in opt_strings
    with pytest.raises(SystemExit):
        parser.parse_args(["--url", "u", "--out", "o",
                           "--wait-until", "domcontentloaded"])


# 6. CLI surface unchanged — the known options are all still present and intact.
def test_cli_surface_unchanged():
    parser = _build_parser()
    opt_strings = {s for a in parser._actions for s in a.option_strings}
    for expected in ("--url", "--out", "--system-chrome", "--body-cap-mib",
                     "--chunk-events", "--profile-dir", "--autofill", "--title",
                     "--url-memory-file", "--max-seconds", "--finish-file",
                     "--no-hud"):
        assert expected in opt_strings, f"missing CLI option {expected}"
    # exactly 12 user-facing add_argument options (+ implicit -h/--help)
    # (11 -> 12 at v3.66.230: --no-hud added for the per-capture HUD toggle)
    user_opts = {s for s in opt_strings if s.startswith("--")} - {"--help"}
    assert len(user_opts) == 12


# ─── BD_CAPTURE_WAIT_UNTIL opt-in toggle (default-OFF) ────────────────
#
# The interactive-capture navigation wait condition is overridable, opt-in, via
# the BD_CAPTURE_WAIT_UNTIL env var. Default (unset) must be byte-identical to
# the pre-toggle path: a bare page.goto(start_url) with NO wait_until kwarg. An
# operator opts in (per capture, justified by tools/nav_probe.py evidence) to a
# weaker condition; the timeout-tolerant branch still applies either way.

@contextlib.contextmanager
def _wait_until_env(value):
    """Set/clear BD_CAPTURE_WAIT_UNTIL and restore it (monkeypatch is unreliable
    in the run_tests.py harness — restore the global explicitly)."""
    prev = os.environ.get("BD_CAPTURE_WAIT_UNTIL")
    if value is None:
        os.environ.pop("BD_CAPTURE_WAIT_UNTIL", None)
    else:
        os.environ["BD_CAPTURE_WAIT_UNTIL"] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("BD_CAPTURE_WAIT_UNTIL", None)
        else:
            os.environ["BD_CAPTURE_WAIT_UNTIL"] = prev


class KwargFakePage:
    """Like FakePage but its goto accepts (and records) kwargs, so the opt-in
    path that passes wait_until= can be asserted. The plain FakePage's
    goto(self, url) intentionally rejects kwargs — proving the default path
    never passes one."""
    def __init__(self, goto_exc=None, readystate="complete", body_len=100,
                 title="Title"):
        self._goto_exc = goto_exc
        self._rs = readystate
        self._body_len = body_len
        self._title = title
        self.goto_url = None
        self.goto_kwargs = None

    def goto(self, url, **kwargs):
        self.goto_url = url
        self.goto_kwargs = kwargs
        if self._goto_exc is not None:
            raise self._goto_exc
        return None

    def evaluate(self, expr):
        if "readyState" in expr:
            return self._rs
        if "innerText" in expr:
            return self._body_len
        if "title" in expr:
            return self._title
        return None


# 7. Default (env unset): bare goto, NO wait_until kwarg — byte-identical path.
def test_wait_until_unset_is_bare_goto():
    with _wait_until_env(None):
        page = KwargFakePage()
        assert _goto_or_continue_if_usable(page, "https://x/") is True
        assert page.goto_url == "https://x/"
        assert page.goto_kwargs == {}


# 8. Opt-in domcontentloaded: goto receives wait_until="domcontentloaded".
def test_wait_until_dcl_opt_in_passes_kwarg():
    with _wait_until_env("domcontentloaded"):
        page = KwargFakePage()
        assert _goto_or_continue_if_usable(page, "https://x/") is True
        assert page.goto_kwargs == {"wait_until": "domcontentloaded"}


# 9. Opt-in commit also threads through.
def test_wait_until_commit_opt_in_passes_kwarg():
    with _wait_until_env("commit"):
        page = KwargFakePage()
        assert _goto_or_continue_if_usable(page, "https://x/") is True
        assert page.goto_kwargs == {"wait_until": "commit"}


# 10. Unrecognised value is ignored → falls back to the bare default path.
def test_wait_until_invalid_falls_back_to_default():
    with _wait_until_env("networkidle"):   # not in the allowlist
        page = KwargFakePage()
        assert _goto_or_continue_if_usable(page, "https://x/") is True
        assert page.goto_kwargs == {}


# 11. Opt-in path STILL tolerates a load/DCL timeout when the DOM is usable.
def test_wait_until_opt_in_still_tolerates_timeout_usable(capsys):
    with _wait_until_env("domcontentloaded"):
        page = KwargFakePage(goto_exc=PWTimeoutError("Timeout 30000ms exceeded"),
                             readystate="complete", body_len=200)
        assert _goto_or_continue_if_usable(page, "https://x/") is False
        assert page.goto_kwargs == {"wait_until": "domcontentloaded"}
        assert "WARNING" in capsys.readouterr().err


# 12. The resolver: allowlist accepted (incl. explicit "load"); else None.
def test_resolve_capture_wait_until_allowlist_and_normalization():
    for v in ("load", "domcontentloaded", "commit"):
        with _wait_until_env(v):
            assert _resolve_capture_wait_until() == v
    # case + surrounding whitespace are normalised
    with _wait_until_env("  DOMContentLoaded  "):
        assert _resolve_capture_wait_until() == "domcontentloaded"
    # blank / unknown / unset → None (the unchanged default)
    for bad in ("", "   ", "bogus", "networkidle"):
        with _wait_until_env(bad):
            assert _resolve_capture_wait_until() is None
    with _wait_until_env(None):
        assert _resolve_capture_wait_until() is None


# ---------------------------------------------------------------------------
# #2b — observational-picker pick TRANSPORT normalisation.
#
# The picker hands picks back via the Playwright binding when present, and
# buffers the SAME {d, ts} record in-page for the pump to drain when the binding
# has not survived a cross-origin document swap (the w212wow failure: login
# clicks on auth.* resolved, every post-navigation venus.* click was dropped).
# Both transports feed `_normalize_pick`; these pin its contract.
# ---------------------------------------------------------------------------

def test_normalize_pick_uses_in_page_ts_from_buffer_record():
    # A drained buffer record carries the in-page epoch-ms stamp — it MUST be
    # preserved (not re-stamped at drain time) or correlation mis-attributes.
    out = _normalize_pick({"d": {"tag": "button", "classes": ["vjs-big-play"]},
                           "ts": 1781396200000}, lambda: 999)
    assert out == {"descriptor": {"tag": "button", "classes": ["vjs-big-play"]},
                   "ts": 1781396200000}


def test_normalize_pick_float_ts_coerced_and_missing_ts_falls_back():
    assert _normalize_pick({"d": {"tag": "a"}, "ts": 123.0}, lambda: 999)["ts"] == 123
    assert _normalize_pick({"d": {"tag": "a"}}, lambda: 999)["ts"] == 999  # local clock


def test_normalize_pick_accepts_legacy_bare_descriptor():
    # Backward-compat: a pre-#2b bare descriptor (no "d" key) is stamped locally.
    out = _normalize_pick({"tag": "div", "id": "x"}, lambda: 555)
    assert out == {"descriptor": {"tag": "div", "id": "x"}, "ts": 555}


def test_normalize_pick_drops_malformed_and_nondict():
    for bad in (None, "x", 5, [], {"d": "not-a-dict"}, {"d": None}):
        assert _normalize_pick(bad, lambda: 1) is None


def test_pump_dom_wires_add_init_script_and_buffer_drain():
    """#2b positive control: the normalize-contract tests above pass even if the
    pump never drained the in-page buffer, so pin the WIRING directly. The capture
    run must (a) register the picker via add_init_script (re-installs at
    document-start on every navigation, cross-origin included) and (b) drain
    window.__bd_picks each tick into the SAME recorder as the binding. Guards
    against silent removal of the cross-origin transport."""
    import inspect
    import tools.capture_session as cs
    src = inspect.getsource(cs)
    assert "add_init_script(_picker_js)" in src, "picker not re-armed on navigation"
    assert "window.__bd_picks" in src, "in-page pick buffer never drained"
    # the drained records must feed the shared recorder (identical resolution/redaction)
    drain_region = src[src.index("window.__bd_picks"):]
    assert "_record_pick(" in drain_region[:600], "drained picks not fed to _record_pick"
