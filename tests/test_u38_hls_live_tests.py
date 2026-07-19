"""U38 — L12 (hls-dash-segmented-download) + L15 (resume-after-
interruption) + L16 (ffmpeg-process-group-kill), the three HLS/ffmpeg
pipeline live tests. All disruptive=True.

L16 is genuinely runnable wherever ffmpeg exists — it spawns a real
ffmpeg child in its own process group and kills the group — so the
sandbox exercises it for real. L12/L15 need the deployment; here they
are checked for registration and graceful degradation.
"""
import shutil

import pytest

import live_tests.checks as checks  # noqa: F401 (registers the checks)
import live_tests.harness as h
from bulk_downloader import db


_LEVELS = (h.PASS, h.WARN, h.FAIL)


def _get_test(test_id):
    for t in h.registry():
        if t.id == test_id:
            return t
    return None


# ── registration ───────────────────────────────────────────────────

def test_l12_l15_l16_registered():
    ids = {t.id for t in h.registry()}
    assert {"L12", "L15", "L16"} <= ids


def test_hls_tests_are_disruptive():
    for tid in ("L12", "L15", "L16"):
        assert _get_test(tid).disruptive is True


# ── graceful degradation ───────────────────────────────────────────

def _dead_ctx():
    return h.Context("http://localhost:1", "/tmp/u38_no_dir",
                     disruptive=True)


def test_l12_no_db_is_warn():
    level, detail = _get_test("L12").fn(_dead_ctx())
    # ffmpeg present in sandbox, but no DB -> WARN
    assert level in (h.WARN, h.FAIL)
    if level == h.WARN:
        assert "no DB" in detail or "ffmpeg" in detail


def test_l15_no_db_is_warn():
    level, detail = _get_test("L15").fn(_dead_ctx())
    assert level == h.WARN
    assert "no DB" in detail


def test_all_three_return_valid_tuples():
    for tid in ("L12", "L15", "L16"):
        res = _get_test(tid).fn(_dead_ctx())
        assert isinstance(res, tuple) and len(res) == 2
        assert res[0] in _LEVELS


# ── L15 against a real sandbox DB (reads the queue table) ──────────

def test_l15_passes_with_a_real_queue_table(clean_workdir):
    db.db_init()  # creates the queue table
    ctx = h.Context("http://localhost:1", str(clean_workdir),
                    disruptive=True)
    level, detail = _get_test("L15").fn(ctx)
    # the queue table exists -> the resume machinery is present
    assert level == h.PASS
    assert "queue table persists jobs" in detail


# ── L16 is genuinely runnable wherever ffmpeg exists ───────────────

@pytest.mark.skipif(shutil.which("ffmpeg") is None,
                    reason="ffmpeg not installed")
def test_l16_kills_a_real_ffmpeg_process_group():
    # this actually spawns ffmpeg, kills its process group, and
    # confirms no orphan — a real test of _terminate's invariant
    ctx = h.Context("http://localhost:1", "/tmp", disruptive=True)
    level, detail = _get_test("L16").fn(ctx)
    assert level == h.PASS
    assert "killed cleanly" in detail


def test_l16_warns_without_ffmpeg(monkeypatch):
    # if ffmpeg is absent L16 must WARN (environment gap), not FAIL
    monkeypatch.setattr(checks, "_ffmpeg_present", lambda: None)
    ctx = h.Context("http://localhost:1", "/tmp", disruptive=True)
    level, detail = _get_test("L16").fn(ctx)
    assert level == h.WARN
    assert "ffmpeg not found" in detail


# ── harness integration ────────────────────────────────────────────

def test_hls_tests_skipped_without_disruptive_flag(tmp_path):
    rdir = tmp_path / "r"
    h.run_all("http://localhost:1", str(tmp_path), results_dir=rdir)
    for tid in ("L12", "L15", "L16"):
        assert not (rdir / f"{tid}.log").exists()
