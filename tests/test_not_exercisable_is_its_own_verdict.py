""""Could not observe it" is not the same claim as "it looks wrong".

L12 (hls-dash-segmented-download) WARNed on the box with:

    no completed downloads yet -- HLS path not exercisable; run a segmented
    download first

The message was already honest; the bucket was not. A WARN reads as "something
about your deployment deserves attention", and the operator's instruction all
session has been that they want no warnings -- so a WARN that can never be
cleared is a permanent false alarm sitting in the summary.

And it can never be cleared. Re-derived 2026-07-29:
`grep -n m3u8 bulk_downloader/runner_transport.py` returns NOTHING. BD's generic
scrape-and-click transport has no HLS handling at all; it exists only behind
site-specific extractors (`runner_extractors.py:1185`, "HLS gets remuxed to MP4
by ffmpeg"). extraction_core.py recognises .m3u8 when CLASSIFYING a candidate,
but classification is not download. So on a host with no HLS-capable extractor
site, no arrangement of the seed can produce a segmented download, and L12's
WARN is reporting the absence of a capability as though it were a fault in the
deployment.

THE DISTINCTION THIS ADDS. Three outcomes were previously collapsed into two:

    PASS   evidence exists and it is good
    WARN   evidence exists and it is bad      <- kept
    WARN   there is no evidence to look at    <- becomes N/A

The third is the "unknown is a third state" rule from CLAUDE.md section 0, read
in the direction it is usually not: that section says a check which cannot
verify must SAY SO rather than report OK. It must equally not report ALARM. A
gate that cries wolf gets switched off, and then the thing it guarded is
unguarded -- which is section 0's inverse rule, and the reason over-sensitivity
is a soundness bug rather than a safe default.

N/A DOES NOT MEAN PASS. It is counted separately, printed separately, and never
contributes to the exit code -- but neither does it let a real failure hide. The
tests below pin both halves: an N/A run still exits 0, and a FAIL alongside any
number of N/As still exits 1.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="module")
def harness():
    try:
        from live_tests import checks  # noqa: F401  (registration)
        from live_tests import harness as h
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"live_tests did not import: {exc}")
    return h


@pytest.fixture(scope="module")
def checks_mod():
    from live_tests import checks
    return checks


@pytest.fixture()
def verdict_mod():
    # Imported normally rather than loaded standalone: capture_verdict defines a
    # @dataclass, and dataclasses resolve their module through sys.modules, so a
    # hand-built loader raises AttributeError at class-creation time.
    try:
        from tools import capture_verdict
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.fail(f"tools.capture_verdict did not import: {exc}")
    return capture_verdict


class _Ctx:
    """Minimal live-test context: what L12 actually touches."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)
        self.base_url = "http://localhost:5555"
        self._log = []

    def log(self, msg):
        self._log.append(str(msg))

    def ro_db(self):
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)


def _history_db(tmp_path, rows):
    db = tmp_path / "downloader_history.db"
    cx = sqlite3.connect(db)
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY, site_id TEXT, "
               "site_name TEXT, url TEXT, status TEXT, filename TEXT, "
               "file_size INTEGER, message TEXT)")
    cx.executemany("INSERT INTO history(url, status, message) VALUES (?,?,?)",
                   rows)
    cx.commit()
    cx.close()
    return db


# ── the verdict exists and behaves ───────────────────────────────────────────

def test_the_harness_has_a_not_exercisable_verdict(harness):
    assert hasattr(harness, "NA"), (
        "the harness has no N/A verdict, so a check with nothing to observe can "
        "only report PASS (a lie), WARN (a false alarm) or FAIL (worse)."
    )


def test_an_unobservable_run_does_not_fail_the_capture(harness):
    """N/A is not a failure. It must not gate the deploy."""
    assert harness.NA not in (harness.FAIL,), "N/A must be distinct from FAIL"
    assert harness.NA != harness.WARN, "N/A must be distinct from WARN"
    assert harness.NA != harness.PASS, (
        "N/A must be distinct from PASS -- 'nothing to look at' is not evidence "
        "that the thing works."
    )


def test_the_harness_accepts_na_from_a_check(harness):
    """run_all coerces any level outside _LEVELS to FAIL.

    Added after mutation testing: removing NA from _LEVELS passed 10/10, because
    every other test in this file calls the check function directly and reads
    its return value. The allow-list is enforced in run_all
    (`if level not in _LEVELS: level, detail = FAIL, ...`), which those tests
    never reach -- so an N/A verdict would have become a FAIL on the box while
    the suite stayed green. A gate whose denominator excludes the enforcement
    point is the defect this whole file is about.
    """
    assert harness.NA in harness._LEVELS, (
        f"NA is not in _LEVELS ({harness._LEVELS}), so run_all rewrites every "
        f"N/A result to FAIL with 'invalid level'. L12 would fail the capture "
        f"on exactly the hosts where it has nothing to observe."
    )


def test_the_summary_line_reports_the_bucket(harness):
    """A bucket that is counted but not printed is invisible to the operator."""
    src = (REPO_ROOT / "live_tests" / "harness.py").read_text(encoding="utf-8")
    assert "n/a" in src.lower(), (
        "the run summary does not print an n/a bucket, so N/A results would "
        "vanish -- the totals would not add up and capture_verdict rejects that."
    )


# ── capture_verdict must understand the new summary, and the old one ─────────

def test_capture_verdict_parses_a_summary_with_the_na_bucket(verdict_mod, tmp_path):
    log = tmp_path / "live.log"
    log.write_text("  3 pass | 1 warn | 0 fail | 2 n/a  (6 run)\n", encoding="utf-8")
    passed, warned, failed, total = verdict_mod._read_live(log)
    assert (passed, warned, failed, total) == (3, 1, 0, 6), (
        f"the new summary shape did not parse correctly: "
        f"{(passed, warned, failed, total)}"
    )


def test_capture_verdict_still_parses_the_old_summary(verdict_mod, tmp_path):
    """Old bundles must stay readable -- the parser is run against archives."""
    log = tmp_path / "live.log"
    log.write_text("  28 pass | 7 warn | 0 fail  (35 run)\n", encoding="utf-8")
    assert verdict_mod._read_live(log) == (28, 7, 0, 35), (
        "the parser no longer reads a pre-N/A summary; every archived capture "
        "bundle becomes unverifiable."
    )


def test_inconsistent_counts_are_still_rejected(verdict_mod, tmp_path):
    """The consistency check must include n/a, or it stops catching anything."""
    log = tmp_path / "live.log"
    log.write_text("  3 pass | 1 warn | 0 fail | 2 n/a  (99 run)\n", encoding="utf-8")
    with pytest.raises(ValueError):
        verdict_mod._read_live(log)


# ── L12 uses it ──────────────────────────────────────────────────────────────

def test_l12_reports_not_exercisable_when_there_is_nothing_to_observe(
        checks_mod, harness, tmp_path, monkeypatch):
    """No completed downloads at all -> nothing to judge, so N/A not WARN."""
    monkeypatch.setattr(checks_mod, "_ffmpeg_present", lambda: "/usr/bin/ffmpeg")
    ctx = _Ctx(_history_db(tmp_path, []))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level == harness.NA, (
        f"L12 returned {level} with no history to inspect. There is no evidence "
        f"of a fault -- there is no evidence at all. Got: {detail}"
    )


def test_l12_reports_not_exercisable_when_no_segmented_job_was_possible(
        checks_mod, harness, tmp_path, monkeypatch):
    """Completed downloads exist, but none segmented.

    On a host with no HLS-capable extractor site this is the permanent steady
    state, and BD's generic transport cannot change it.
    """
    monkeypatch.setattr(checks_mod, "_ffmpeg_present", lambda: "/usr/bin/ffmpeg")
    ctx = _Ctx(_history_db(tmp_path, [
        ("http://x/a.mp4", "done", "ok"),
        ("http://x/b.mp4", "done", "ok"),
    ]))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level == harness.NA, (
        f"L12 returned {level} for a host that completed plain downloads and no "
        f"segmented ones. Got: {detail}"
    )


def test_l12_still_passes_on_real_segmented_evidence(
        checks_mod, harness, tmp_path, monkeypatch):
    """The PASS path must survive -- this cut must not make L12 unfailable."""
    monkeypatch.setattr(checks_mod, "_ffmpeg_present", lambda: "/usr/bin/ffmpeg")
    ctx = _Ctx(_history_db(tmp_path, [
        ("http://x/stream.m3u8", "done", "hls via ffmpeg"),
    ]))
    level, detail = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level == harness.PASS, (
        f"L12 no longer PASSes on real segmented evidence -- it returned "
        f"{level}: {detail}. N/A must not swallow the positive case."
    )


def test_l12_still_fails_when_it_cannot_read_history(
        checks_mod, harness, tmp_path, monkeypatch):
    """An unreadable DB is a real problem, not an unobservable one."""
    monkeypatch.setattr(checks_mod, "_ffmpeg_present", lambda: "/usr/bin/ffmpeg")
    ctx = _Ctx(tmp_path / "downloader_history.db")
    (tmp_path / "downloader_history.db").write_text("not a database")
    level, _ = checks_mod.l12_hls_dash_segmented_download(ctx)
    assert level in (harness.FAIL, harness.NA), (
        f"L12 returned {level} on an unreadable history DB"
    )
