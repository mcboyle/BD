"""Row 450 -- the watch folder must not import a .txt that is still being written.

``scan_once`` returned every .txt the poll saw with no age or stability guard,
and ``process_file`` read it immediately and then MOVED it into ``.processed/``.
A scraper or rsync still appending when the poll fires therefore yields a partial
read: the complete leading lines import, and the writer's remaining bytes are
lost -- on the same filesystem the rename follows the inode, so the tail lands
INSIDE ``.processed/``, which ``scan_once`` skips forever.

The event log then reports ``Imported N URL(s)`` as success and the ``.processed``
audit copy contains URLs that were never queued, so the operator's own audit trail
affirms an import that silently dropped its tail.  That is the
wrong-artifact-recorded-as-done shape at the bulk-ingest seam.

WHY MTIME-AGE AND NOT SIZE-STABILITY.  A size-stability guard needs cross-scan
state, and that state is per-process: ``/api/sites/<sid>/watch/scan_now`` is a
SECOND caller living in a request thread with no memory of the loop's previous
poll, so a stateful guard would protect the loop and leave the endpoint naked.
Age is stateless, both callers get it for free, and it is strictly conservative --
a file whose mtime is younger than one poll interval MIGHT still be growing, so it
waits.  A writer that is genuinely finished costs at most one extra poll.

FAIL CLOSED.  A stat() that raises during the age check has not proven the file
quiescent, so the file is DEFERRED rather than imported -- CLAUDE.md A7: an
unavailable measurement is UNKNOWN, never OK.  Importing on an unreadable stat
would be the exact fail-open this row exists to remove.
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"


# ── fixtures ──────────────────────────────────────────────────────────


class _FakeRunner:
    """Records exactly what reached the queue, so 'imported' can be compared
    against the file's real final contents rather than against the report."""

    def __init__(self, cfg=None):
        self.config = cfg or {}
        self.loaded: list = []
        self.events: list = []

    def load_urls(self, urls, *a, **kw):
        self.loaded.extend(urls)
        return (len(urls), 0, 0)

    def bulk_priority(self, urls, priority):
        return len(urls)

    def log_event(self, kind, msg):
        self.events.append((kind, msg))


def _age(path: Path, seconds: float):
    """Backdate a file's mtime so an age guard considers it quiescent."""
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime - seconds))


# ── precondition: a mid-write file is really mid-write ────────────────


def test_the_fixture_really_holds_the_file_open_and_appends(tmp_path):
    """Before any verdict about imports, prove the fixture builds the shape:
    an open writer fd, a partial line on disk, and MORE bytes arriving after
    the first read."""
    p = tmp_path / "growing.txt"
    fh = open(p, "w", encoding="utf-8")
    try:
        fh.write("https://example.invalid/a\nhttps://example.invalid/b\n")
        fh.write("https://example.inval")          # torn mid-URL, no newline
        fh.flush()

        assert not fh.closed, "the writer fd must still be open"
        first_bytes = p.stat().st_size
        first_text = p.read_text(encoding="utf-8")
        assert first_text.endswith("https://example.inval"), (
            "the fixture did not leave a torn final line")
        assert first_text.count("\n") == 2, "expected exactly 2 complete lines"

        fh.write("id/c\nhttps://example.invalid/d\n")
        fh.flush()
        second_bytes = p.stat().st_size
    finally:
        fh.close()

    final_text = p.read_text(encoding="utf-8")
    assert second_bytes > first_bytes, (
        "the append did not grow the file: %d -> %d"
        % (first_bytes, second_bytes))
    assert final_text.count("\n") == 4, "expected exactly 4 complete lines"
    assert "https://example.invalid/c" in final_text, (
        "the torn line must complete into a DIFFERENT url than its prefix")


# ── row 450 core: the partial import ──────────────────────────────────


def test_a_file_still_being_written_is_not_imported(tmp_path):
    """RED on the defective parent: scan_once hands back a growing file,
    process_file imports its prefix, and the tail is lost forever."""
    p = tmp_path / "scraper_output.txt"
    fh = open(p, "w", encoding="utf-8")
    try:
        fh.write("https://example.invalid/a\nhttps://example.invalid/b\n")
        fh.write("https://example.inval")          # the writer is mid-URL
        fh.flush()

        from bulk_downloader.watch_folder import scan_once

        # Precondition: the file exists, the writer still owns it, and the
        # bytes on disk are a strict prefix of what will be written.
        assert p.exists() and not fh.closed
        bytes_when_polled = p.stat().st_size
        assert bytes_when_polled > 0

        found = scan_once(tmp_path)

        assert [f.name for f in found] == [], (
            "scan_once returned a file whose writer is STILL APPENDING "
            "(%d bytes on disk, fd open); process_file would read a torn "
            "prefix and then move the file out from under the writer"
            % bytes_when_polled)
    finally:
        fh.close()


def test_the_processed_audit_copy_matches_exactly_what_was_queued(tmp_path):
    """The audit trail must not affirm URLs that never reached the queue.

    This is the operator-facing half of the row: `.processed/` is the record
    the operator trusts, so a file whose tail was lost must never appear there
    claiming a complete import."""
    from bulk_downloader import watch_folder as wf

    p = tmp_path / "feed.txt"
    fh = open(p, "w", encoding="utf-8")
    try:
        fh.write("https://example.invalid/a\nhttps://example.invalid/b\n")
        fh.write("https://example.inval")
        fh.flush()

        runner = _FakeRunner()
        # Poll while the writer is live; nothing may be eligible.
        assert wf.scan_once(tmp_path) == [], (
            "the growing file was offered for import")

        # The writer finishes.
        fh.write("id/c\n")
        fh.flush()
    finally:
        fh.close()

    _age(p, 120)  # the file is now demonstrably quiescent
    eligible = wf.scan_once(tmp_path)
    assert [f.name for f in eligible] == ["feed.txt"], (
        "a finished, quiescent file must import on its first eligible poll")

    result = wf.process_file(eligible[0], runner)
    assert result["ok"] is True
    assert result["urls_imported"] == 3, (
        "expected the FULL 3-URL file, got %r" % (result["urls_imported"],))
    assert runner.loaded == [
        "https://example.invalid/a",
        "https://example.invalid/b",
        "https://example.invalid/c",
    ], "the queue did not receive the complete file: %r" % (runner.loaded,)

    processed = list((tmp_path / ".processed").glob("*feed.txt"))
    assert len(processed) == 1, "expected exactly one audit copy"
    audit_urls = [ln.strip() for ln in
                  processed[0].read_text(encoding="utf-8").splitlines()
                  if ln.strip()]
    assert audit_urls == runner.loaded, (
        "the .processed audit copy must contain EXACTLY the URL set that was "
        "queued; audit=%r queued=%r" % (audit_urls, runner.loaded))


def test_a_growing_file_is_deferred_on_every_poll_while_it_grows(tmp_path):
    """Deferral is not a one-shot: each poll while the writer appends must
    decline, and the count of declines is asserted nonzero and exact."""
    from bulk_downloader.watch_folder import scan_once

    p = tmp_path / "slow_writer.txt"
    fh = open(p, "w", encoding="utf-8")
    deferrals = 0
    sizes = []
    try:
        for i in range(3):
            fh.write("https://example.invalid/%d\n" % i)
            fh.flush()
            sizes.append(p.stat().st_size)
            if scan_once(tmp_path) == []:
                deferrals += 1
    finally:
        fh.close()

    assert sizes == sorted(sizes) and len(set(sizes)) == 3, (
        "the fixture did not actually grow the file across polls: %r" % (sizes,))
    assert deferrals == 3, (
        "expected the growing file deferred on all 3 polls, got %d" % deferrals)

    # And exactly once after it settles.
    _age(p, 120)
    assert [f.name for f in scan_once(tmp_path)] == ["slow_writer.txt"]


# ── negative controls ─────────────────────────────────────────────────


def test_a_complete_quiescent_file_still_imports_on_its_first_poll(tmp_path):
    """The guard must not turn every import into a no-op -- the ordinary
    drop-a-file workflow keeps working."""
    from bulk_downloader import watch_folder as wf

    p = tmp_path / "done.txt"
    p.write_text("https://example.invalid/x\nhttps://example.invalid/y\n",
                 encoding="utf-8")
    _age(p, 120)

    found = wf.scan_once(tmp_path)
    assert [f.name for f in found] == ["done.txt"], (
        "a quiescent file was wrongly deferred -- the guard is too strict")

    runner = _FakeRunner()
    result = wf.process_file(found[0], runner)
    assert result["ok"] is True and result["urls_imported"] == 2
    assert len(runner.loaded) == 2


def test_a_stat_failure_defers_rather_than_importing(tmp_path, monkeypatch):
    """CLAUDE.md A7: an age check that could not be MEASURED has not proven
    the file quiescent.  Fail closed."""
    from bulk_downloader import watch_folder as wf

    p = tmp_path / "unstattable.txt"
    p.write_text("https://example.invalid/z\n", encoding="utf-8")
    _age(p, 120)

    # Control: it IS eligible when stat works.
    assert [f.name for f in wf.scan_once(tmp_path)] == ["unstattable.txt"], (
        "precondition failed -- the file is not otherwise eligible, so the "
        "stat-failure result below would prove nothing")

    # The failure must land on the QUIESCENCE check, not earlier.  Path.is_file()
    # re-raises EIO (errno 5 is not in pathlib's ignored set), so a stat that
    # explodes on every call makes scan_once skip the file at the is_file()
    # probe and never reach _is_quiescent at all -- the test would then pass by
    # unrelated early refusal and the fail-closed branch would be pinned by
    # nothing (CLAUDE.md A7).  So call 1 (is_file) is passed through and only
    # call 2 onward raises.
    calls = []
    real_stat = Path.stat

    def exploding_stat(self, *a, **kw):
        if self.name == "unstattable.txt":
            calls.append(self.name)
            if len(calls) >= 2:
                raise OSError(5, "Input/output error")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(Path, "stat", exploding_stat)
    found = wf.scan_once(tmp_path)

    assert len(calls) >= 2, (
        "the quiescence check never stat()ed the file -- scan_once refused it "
        "earlier, so the fail-closed branch was NOT exercised (calls=%r)"
        % (calls,))
    assert found == [], (
        "an unstattable file must be DEFERRED as UNKNOWN, not imported; "
        "scan_once returned %r" % (found,))


def test_the_scan_now_endpoint_helper_shares_the_guard(tmp_path):
    """`/api/sites/<sid>/watch/scan_now` calls the same scan_once, so the
    endpoint inherits the guard rather than needing its own.  Pinned so a
    future refactor cannot give the request path a naked scan."""
    from bulk_downloader import watch_folder as wf

    p = tmp_path / "fresh.txt"
    p.write_text("https://example.invalid/q\n", encoding="utf-8")
    # Freshly written -- the writer could still be appending.
    assert wf.scan_once(tmp_path) == [], (
        "a just-written file was offered to the forced-scan path")
    _age(p, 120)
    assert [f.name for f in wf.scan_once(tmp_path)] == ["fresh.txt"]


def test_the_watch_loop_passes_its_own_poll_interval_as_the_age_floor(tmp_path):
    """The loop's guard must scale with ITS configured interval: a 60s poll
    means a file could have been growing for up to 60s between looks."""
    from bulk_downloader.watch_folder import watch_loop_for_site

    seen: list = []
    runner = _FakeRunner({
        "watch_enabled": True,
        "watch_folder": str(tmp_path),
        "watch_poll_seconds": 60,
    })
    stop = threading.Event()

    def recording_scan(folder, *a, **kw):
        seen.append((folder, a, kw))
        stop.set()
        return []

    t = threading.Thread(target=watch_loop_for_site,
                         args=(runner, stop, recording_scan, lambda s: None))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "the watch loop did not exit"

    assert len(seen) == 1, "expected exactly one scan, got %d" % len(seen)
    folder, args, kwargs = seen[0]
    assert folder == str(tmp_path)
    passed = list(args) + list(kwargs.values())
    assert 60 in passed, (
        "the loop must hand its own poll interval to the scanner as the age "
        "floor; it passed %r / %r" % (args, kwargs))


def test_a_legacy_single_argument_scanner_stub_still_works(tmp_path):
    """Back-compat: poll_fn is a documented injection point and older stubs
    take one positional arg.  The loop must not break them."""
    from bulk_downloader.watch_folder import watch_loop_for_site

    calls = []
    runner = _FakeRunner({
        "watch_enabled": True,
        "watch_folder": str(tmp_path),
        "watch_poll_seconds": 5,
    })
    stop = threading.Event()

    def one_arg_scan(folder):          # no min-age parameter at all
        calls.append(folder)
        stop.set()
        return []

    t = threading.Thread(target=watch_loop_for_site,
                         args=(runner, stop, one_arg_scan, lambda s: None))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "the watch loop did not exit"
    assert calls == [str(tmp_path)], (
        "a one-argument poll_fn stub must still be callable; got %r" % (calls,))


def test_an_internal_typeerror_is_not_mistaken_for_a_one_arg_stub(tmp_path):
    """A7 self-audit control: the arity fallback must not swallow a TypeError
    raised INSIDE the scanner and silently scan a second time.

    Deciding arity by catching TypeError would call poll_fn twice here."""
    from bulk_downloader.watch_folder import watch_loop_for_site

    calls = []
    runner = _FakeRunner({
        "watch_enabled": True,
        "watch_folder": str(tmp_path),
        "watch_poll_seconds": 5,
    })
    stop = threading.Event()

    def exploding_scan(folder, min_age_s=0.0):
        calls.append(folder)
        stop.set()
        raise TypeError("a bug deep inside the scanner, not an arity mismatch")

    t = threading.Thread(target=watch_loop_for_site,
                         args=(runner, stop, exploding_scan, lambda s: None))
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "the watch loop did not exit"

    assert len(calls) == 1, (
        "the scanner ran %d times -- an internal TypeError was mistaken for a "
        "one-argument stub and the scan was silently repeated" % len(calls))
    # The error is reported to the operator rather than swallowed.
    assert any("TypeError" in msg for _kind, msg in runner.events), (
        "the scanner's own failure was never surfaced: %r" % (runner.events,))
