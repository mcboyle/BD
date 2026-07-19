"""v3.66.156 — selector_lint stops flagging video-player controls as chrome.

The nav-words rule matches "settings", "menu-bar", etc. to catch selectors that
target account/nav chrome instead of a download control. But a video player's
own quality menu — e.g. ``[aria-label="Open the video quality settings menu"]``
— tripped it on "settings", producing a false-positive warning on every
reviewed template that carries a quality opener (seen in the v155 round-trip:
the candidate warned that the quality menu "targets nav/account/search
chrome"). The fix adds a playback-context guard: a nav word is ignored when the
selector clearly targets a player control (quality/resolution/playback/etc.).
Real account/nav chrome and the generic-root checks are unchanged.

No browser, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader.selector_lint import lint_selector, lint_template  # noqa: E402


def _codes(selector: str, role: str = "trigger"):
    return [i.code for i in lint_selector(selector, role=role)]


# ── player controls must NOT be flagged as nav/account chrome ─────────────
def test_quality_settings_menu_not_flagged() -> None:
    assert "nav_selector" not in _codes('[aria-label="Open the video quality settings menu"]')


def test_set_quality_option_not_flagged() -> None:
    assert "nav_selector" not in _codes('[aria-label="Set video quality to 1080p"]')


def test_quality_menu_class_not_flagged() -> None:
    assert "nav_selector" not in _codes(".quality-menu button")


def test_playback_settings_not_flagged() -> None:
    # "playback settings" is a player control, not account settings
    assert "nav_selector" not in _codes('[aria-label="Playback settings"]')


# ── genuine account/nav chrome is still flagged ───────────────────────────
def test_account_settings_still_flagged() -> None:
    assert "nav_selector" in _codes('[aria-label="Account settings"]')


def test_navbar_still_flagged() -> None:
    # role=row so a nav anchor is an error-level nav/generic hit
    codes = _codes(".navbar a", role="row")
    assert "nav_selector" in codes or "generic_row_selector" in codes


def test_search_still_flagged() -> None:
    assert "nav_selector" in _codes('[aria-label="Search"]')


def test_preferences_still_flagged() -> None:
    assert "nav_selector" in _codes('[aria-label="Open preferences"]')


# ── scoped selectors stay safe (pre-existing behaviour) ───────────────────
def test_scoped_settings_safe() -> None:
    assert "nav_selector" not in _codes('.modal [aria-label="settings"]')


# ── template-level path: quality opener no longer warns ───────────────────
def test_lint_template_quality_menu_clean() -> None:
    tpl = {
        "selectors": {
            "download": {"trigger": '[aria-label*="Download" i]'},
            "quality": {
                "open_menu": '[aria-label="Open the video quality settings menu"]',
                "resolution_option": '[aria-label="Set video quality to {resolution}"]',
            },
        }
    }
    msgs = [i.message for i in lint_template(tpl)]
    assert not any("chrome" in m for m in msgs), msgs


def test_lint_template_account_settings_in_quality_still_warns() -> None:
    # a quality slot mistakenly pointed at account chrome should still warn
    tpl = {"selectors": {"quality": {"open_menu": '[aria-label="Account settings"]'}}}
    assert any(i.code == "nav_selector" for i in lint_template(tpl))
