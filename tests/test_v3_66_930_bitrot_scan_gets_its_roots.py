"""v3.66.930: the nightly bit-rot scan had nothing to resolve against, so
it decided nothing.

`bg_scheduler._run_bitrot` called `run_scan(scan_fraction=..., min_age_days=...,
max_files=...)` with NO download_dir. `final_filename` is a bare BASENAME
(v3.66.925), so `_resolve_recorded` returned "unknown" for every relative row
and `run_scan` reported `unknown: N`. That is honest -- it is a scan saying it
could not see its subject -- but it means the nightly job has never verified a
relative row, and `alerts_engine` watches a signal it could not produce.

run_scan's own docstring said so and named the fix as a separate cut:
"Sourcing the configured roots belongs with the path-allowlist validation the
library routes already do (app_library.py:262) and is a separate cut, not a
line here." This is that cut.

MULTI-ROOT IS THE POINT, not a generalisation for its own sake. download_dir
is configured PER SITE, so a single string cannot express the library on a
multi-site install -- handing the scan one root would silently verify part of
the library and report the rest as unknown, which is the same blindness the
fix is removing. Resolution therefore takes a sequence, and a basename that
matches files under TWO roots stays "ambiguous" rather than being guessed.

The enumerator is ONE function. healthcheck._check_disk built the same list
inline; three copies of a denominator is how the copy nobody updated becomes
the one that runs (CLAUDE.md section 5, S0/S8).
"""
from __future__ import annotations

import pytest

from bulk_downloader import bitrot as _br
from bulk_downloader import bg_scheduler as _sched
from bulk_downloader import library_final as _lf


# ── the shared enumerator ─────────────────────────────────────────────

def test_download_roots_enumerates_every_configured_site():
    roots = _lf.download_roots({
        "a": {"download_dir": "/srv/one"},
        "b": {"download_dir": "/srv/two"},
    })
    assert roots == ["/srv/one", "/srv/two"]


def test_download_roots_dedupes_and_drops_blanks():
    roots = _lf.download_roots({
        "a": {"download_dir": "/srv/one"},
        "b": {"download_dir": ""},
        "c": {"download_dir": "/srv/one"},
        "d": {},
    })
    assert roots == ["/srv/one"]


def test_download_roots_tolerates_an_absent_config():
    assert _lf.download_roots(None) == []
    assert _lf.download_roots({}) == []


# ── multi-root resolution ─────────────────────────────────────────────

def test_index_merges_every_root(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "one.mkv").write_bytes(b"x")
    (b / "two.mkv").write_bytes(b"y")

    idx = _lf._basename_index([str(a), str(b)])

    assert set(idx) == {"one.mkv", "two.mkv"}


def test_a_row_under_the_second_root_resolves(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    target = b / "nested" / "show.mkv"
    target.parent.mkdir()
    target.write_bytes(b"data")

    roots = [str(a), str(b)]
    idx = _lf._basename_index(roots)
    p, state = _lf._resolve_recorded("show.mkv", roots, idx)

    assert state == "resolved", (
        "a file under the SECOND configured root was not found; a scan given "
        "one root reports the rest of the library as unknown")
    assert p == target


def test_the_same_basename_under_two_roots_stays_ambiguous(tmp_path):
    """Guessing first-match-wins would hash the wrong twin and report a
    modification that is an artefact of the guess."""
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "dup.mkv").write_bytes(b"one")
    (b / "dup.mkv").write_bytes(b"two")

    roots = [str(a), str(b)]
    idx = _lf._basename_index(roots)
    _p, state = _lf._resolve_recorded("dup.mkv", roots, idx)

    assert state == "ambiguous"


def test_no_roots_is_still_unknown_not_missing(tmp_path):
    """The over-correction guard. A row that cannot be placed is not
    evidence of rot, and must never be recorded as missing."""
    idx = _lf._basename_index([])
    _p, state = _lf._resolve_recorded("anything.mkv", [], idx)
    assert state == "unknown"


# ── backward compatibility: five call sites still pass a str ──────────

def test_a_single_string_root_still_works(tmp_path):
    (tmp_path / "solo.mkv").write_bytes(b"z")
    idx = _lf._basename_index(str(tmp_path))
    p, state = _lf._resolve_recorded("solo.mkv", str(tmp_path), idx)
    assert state == "resolved" and p == tmp_path / "solo.mkv"


def test_an_empty_string_root_is_still_unknown():
    idx = _lf._basename_index("")
    _p, state = _lf._resolve_recorded("x.mkv", "", idx)
    assert state == "unknown" and idx == {}


# ── the nightly task actually receives them ───────────────────────────

def test_the_nightly_task_passes_the_configured_roots(monkeypatch):
    """The defect itself: the registered callable invoked run_scan with no
    roots at all, so the nightly job could decide nothing."""
    seen = {}

    def _fake_run_scan(**kw):
        seen.update(kw)
        return {"checked": 0}

    monkeypatch.setattr(_br, "run_scan", _fake_run_scan)
    captured = {}
    monkeypatch.setattr(_sched, "register",
                        lambda name, fn, **kw: captured.setdefault(name, fn))

    _sched.register_default_tasks(
        s_cfg_getter=lambda: {"s1": {"download_dir": "/srv/media"}})
    assert "bitrot.nightly_scan" in captured, sorted(captured)
    captured["bitrot.nightly_scan"]()

    assert seen.get("download_dirs") == ["/srv/media"], (
        "the nightly scan still runs with no roots; it reports unknown=N and "
        f"decides nothing. got {seen!r}")


def test_the_nightly_task_survives_a_missing_getter(monkeypatch):
    """Over-correction guard: no s_cfg_getter must not raise inside the
    scheduler loop, it must just scan with no roots as before."""
    seen = {}
    monkeypatch.setattr(_br, "run_scan", lambda **kw: seen.update(kw) or {})
    captured = {}
    monkeypatch.setattr(_sched, "register",
                        lambda name, fn, **kw: captured.setdefault(name, fn))

    _sched.register_default_tasks(s_cfg_getter=None)
    captured["bitrot.nightly_scan"]()

    assert seen.get("download_dirs") == []


# ── run_scan threads them through ─────────────────────────────────────

def test_run_scan_accepts_download_dirs(monkeypatch, tmp_path):
    """The seam that matters: whatever run_scan is given must reach the
    index, or the wiring above is decorative."""
    import contextlib

    from bulk_downloader import db as _db

    class _Row:
        def __getitem__(self, _k):
            return 1

    class _Cx:
        def execute(self, *_a, **_kw):
            return type("C", (), {"fetchone": lambda _s: _Row()})()

    @contextlib.contextmanager
    def _fake_conn():
        yield _Cx()

    # run_scan returns EARLY when the library is empty, so a stubbed
    # _candidates alone never reaches the index and the assertion below would
    # pass over a function that was never called.
    monkeypatch.setattr(_db, "db_conn", _fake_conn)
    monkeypatch.setattr(_br, "_ensure_integrity_table", lambda: None)
    monkeypatch.setattr(_br, "_candidates", lambda **kw: [])

    got = {}
    monkeypatch.setattr(_lf, "_basename_index",
                        lambda d: got.setdefault("dirs", list(d)) or {})
    _br.run_scan(download_dirs=["/srv/one", "/srv/two"])
    assert got.get("dirs") == ["/srv/one", "/srv/two"]


def test_healthcheck_uses_the_shared_enumerator():
    """One enumerator, not three. A second copy is how the one nobody
    updated becomes the one that runs."""
    import inspect
    from bulk_downloader import healthcheck as _hc
    src = inspect.getsource(_hc._check_disk)
    assert "download_roots" in src, (
        "healthcheck still builds the download-root list inline")
