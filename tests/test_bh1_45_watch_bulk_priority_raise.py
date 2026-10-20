"""BH1-45: a bulk_priority raise in the watch-folder import must not be
reported as a failed load, and the file must stay retryable.

dqb F3 made SiteRunner.bulk_priority re-raise a failed queue write (after
rolling the priority change back). process_file runs load_urls and
bulk_priority in one try, so that raise landed in the load_urls handler: the
file was MOVED to .failed/ (which scan_once never revisits) and the reason
said "load_urls failed", with urls_imported=0 -- yet load_urls had succeeded
and the URLs were queued at normal priority.

Chosen behaviour: on a bulk_priority raise the URLs stay queued, the file is
LEFT IN PLACE so the next poll retries (load_urls dedups, bulk_priority runs
again), ok=False, urls_imported is the real count, and the reason names
bulk_priority. A load_urls raise still goes to .failed/ (unchanged).
"""
import sqlite3
import tempfile
import threading
from pathlib import Path

from bulk_downloader.watch_folder import process_file, watch_loop_for_site

BD_GATE_SCOPE = "module"

_URL = "https://example.invalid/bh145/a.mp4"


class _FakeRunner:
    """The SiteRunner surface process_file uses; load_urls has the real
    signature, bulk_priority raises like the F3 re-raise when told to."""

    def __init__(self, raise_on_priority=False, raise_on_load=False, config=None):
        self.raise_on_priority = raise_on_priority
        self.raise_on_load = raise_on_load
        self.config = config or {}
        self.loaded = []
        self.priority_calls = []
        self.events = []

    def load_urls(self, urls, dedupe=True, folder_scan=False):
        if self.raise_on_load:
            raise RuntimeError("bh145 simulated load failure")
        self.loaded.append(list(urls))

    def bulk_priority(self, urls, priority):
        self.priority_calls.append((list(urls), priority))
        if self.raise_on_priority:
            raise sqlite3.OperationalError("database is locked (bh145 injected)")
        return len(urls)

    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))


def _drop(td):
    p = Path(td) / "urls.txt"
    p.write_text(_URL + "\n", encoding="utf-8")
    return p


def test_bulk_priority_raise_keeps_file_and_reports_real_reason():
    runner = _FakeRunner(raise_on_priority=True)
    with tempfile.TemporaryDirectory() as td:
        p = _drop(td)
        result = process_file(p, runner, priority="high")
        assert p.exists(), f"BH1_45_FILE_MOVED: {result!r}"
        assert not (Path(td) / ".failed").exists(), f"BH1_45_FAILED_DIR: {result!r}"
        assert result["moved_to"] == str(p), f"BH1_45_MOVED_TO: {result!r}"
        assert result["ok"] is False
        assert result["urls_imported"] == 1, f"BH1_45_IMPORT_COUNT: {result!r}"
        err = result["errors"][0]
        assert err.startswith("bulk_priority failed"), f"BH1_45_REASON: {err!r}"
        assert "OperationalError" in err and "bh145 injected" in err, err
        assert "load_urls failed" not in err, f"BH1_45_MISREPORT: {err!r}"


def test_bulk_priority_raise_then_next_pass_imports_and_applies_priority():
    runner = _FakeRunner(raise_on_priority=True)
    with tempfile.TemporaryDirectory() as td:
        p = _drop(td)
        process_file(p, runner, priority="high")
        runner.raise_on_priority = False
        result = process_file(p, runner, priority="high")
        assert result["ok"] is True, f"BH1_45_RETRY_NOT_OK: {result!r}"
        assert not p.exists()
        assert len(list((Path(td) / ".processed").iterdir())) == 1
        assert runner.priority_calls[-1] == ([_URL], "high")


def test_watch_loop_event_does_not_claim_a_move_for_a_kept_file():
    stop = threading.Event()
    with tempfile.TemporaryDirectory() as td:
        p = _drop(td)
        runner = _FakeRunner(raise_on_priority=True, config={
            "watch_enabled": True, "watch_folder": td,
            "watch_url_priority": "high"})

        def _poll(folder):
            return [p]

        def _sleep_stop(_s):
            stop.set()
            return True
        stop.wait = _sleep_stop
        watch_loop_for_site(runner, stop, poll_fn=_poll)
        msgs = [m for k, m in runner.events if k == "watch_import"]
        assert len(msgs) == 1, msgs
        assert "moved to" not in msgs[0], f"BH1_45_EVENT_FALSE_MOVE: {msgs[0]!r}"
        assert "bulk_priority failed" in msgs[0], msgs[0]
        assert "left in place" in msgs[0], msgs[0]


def test_control_priority_path_unchanged():
    runner = _FakeRunner()
    with tempfile.TemporaryDirectory() as td:
        p = _drop(td)
        result = process_file(p, runner, priority="high")
        assert result["ok"] is True and result["urls_imported"] == 1
        assert not p.exists()
        assert (Path(td) / ".processed").exists()
        assert runner.priority_calls == [([_URL], "high")]


def test_control_load_urls_raise_still_goes_to_failed():
    runner = _FakeRunner(raise_on_load=True)
    with tempfile.TemporaryDirectory() as td:
        p = _drop(td)
        result = process_file(p, runner, priority="high")
        assert result["ok"] is False and result["urls_imported"] == 0
        assert not p.exists()
        assert (Path(td) / ".failed").exists()
        assert result["errors"][0].startswith("load_urls failed")
        assert runner.priority_calls == []
