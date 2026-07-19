"""v3.43.30 regression tests — watch folder for .txt URL imports.

Coverage:
  - parse_url_file: format handling (blank, comments, URL detection,
    dedup, encoding fallback)
  - scan_once: skips dotted/non-txt, sorts by mtime
  - process_file: move to .processed/ on success, .failed/ on error,
    handles load_urls failure
  - watch_loop_for_site: respects watch_enabled toggle, stop event,
    polls in cycle, configurable interval
  - Endpoints registered
  - DEFAULTS + CFG_FIELDS
  - Runner integration (load_urls called with priority)
  - UI: modal fields, handlers
"""
from __future__ import annotations

# [SAST 3:13pm 13 may] removed unused: import json
# [SAST 3:13pm 13 may] removed unused: import os
import tempfile
import threading
import time
from pathlib import Path
# [SAST 3:13pm 13 may] removed unused: from unittest import mock


_REPO_ROOT = Path(__file__).resolve().parent.parent
_APP_PY = _REPO_ROOT / "bulk_downloader" / "app.py"
_RUNNER_PY = _REPO_ROOT / "bulk_downloader" / "runner.py"
_WATCH_PY = _REPO_ROOT / "bulk_downloader" / "watch_folder.py"


# ── parse_url_file ────────────────────────────────────────────────────


def _APP_SRC():
    """app.py + extracted app_*.py blueprint modules (Phase 4 thin-core-shell)."""
    import bulk_downloader as _bd, pathlib as _pl
    _pkg = _pl.Path(_bd.__file__).parent
    _parts = [(_pkg / 'app.py').read_text(encoding='utf-8')]
    _parts += [p.read_text(encoding='utf-8') for p in sorted(_pkg.glob('app_*.py'))]
    return '\n'.join(_parts)


def test_parse_url_file_basic():
    """One URL per line, blanks and comments ignored."""
    from bulk_downloader.watch_folder import parse_url_file
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text(
            "# header comment\n"
            "https://example.com/a\n"
            "\n"
            "  https://example.com/b  \n"
            "# another comment\n"
            "https://example.com/c\n",
            encoding="utf-8")
        urls, errors = parse_url_file(p)
        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]
        assert errors == []


def test_parse_url_file_dedup():
    """Within a single file, duplicate URLs are dropped — preserve
    first-seen order."""
    from bulk_downloader.watch_folder import parse_url_file
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text(
            "https://example.com/a\n"
            "https://example.com/b\n"
            "https://example.com/a\n"  # dup of #1
            "https://example.com/c\n"
            "https://example.com/b\n",  # dup of #2
            encoding="utf-8")
        urls, _ = parse_url_file(p)
        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]


def test_parse_url_file_reports_non_urls():
    """Non-URL lines are errors (with line numbers), but parsing
    continues so valid URLs still extract."""
    from bulk_downloader.watch_folder import parse_url_file
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text(
            "https://example.com/a\n"
            "garbage line not a URL\n"
            "https://example.com/b\n",
            encoding="utf-8")
        urls, errors = parse_url_file(p)
        assert urls == [
            "https://example.com/a",
            "https://example.com/b",
        ]
        assert len(errors) == 1
        assert "line 2" in errors[0]


def test_parse_url_file_accepts_magnet():
    """Magnet links are valid URLs for watch import (qB bridge will
    auto-route them)."""
    from bulk_downloader.watch_folder import parse_url_file
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text(
            "magnet:?xt=urn:btih:abc123\n"
            "https://example.com/a\n",
            encoding="utf-8")
        urls, errors = parse_url_file(p)
        assert len(urls) == 2
        assert urls[0].startswith("magnet:")


def test_parse_url_file_handles_bad_encoding():
    """Files with non-UTF-8 bytes (latin-1 etc.) should still parse —
    we don't want a single bad byte to lose all the URLs."""
    from bulk_downloader.watch_folder import parse_url_file
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        # Write latin-1 encoded text with non-ASCII bytes
        p.write_bytes(b"https://example.com/caf\xe9\n"
                       b"https://example.com/normal\n")
        urls, _ = parse_url_file(p)
        assert len(urls) == 2


def test_parse_url_file_missing_returns_error():
    """A nonexistent file shouldn't crash — return empty + error."""
    from bulk_downloader.watch_folder import parse_url_file
    with tempfile.TemporaryDirectory() as td:
        urls, errors = parse_url_file(Path(td) / "does_not_exist.txt")
        assert urls == []
        assert len(errors) == 1
        assert "read failed" in errors[0].lower()


# ── scan_once ─────────────────────────────────────────────────────────

def test_scan_once_lists_only_txt():
    """Only .txt files at the top level. No .processed, .failed, or
    other extensions."""
    from bulk_downloader.watch_folder import scan_once
    with tempfile.TemporaryDirectory() as td:
        Path(td, "a.txt").write_text("x")
        Path(td, "b.txt").write_text("y")
        Path(td, "c.md").write_text("z")
        Path(td, ".hidden.txt").write_text("h")  # dotted hidden file
        Path(td, ".processed").mkdir()
        Path(td, ".processed/old.txt").write_text("old")
        result = scan_once(td)
        names = {p.name for p in result}
        assert names == {"a.txt", "b.txt"}


def test_scan_once_sorts_by_mtime():
    """Older files first so arrival order is preserved."""
    from bulk_downloader.watch_folder import scan_once
    with tempfile.TemporaryDirectory() as td:
        Path(td, "first.txt").write_text("1")
        time.sleep(0.05)
        Path(td, "second.txt").write_text("2")
        time.sleep(0.05)
        Path(td, "third.txt").write_text("3")
        result = scan_once(td)
        assert [p.name for p in result] == ["first.txt", "second.txt", "third.txt"]


def test_scan_once_handles_missing_folder():
    """Nonexistent folder = empty result, no error."""
    from bulk_downloader.watch_folder import scan_once
    with tempfile.TemporaryDirectory() as td:
        result = scan_once(Path(td) / "does_not_exist")
        assert result == []


# ── process_file ──────────────────────────────────────────────────────

class _FakeRunner:
    """Minimal stub that mimics SiteRunner just for the methods watch
    uses (load_urls + log_event + config)."""
    def __init__(self, config=None, raise_on_load=False):
        self.config = config or {}
        self.events = []
        self.loaded = []
        self.priority_calls = []
        self.raise_on_load = raise_on_load
    def load_urls(self, urls, dedupe=True, folder_scan=False):
        # Real SiteRunner signature -- there is NO url_priorities param (the old
        # fake had one, which masked THC: the real call always TypeError'd).
        if self.raise_on_load:
            raise RuntimeError("simulated load failure")
        self.loaded.append(list(urls))
    def bulk_priority(self, urls, priority):
        self.priority_calls.append((list(urls), priority))
        return len(urls)
    def log_event(self, kind, message, url=None, extra=None):
        self.events.append((kind, message))


def test_process_file_success_moves_to_processed():
    from bulk_downloader.watch_folder import process_file
    runner = _FakeRunner()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text("https://example.com/a\nhttps://example.com/b\n")
        result = process_file(p, runner)
        assert result["ok"] is True
        assert result["urls_imported"] == 2
        # File moved out of top level
        assert not p.exists()
        # And lives under .processed/ now
        proc = Path(td) / ".processed"
        assert proc.exists()
        assert len(list(proc.iterdir())) == 1
        # The processed filename keeps the original suffix
        moved = next(proc.iterdir())
        assert moved.name.endswith("urls.txt")
        # Runner saw the load_urls call
        assert len(runner.loaded) == 1
        assert runner.loaded[0] == ["https://example.com/a",
                                    "https://example.com/b"]


def test_process_file_empty_moves_to_failed():
    """A file with no usable URLs goes to .failed/ with a .log."""
    from bulk_downloader.watch_folder import process_file
    runner = _FakeRunner()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "garbage.txt"
        p.write_text("just some lines\nno URLs here\n# comment\n")
        result = process_file(p, runner)
        assert result["ok"] is False
        assert result["urls_imported"] == 0
        fail = Path(td) / ".failed"
        assert fail.exists()
        # Should have the moved file AND a .log sibling
        files = list(fail.iterdir())
        assert any(f.name.endswith("garbage.txt") for f in files)
        assert any(f.name.endswith(".log") for f in files)


def test_process_file_load_failure_moves_to_failed():
    """If load_urls raises (DB error etc.), the file goes to .failed
    so the next poll doesn't infinite-loop on it."""
    from bulk_downloader.watch_folder import process_file
    runner = _FakeRunner(raise_on_load=True)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text("https://example.com/a\n")
        result = process_file(p, runner)
        assert result["ok"] is False
        # Failed; moved to .failed/
        assert not p.exists()
        assert (Path(td) / ".failed").exists()


def test_process_file_with_priority():
    from bulk_downloader.watch_folder import process_file
    runner = _FakeRunner()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text("https://example.com/a\n")
        result = process_file(p, runner, priority="high")
        assert result["ok"] is True
        # THC: priority is applied via runner.bulk_priority (load_urls has no
        # url_priorities param; the old assertion tested a non-existent contract).
        assert runner.priority_calls == [(["https://example.com/a"], "high")]


def test_process_file_no_priority_omits_priorities_dict():
    """When priority is 'normal' (default), we shouldn't bother
    populating the priorities dict — saves the runner a copy."""
    from bulk_downloader.watch_folder import process_file
    runner = _FakeRunner()
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "urls.txt"
        p.write_text("https://example.com/a\n")
        process_file(p, runner, priority="normal")
        assert runner.priority_calls == [], "normal priority must not tag rows"


def test_process_file_collision_safe():
    """Two files processed in quick succession with the same name
    shouldn't overwrite each other in .processed/."""
    from bulk_downloader.watch_folder import process_file
    runner = _FakeRunner()
    with tempfile.TemporaryDirectory() as td:
        # First file
        p1 = Path(td) / "urls.txt"
        p1.write_text("https://example.com/a\n")
        process_file(p1, runner)
        # Recreate with same name
        p2 = Path(td) / "urls.txt"
        p2.write_text("https://example.com/b\n")
        process_file(p2, runner)
        proc = Path(td) / ".processed"
        # Both files preserved in .processed/
        assert len(list(proc.iterdir())) == 2


# ── watch_loop_for_site ───────────────────────────────────────────────

def test_watch_loop_respects_stop_event():
    """The loop must exit promptly when stop is set, even when sleeping."""
    from bulk_downloader.watch_folder import watch_loop_for_site
    runner = _FakeRunner({"watch_enabled": False})
    stop = threading.Event()

    # Use a no-sleep fake to avoid real waiting
    sleep_calls = []
    def fake_sleep(s): sleep_calls.append(s)
    # Quick stop after one iteration
    thread = threading.Thread(
        target=watch_loop_for_site,
        args=(runner, stop, lambda f: [], fake_sleep))
    thread.start()
    time.sleep(0.05)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive(), "watch loop didn't honor stop event"


def test_watch_loop_skips_when_disabled():
    """When watch_enabled=False, the loop polls less aggressively but
    doesn't call into the scanner."""
    from bulk_downloader.watch_folder import watch_loop_for_site
    runner = _FakeRunner({"watch_enabled": False})
    stop = threading.Event()
    scan_count = [0]
    def counting_scan(folder):
        scan_count[0] += 1
        return []
    thread = threading.Thread(
        target=watch_loop_for_site,
        args=(runner, stop, counting_scan, lambda s: None))
    thread.start()
    time.sleep(0.1)
    stop.set()
    thread.join(timeout=2)
    # Should NOT have called the scanner — runner is disabled
    assert scan_count[0] == 0


def test_watch_loop_calls_scanner_when_enabled():
    from bulk_downloader.watch_folder import watch_loop_for_site
    runner = _FakeRunner({
        "watch_enabled": True,
        "watch_folder": "/tmp/dummy",
        "watch_poll_seconds": 5,
    })
    stop = threading.Event()
    scan_count = [0]
    def counting_scan(folder):
        scan_count[0] += 1
        if scan_count[0] >= 2:
            stop.set()
        return []
    thread = threading.Thread(
        target=watch_loop_for_site,
        args=(runner, stop, counting_scan, lambda s: None))
    thread.start()
    thread.join(timeout=2)
    assert scan_count[0] >= 1, "scanner never called"


# ── DEFAULTS / CFG_FIELDS ─────────────────────────────────────────────

def test_watch_defaults_present():
    src = _APP_SRC()
    assert '"watch_enabled": False' in src
    assert '"watch_folder": ""' in src
    assert '"watch_poll_seconds": 30' in src
    assert '"watch_url_priority": "normal"' in src


def test_watch_fields_in_cfg_fields():
    src = _APP_SRC()
    import re
    m = re.search(r"CFG_FIELDS\s*=\s*\[", src)
    start = m.end()
    depth = 1
    pos = start
    while pos < len(src) and depth > 0:
        if src[pos] == "[":
            depth += 1
        elif src[pos] == "]":
            depth -= 1
        pos += 1
    fields_block = src[start:pos - 1]
    for fld in ("watch_enabled", "watch_folder",
                 "watch_poll_seconds", "watch_url_priority"):
        assert f'"{fld}"' in fields_block, f"{fld} missing from CFG_FIELDS"


# ── Endpoints ─────────────────────────────────────────────────────────

def test_watch_endpoints_registered():
    src = _APP_SRC()
    assert '/api/sites/<sid>/watch/scan_now' in src
    assert "def api_watch_scan_now" in src
    assert '/api/sites/<sid>/watch/status' in src
    assert "def api_watch_status" in src


def test_watch_thread_start_helper_exists():
    """Per-site daemon spawn must happen at startup AND on site
    create/update."""
    src = _APP_PY.read_text(encoding="utf-8")
    assert "def _start_watch_folder_threads" in src
    assert "_start_watch_folder_threads()" in src


def test_watch_thread_disable_keepalive_gate():
    """Tests must not spawn threads — gated by BD_DISABLE_KEEPALIVE."""
    src = _APP_PY.read_text(encoding="utf-8")
    fn_pos = src.find("def _start_watch_folder_threads")
    assert fn_pos > 0
    fn_body = src[fn_pos:fn_pos + 1500]
    assert "BD_DISABLE_KEEPALIVE" in fn_body, (
        "Watch-folder thread spawn must be gated by "
        "BD_DISABLE_KEEPALIVE so tests don't accumulate threads"
    )


def test_watch_thread_stopped_on_site_delete():
    """When a site is deleted, its watch thread must stop. Otherwise
    threads accumulate over the app's lifetime."""
    src = _APP_SRC()
    del_pos = src.find("def api_delete(sid)")
    assert del_pos > 0
    del_body = src[del_pos:del_pos + 2000]
    assert "_watch_stops" in del_body
    assert ".set()" in del_body  # stop event triggered


# ── UI ────────────────────────────────────────────────────────────────





