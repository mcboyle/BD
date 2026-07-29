"""A history row cannot say whether anything was downloaded.

THE DEFECT. `status='done'` is written by fifteen call sites, and at least five
of them transfer nothing:

  bulk_downloader/runner.py:3651              no download dir -- click, sleep, done
  bulk_downloader/runner_integrations.py:79   Stash dedup -- "Skipped (in Stash as scene N)"
  bulk_downloader/runner_transport.py:797     skip_if_exists -- "already on disk", dl.cancel()
  bulk_downloader/runner_transport.py:1291    HTTP 416 -- rename the .part, return stat()
  bulk_downloader/runner_extractors.py:344    yt-dlp "has already been downloaded"

Nothing in the row distinguishes those from a real transfer. `file_size` is not
a transfer count -- both download helpers return an on-disk stat
(`runner_transport.py:1526`, `runner_browser.py:25`), so a skipped job records
the size of the file that was already there. `message` is prose: it varies by
path, one of the five positively asserts a download that did not happen
("Downloaded via yt-dlp fallback"), and keying a check on it couples the reader
to a string written 1400 lines away.

Measured consequence on the deploy host: eight consecutive seeded runs, seven of
them `already on disk`, and L11 reported "the end-to-end pipeline has worked"
for every one. With the stale file moved aside the pipeline was confirmed
genuinely working -- so the code was fine and only the observation was blind,
which is the worst version, because the finding looks closed.

THE FIX IS TO MAKE THE PRODUCER STATE THE FACT, not to make the consumer infer
it. A denylist of "these messages mean no fetch" is already five long, would have
to grow every time a path is added, and grows silently. `bytes_fetched` is one
number the writer already knows.

AND IT ALREADY EXISTS. `runner_transport.py:1517-1521` computes

    transferred = final_size - _dl_initial_bytes

under the comment "Only count bytes ACTUALLY transferred this call (not resumed
bytes)", feeds it to the throughput EWMA, and throws it away -- while the
function returns `final_path.stat().st_size`. This cut propagates a number that
is already correct rather than deriving a new one.

WHY A RETURN VALUE AND NOT `self`. `runner.py:1120` starts one worker thread per
slot against a SHARED runner instance, so a `self._last_transferred` attribute
would be clobbered across concurrent downloads and attribute one job's bytes to
another. The count travels back through the return.

THREE STATES, AND UNKNOWN IS NOT A PASS. NULL means "this path does not record
it" -- every pre-migration row, and any call site added without the argument.
A consumer must treat NULL as unknown and unknown as not-proven, or the
migration window silently reopens the hole for the entire existing history.

RED-first: every assertion below fails on pristine source.
"""
from __future__ import annotations

import ast
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ── the denominator: every done-writing call site ────────────────────────────

def _done_call_sites() -> list[tuple[str, int, ast.Call]]:
    """Every db_log(...) whose positional `status` argument is the literal
    'done', across all tracked application source.

    AST, not grep: `status` is positional argument 3 and a textual search for
    'done' drowns in unrelated matches. `git ls-files`, not rglob: ephemeral
    agent worktrees live under the repository root and would double-count.
    """
    files = [f for f in subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "bulk_downloader/*.py"],
        capture_output=True, text=True).stdout.split("\0") if f]
    out = []
    for rel in files:
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8",
                                                    errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name != "db_log":
                continue
            if len(node.args) >= 4 and isinstance(node.args[3], ast.Constant) \
                    and node.args[3].value == "done":
                out.append((rel, node.lineno, node))
    return out


def test_the_scan_finds_the_done_call_sites():
    """A collapsed denominator would make the assertion below vacuous.

    Measured 15 at the time of writing. Asserting a floor rather than the exact
    number: a new done-writing path should fail the NEXT test by lacking the
    argument, not this one by changing a count.
    """
    sites = _done_call_sites()
    assert len(sites) >= 10, (
        f"the AST scan found only {len(sites)} db_log(..., 'done', ...) call "
        f"sites. It cannot see its subject, so every assertion below would "
        f"pass for the wrong reason."
    )


def test_every_done_site_states_whether_bytes_were_fetched():
    """THE GATE. Denominator is all done-writing sites, so a new one added
    without the argument fails here rather than silently recording NULL."""
    missing = [f"{rel}:{lineno}" for rel, lineno, node in _done_call_sites()
               if not any(kw.arg == "bytes_fetched" for kw in node.keywords)]
    assert not missing, (
        "these db_log(..., 'done', ...) call sites do not state whether any "
        "bytes were transferred, so a reader cannot tell a real download from "
        "a skip:\n  " + "\n  ".join(missing) +
        "\nPass bytes_fetched=0 on a path that transfers nothing, or the real "
        "count on one that does. Omitting it records NULL, which is unknown."
    )


@pytest.mark.parametrize("rel,lineno", [
    ("bulk_downloader/runner.py", 3651),
    ("bulk_downloader/runner_integrations.py", 79),
    ("bulk_downloader/runner_transport.py", 797),
])
def test_the_known_no_fetch_paths_record_zero(rel, lineno):
    """Not just present -- truthful.

    A fix that passed bytes_fetched=file_size everywhere would satisfy the gate
    above while recording a skip as a transfer, which is the defect restored.
    These three are the no-fetch paths whose line numbers are stable enough to
    pin; the others are covered by the denominator test.
    """
    for r, ln, node in _done_call_sites():
        if r != rel:
            continue
        if abs(ln - lineno) > 25:
            continue
        kw = {k.arg: k.value for k in node.keywords}
        got = kw.get("bytes_fetched")
        assert got is not None, f"{r}:{ln} does not pass bytes_fetched"
        assert isinstance(got, ast.Constant) and got.value == 0, (
            f"{r}:{ln} passes bytes_fetched={ast.unparse(got)}, but this path "
            f"transfers nothing -- it must record 0, not a file size."
        )
        return
    pytest.fail(f"no db_log(..., 'done', ...) found near {rel}:{lineno}")


# ── the schema, and the migration that history never had ─────────────────────

def test_db_log_accepts_and_stores_the_count(tmp_path, monkeypatch):
    from bulk_downloader import db as _db
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "h.db"))
    _db.db_init()
    _db.db_log("s1", "S", "u1", "done", "a.mp4", 100, "", bytes_fetched=100)
    _db.db_log("s1", "S", "u2", "done", "a.mp4", 100, "already on disk",
               bytes_fetched=0)
    _db.db_log("s1", "S", "u3", "done", "a.mp4", 100, "")   # omitted -> unknown
    with _db.db_conn() as cx:
        rows = dict(cx.execute(
            "SELECT url, bytes_fetched FROM history").fetchall())
    assert rows["u1"] == 100, "a real transfer must record its count"
    assert rows["u2"] == 0, "a skip must record 0, distinguishably from unknown"
    assert rows["u3"] is None, (
        "an unrecorded path must be NULL -- unknown is a third state and a "
        "consumer must be able to tell it from 0"
    )


def test_an_existing_history_table_gains_the_column(tmp_path, monkeypatch):
    """`CREATE TABLE IF NOT EXISTS` never adds a column to a live table.

    The operator's DB predates the column, so without a migration every
    deployed database keeps a history table without it and db_log's insert
    fails.

    THE OWNER OF THAT MIGRATION CHANGED. #63 added a bespoke loop inside
    db_init(); it worked, but it sat outside bulk_downloader/migrations.py --
    a versioned framework with a schema_migrations ledger, applied at
    app.py:1814, which already owns retry_after (v2), library_id (v5),
    removed_at (v6) and honeypot_score (v7). bytes_fetched is migration v8 now,
    so the column arrives through apply_pending() rather than db_init().

    This assertion was rewritten rather than deleted: the property it protects
    -- an existing database gains the column -- is unchanged and still worth
    pinning. Only the path changed.
    """
    from bulk_downloader import db as _db, migrations as _m
    p = tmp_path / "old.db"
    cx = sqlite3.connect(p)
    cx.execute("CREATE TABLE history(id INTEGER PRIMARY KEY AUTOINCREMENT, "
               "site_id TEXT, site_name TEXT, url TEXT, status TEXT, "
               "filename TEXT, file_size INTEGER, message TEXT, "
               "screenshot TEXT, ts TEXT)")
    cx.commit()
    cx.close()

    monkeypatch.setattr(_db, "DB_PATH", str(p))
    _db.db_init()
    _m.apply_pending(backup_first=False)
    with _db.db_conn() as cx:
        cols = {r[1] for r in cx.execute("PRAGMA table_info(history)")}
    assert "bytes_fetched" in cols, (
        "an existing history table did not gain bytes_fetched. Every deployed "
        "database predates the column, so without the migration db_log raises "
        "on the operator's box while passing in a fresh sandbox."
    )
    assert "honeypot_score" in cols, (
        "honeypot_score is migration v7's job and must also reach an existing "
        "table through the same path."
    )



# ── the concurrency constraint ───────────────────────────────────────────────

def test_the_transferred_count_travels_by_return_not_by_self():
    """runner.py:1120 starts one worker thread per slot against a SHARED
    runner instance. A `self.<attr>` handoff would attribute one concurrent
    download's bytes to another; the count must come back through the return.
    """
    src = (ROOT / "bulk_downloader" / "runner_transport.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_http_download":
            returns = [n for n in ast.walk(node) if isinstance(n, ast.Return)
                       and n.value is not None]
            assert returns, "_http_download has no return value"
            # EVERY return, not any. The first version of this assertion used
            # `any`, and mutation showed that passes while the main success
            # path regresses to a bare stat: the 416 branch still returns a
            # tuple and satisfies the quantifier on its own. A delegation to
            # the parallel helper is allowed because that helper is checked
            # by the same rule.
            bad = []
            for r in returns:
                if isinstance(r.value, ast.Tuple):
                    continue
                if isinstance(r.value, ast.Call) and "_http_download_parallel" \
                        in ast.unparse(r.value.func):
                    continue
                bad.append(f"line {r.lineno}: return {ast.unparse(r.value)[:60]}")
            assert not bad, (
                "these returns from _http_download do not carry the "
                "transferred-byte count:\n  " + "\n  ".join(bad) +
                "\nThe count must travel back through the return value -- a "
                "shared instance attribute races across the per-slot worker "
                "threads started at runner.py:1120."
            )
            # The 416 branch is a NO-FETCH that produces a full-size file: the
            # server refused the range because the file was already complete,
            # and the rename makes it look like a download. Its transferred
            # count must be the literal 0. Pinning only the tuple SHAPE let a
            # mutation report stat().st_size there and pass -- the original
            # defect, restored in one line.
            for r in returns:
                if not isinstance(r.value, ast.Tuple) or len(r.value.elts) != 2:
                    continue
                second = r.value.elts[1]
                if isinstance(second, ast.Constant) and second.value == 0:
                    break
            else:
                pytest.fail(
                    "no return in _http_download carries a literal 0 as its "
                    "transferred count. The HTTP 416 branch renames an "
                    "already-complete file into place without transferring a "
                    "byte; if it reports the file size instead, history claims "
                    "a download that did not happen."
                )
            # and the helper it delegates to must obey the same contract
            par = next((f for f in ast.walk(tree)
                        if isinstance(f, ast.FunctionDef)
                        and f.name == "_http_download_parallel"), None)
            assert par is not None, "_http_download_parallel not found"
            par_returns = [n for n in ast.walk(par) if isinstance(n, ast.Return)
                           and n.value is not None]
            assert par_returns and all(isinstance(r.value, ast.Tuple)
                                       for r in par_returns), (
                "_http_download_parallel does not return the transferred count, "
                "so delegating to it loses what the caller is about to record."
            )
            return
    pytest.fail("_http_download not found in runner_transport.py")
