"""Row 722 live (kink.com, 2026-09-15 05:24Z): the login fell to manual
takeover with "Couldn't submit form: no submit method produced navigation"
and NOTHING of the page was kept -- no HTML, no screenshot.  Diagnosing why
(a hidden duplicate form, G7) took a separate live stepper session.

The fix: the hand-off keeps the page it gave up on, through the SAME
``write_login_evidence`` writer the settled verdicts use, and that writer
now keeps a PNG beside the HTML (best effort: a page that cannot be
screenshotted still yields the HTML).  No live site, no login started.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from test_row708_no_nav_login_is_not_success import _drive, _jar

BD_GATE_SCOPE = "module"


def _evidence(tmp_path):
    return sorted((tmp_path / "evidence").glob("*")) if (tmp_path / "evidence").is_dir() else []


def test_handoff_keeps_the_page_it_gave_up_on(monkeypatch, tmp_path):
    """THE ROW: an unconvincing submit that hands off leaves HTML evidence."""
    before = _jar(["pref_a"])
    result, calls = _drive(monkeypatch, tmp_path, branch="ajax",
                           before=before, after=before, allow_manual=True)
    assert result[0] == "MANUAL_PENDING", repr(result[0])
    kept = _evidence(tmp_path)
    assert [p.suffix for p in kept] == [".html"], (
        "the hand-off kept nothing of the page it gave up on: %r" % kept)
    assert kept[0].name.startswith("manual-takeover"), kept[0].name
    body = kept[0].read_text(encoding="utf-8")
    assert "fixture members page" in body, body[:200]
    assert "https://login.example.invalid/login" in body, body[:200]
    assert calls["content"] == 1, calls


def test_negative_control_a_plain_failure_without_takeover_keeps_no_handoff_evidence(
        monkeypatch, tmp_path):
    """Without manual takeover there is no hand-off, hence no hand-off file."""
    before = _jar(["pref_a"])
    result, _ = _drive(monkeypatch, tmp_path, branch="ajax",
                       before=before, after=before, allow_manual=False)
    assert result[0] is False, repr(result[0])
    assert [p for p in _evidence(tmp_path) if "manual-takeover" in p.name] == []


class _ShotPage:
    def __init__(self, shots):
        self.shots = shots

    def content(self):
        return "<html><body>shot fixture</body></html>"

    def screenshot(self, path):
        self.shots.append(path)
        Path(path).write_bytes(b"\x89PNG fixture")


class _NoShotPage(_ShotPage):
    def screenshot(self, path):
        raise RuntimeError("fixture: page closed")


def test_evidence_writer_keeps_a_png_beside_the_html(tmp_path):
    from bulk_downloader.login_impl.replay import write_login_evidence
    shots = []
    html = write_login_evidence(_ShotPage(shots), {"login_evidence_dir": str(tmp_path)},
                                "https://x.invalid/", "manual takeover x")
    assert html and html.endswith(".html")
    png = Path(html).with_suffix(".png")
    assert shots == [str(png)], shots
    assert png.read_bytes().startswith(b"\x89PNG")


def test_evidence_writer_screenshot_failure_still_keeps_the_html(tmp_path, capsys):
    """NEGATIVE CONTROL: a page that cannot be shot yields the HTML alone,
    with a distinctive diagnostic, never a missing verdict file."""
    from bulk_downloader.login_impl.replay import write_login_evidence
    html = write_login_evidence(_NoShotPage([]), {"login_evidence_dir": str(tmp_path)},
                                "https://x.invalid/", "manual takeover y")
    assert html and Path(html).is_file()
    assert not Path(html).with_suffix(".png").exists()
    assert "evidence screenshot unavailable: fixture: page closed" in capsys.readouterr().err
