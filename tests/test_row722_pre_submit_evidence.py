"""Row 722 (operator, 2026-09-15 08:3xZ): "a login attempt only counts if
you typed in credentials ... on the 2nd attempt review the screenshot before
pressing enter."  That review needs an object: the filled form as it stood
BEFORE the submit sweep.  ``do_login`` now keeps ``login-pre-submit-*.png``
right before ``_submit_login`` runs -- a PNG only (the password renders
masked; no HTML pass, so the row 708 evidence-pass counts are unchanged).

Fakes only; no browser, no live site.
"""
from __future__ import annotations

import inspect
import types

from bulk_downloader.login_impl import replay, submit

BD_GATE_SCOPE = "module"


class _ShotPage:
    def __init__(self):
        self.shots = []
        self.order = []

    def evaluate(self, js, arg=None):
        self.order.append(("mask", arg))

    def screenshot(self, path):
        self.shots.append(path)
        self.order.append(("shot", path))
        with open(path, "wb") as fh:
            fh.write(b"\x89PNG fixture")


class _NoShotPage:
    def screenshot(self, path):
        raise RuntimeError("fixture: page closed")


def _config(tmp_path):
    return {"cookie_file": str(tmp_path / "jar.json"), "name": "fixture"}


def test_the_filled_form_is_kept_as_a_png_before_the_submit(tmp_path, monkeypatch):
    """THE ROW: a pre-submit PNG lands in the login evidence directory."""
    monkeypatch.setattr(replay, "_login_evidence_dir",
                        lambda config: tmp_path / "evidence")
    page = _ShotPage()
    path = replay.keep_pre_submit_screenshot(page, _config(tmp_path))
    assert path, "no pre-submit screenshot: the review-before-enter rule has nothing to review"
    assert path.endswith(".png") and "login-pre-submit-" in path, path
    assert page.shots == [path]
    assert (tmp_path / "evidence").is_dir()
    assert [p.name for p in (tmp_path / "evidence").iterdir()] == [path.rsplit("/", 1)[1]]


def test_negative_control_an_uncapturable_page_reports_it_and_keeps_nothing(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(replay, "_login_evidence_dir",
                        lambda config: tmp_path / "evidence")
    path = replay.keep_pre_submit_screenshot(_NoShotPage(), _config(tmp_path))
    assert path is None
    assert "pre-submit screenshot unavailable: fixture: page closed" in capsys.readouterr().err
    assert list((tmp_path / "evidence").iterdir()) == []


def test_the_capture_runs_inside_do_login_before_the_submit_sweep():
    """Order pins the meaning: kept BEFORE ``_submit_login`` can press enter."""
    src = inspect.getsource(submit.do_login)
    keep = src.find("keep_pre_submit_screenshot(page, config)")
    sweep = src.find("ok,method=_submit_login(page,sb_candidates,pf_candidates)")
    assert keep != -1, "do_login no longer keeps a pre-submit screenshot"
    assert sweep != -1, "the submit sweep call moved; re-anchor this test"
    assert keep < sweep, "the pre-submit screenshot is taken AFTER the submit"
    assert "pre-submit screenshot kept at" in src


def test_the_username_is_masked_for_the_shot_and_restored_after(tmp_path, monkeypatch):
    """The typed username is a credential: discs during the shot, restored after."""
    monkeypatch.setattr(replay, "_login_evidence_dir",
                        lambda config: tmp_path / "evidence")
    page = _ShotPage()
    path = replay.keep_pre_submit_screenshot(page, _config(tmp_path))
    assert path
    assert [o[0] for o in page.order] == ["mask", "shot", "mask"], page.order
    assert page.order[0][1] is True and page.order[-1][1] is False
    assert "webkitTextSecurity" in replay._MASK_TEXT_INPUTS_JS
    assert "not([type=password])" in replay._MASK_TEXT_INPUTS_JS

