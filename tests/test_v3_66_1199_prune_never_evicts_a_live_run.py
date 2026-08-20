"""v3.66.1199 -- the run-context prune must not evict a run still being written.

Row 179. `prune` ranked run directories by DIRECTORY mtime and kept the newest
20. But a chain is append-only, and appending to a file updates the FILE's mtime,
not the containing directory's, so an actively-running suite's run dir mtime
froze at the moment its 24 chain files were created and then ranked as stale.
About 35 tests in this suite spawn a nested pytest as a subprocess; each nested
run's `pytest_unconfigure` calls the SHARED `_run_context.prune()`, and once 20
run dirs had a newer directory mtime than the live outer run, that prune EVICTED
the outer run mid-suite -- all 24 worker chains vanished at once, and only the
files each worker ran after the eviction survived. Measured across three
canonical `-n 24` runs: 126, 151 and 171 of 1386 files recorded, 20-22 of 24
workers, an ~88% loss that made `bd-ladder` replay and any chain-derived
denominator untrustworthy.

The fix ranks runs by their newest CONTENT mtime (the directory's own mtime or
the newest mtime among its files), so a run being actively appended always ranks
newest and is never in the eviction set, while retention stays bounded at 20 for
genuinely stale runs.
"""
import os
import time

import _run_context as rc

BD_GATE_SCOPE = "module"


def _make_run(root, name, dir_mtime, file_mtime):
    d = root / name
    d.mkdir()
    chain = d / "gw0.chain"
    chain.write_text("tests/x.py\n", encoding="utf-8")
    os.utime(chain, (file_mtime, file_mtime))
    os.utime(d, (dir_mtime, dir_mtime))
    return d


def test_prune_does_not_evict_a_run_whose_chain_is_being_appended(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    now = time.time()

    # 25 genuinely idle runs whose DIRECTORY mtime is recent (newer than the
    # live run's) but whose content is old -- the shape prune was ranking by.
    for i in range(25):
        _make_run(tmp_path, "idle%02d" % i, dir_mtime=now - i, file_mtime=now - 10000)

    # THE LIVE RUN: its directory mtime is OLD (frozen when its chains were first
    # created, as it is for a real suite whose 24 chains exist and are only
    # appended thereafter), but its chain was JUST appended -- fresh content.
    live = _make_run(tmp_path, "LIVE", dir_mtime=now - 5000, file_mtime=now)

    assert len(list(tmp_path.iterdir())) == 26

    rc.prune(keep=20)

    assert live.is_dir() and (live / "gw0.chain").exists(), (
        "prune evicted a LIVE run. It ranked run directories by directory mtime, "
        "which freezes on an append-only chain, so an actively-written run looked "
        "stale and a nested pytest's prune deleted it mid-suite (row 179)."
    )
    # Retention is still bounded: 26 runs, keep=20, so the truly-stale excess goes.
    assert len(list(tmp_path.iterdir())) <= 20, "prune stopped bounding retention at keep"


def test_prune_ranks_by_the_newest_file_not_the_oldest(tmp_path, monkeypatch):
    """A live run has several chain files: some created early (one per worker,
    never re-touched, so an OLD mtime) and some just appended (FRESH). Ranking
    must key on the NEWEST of them -- a run appended a millisecond ago is live no
    matter how old its earliest chain is. This is the case a `max`->`min` slip in
    _newest_touch would break, so it is pinned separately."""
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    now = time.time()

    # 21 idle runs, content aged to a middle value.
    for i in range(21):
        _make_run(tmp_path, "idle%02d" % i, dir_mtime=now - i, file_mtime=now - 4000)

    # A live run: an OLD first chain plus a FRESH just-appended one. Its newest
    # content (the fresh file) is younger than every idle run; its oldest (the
    # early file) is older than every idle run. Only ranking by the NEWEST keeps
    # it; ranking by the oldest would evict it.
    live = tmp_path / "LIVE_MULTI"
    live.mkdir()
    old_chain = live / "gw0.chain"
    old_chain.write_text("tests/early.py\n", encoding="utf-8")
    os.utime(old_chain, (now - 8000, now - 8000))
    fresh_chain = live / "gw7.chain"
    fresh_chain.write_text("tests/just_now.py\n", encoding="utf-8")
    os.utime(fresh_chain, (now, now))
    os.utime(live, (now - 8000, now - 8000))

    rc.prune(keep=20)

    assert live.is_dir(), (
        "prune evicted a live run by ranking it on its OLDEST chain file. It "
        "must rank by the newest content, or a run whose first worker chain is "
        "old but which is still being appended looks stale (row 179)."
    )


def test_prune_still_removes_genuinely_stale_runs(tmp_path, monkeypatch):
    """The negative control: prune must not become a no-op. With more than `keep`
    runs all equally idle, the oldest-by-content are still removed."""
    monkeypatch.setattr(rc, "sink_dir", lambda: tmp_path)
    now = time.time()
    for i in range(30):
        _make_run(tmp_path, "run%02d" % i, dir_mtime=now - i, file_mtime=now - 1000 - i)
    removed = rc.prune(keep=20)
    assert removed == 10, f"expected 10 stale runs removed, got {removed}"
    assert len(list(tmp_path.iterdir())) == 20
