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

def _enclosing_function(rel: str, call: ast.Call) -> str:
    """The name of the function a Call node sits inside, or "" at module level.

    Derived by walking each FunctionDef's subtree and recording ownership, which
    is exact -- ast has no parent pointers, and a line-range comparison would get
    nested definitions wrong. Cached per file: this is called once per candidate
    site and re-parsing each time is needless.
    """
    owners = _OWNER_CACHE.get(rel)
    if owners is None:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        defs = [n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        owners = {}
        # Deepest definition first, so a nested function claims its own nodes
        # before an enclosing one can. Relying on ast.walk's traversal order
        # would give the OUTER function, which is the wrong answer for a closure
        # -- and runner_transport is full of them.
        for fn in sorted(defs, key=lambda f: -f.lineno):
            for child in ast.walk(fn):
                if hasattr(child, "lineno"):
                    owners.setdefault((child.lineno, child.col_offset), fn.name)
        _OWNER_CACHE[rel] = owners
    return owners.get((call.lineno, call.col_offset), "")


_OWNER_CACHE: dict = {}


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


@pytest.mark.parametrize("rel,func,file_size_arg", [
    ("bulk_downloader/runner.py", "_process_one", "0"),
    ("bulk_downloader/runner_integrations.py", "_stash_dedup_check", "0"),
    # _do_download holds TWO done-writing sites: this skip path, which reports an
    # ALREADY-PRESENT file's size while having fetched nothing, and the genuine
    # download, which passes a real count. `existing_size` is what distinguishes
    # them, and it is the defect shape exactly -- a real file_size beside a zero
    # transfer. Keying on the function alone matched both and demanded 0 of the
    # real download too.
    ("bulk_downloader/runner_transport.py", "_do_download", "existing_size"),
])
def test_the_known_no_fetch_paths_record_zero(rel, func, file_size_arg):
    """Not just present -- truthful.

    A fix that passed bytes_fetched=file_size everywhere would satisfy the gate
    above while recording a skip as a transfer, which is the defect restored.

    KEYED ON THE ENCLOSING FUNCTION, not on a line number. This was
    parametrised as (file, lineno) with a +/-25 tolerance and the docstring
    claimed those lines were "stable enough to pin". They were not: an unrelated
    change to runner_transport.py added ~50 lines above the target, it moved from
    797 to 894, fell outside the window, and the test failed about code that was
    still correct. That is the same magic-number fragility as the fixed-width
    source windows in test_source_windows_do_not_shift.py -- a coordinate instead
    of a length -- and it is the reason this file now derives the site.

    A function name can also change, but when it does the test says
    "no longer exists" rather than silently pointing at whatever code drifted
    into range, which is the failure mode a tolerance window has.
    """
    hits = [(r, ln, node) for r, ln, node in _done_call_sites()
            if r == rel and _enclosing_function(r, node) == func
            and len(node.args) > 5
            and ast.unparse(node.args[5]) == file_size_arg]
    assert len(hits) == 1, (
        f"expected exactly one db_log(..., 'done', ..., {file_size_arg}) inside "
        f"{rel}::{func}, found {len(hits)}. Either the no-fetch path moved -- in "
        f"which case update this parametrisation -- or it stopped recording "
        f"'done', which is a behaviour change this test must not discover by "
        f"going quiet. Exactly one, not at-least-one: two matches would mean the "
        f"discriminator no longer identifies a single path.")
    r, ln, node = hits[0]
    kw = {k.arg: k.value for k in node.keywords}
    got = kw.get("bytes_fetched")
    assert got is not None, f"{r}:{ln} ({func}) does not pass bytes_fetched"
    assert isinstance(got, ast.Constant) and got.value == 0, (
        f"{r}:{ln} ({func}) passes bytes_fetched={ast.unparse(got)}, but this "
        f"path transfers nothing -- it must record 0, not a file size."
    )


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
    functions = {node.name: node for node in ast.walk(tree)
                 if isinstance(node, ast.FunctionDef)}
    sequential_names = ("_http_download", "_http_download_claimed")
    missing = [name for name in sequential_names if name not in functions]
    assert not missing, f"sequential HTTP return helper(s) missing: {missing}"
    audited_delegates = {"_http_download_claimed", "_http_download_parallel"}
    sequential_returns = []
    for name in sequential_names:
        returns = [n for n in ast.walk(functions[name])
                   if isinstance(n, ast.Return) and n.value is not None]
        assert returns, f"{name} has no return value"
        sequential_returns.extend(returns)
        bad = []
        for returned in returns:
            if isinstance(returned.value, ast.Tuple):
                if len(returned.value.elts) != 2:
                    bad.append(
                        f"line {returned.lineno}: return tuple has "
                        f"{len(returned.value.elts)} values")
                    continue
                transferred = returned.value.elts[1]
                self_reads = [node for node in ast.walk(transferred)
                              if isinstance(node, ast.Attribute)
                              and isinstance(node.value, ast.Name)
                              and node.value.id == "self"]
                if self_reads:
                    bad.append(
                        f"line {returned.lineno}: transferred count reads "
                        f"{ast.unparse(transferred)} from shared self")
                continue
            if isinstance(returned.value, ast.Call):
                delegate = ast.unparse(returned.value.func).rsplit(".", 1)[-1]
                if delegate in audited_delegates:
                    continue
            bad.append(
                f"line {returned.lineno}: return "
                f"{ast.unparse(returned.value)[:60]}")
        assert not bad, (
            f"these returns from {name} do not carry the transferred-byte "
            "count:\n  " + "\n  ".join(bad) +
            "\nThe count must travel back through the return value -- a "
            "shared instance attribute races across the per-slot worker "
            "threads started at runner.py:1120."
        )

    # The 416 branch is a NO-FETCH that produces a full-size file: the server
    # refused the range because the file was already complete. Its transferred
    # count must remain the literal 0 somewhere in the sequential return chain.
    literal_zero = [returned for returned in sequential_returns
                    if isinstance(returned.value, ast.Tuple)
                    and len(returned.value.elts) == 2
                    and isinstance(returned.value.elts[1], ast.Constant)
                    and returned.value.elts[1].value == 0]
    assert literal_zero, (
        "no sequential HTTP return carries a literal 0 as its transferred "
        "count for the no-fetch HTTP 416 branch")

    parallel = functions.get("_http_download_parallel")
    assert parallel is not None, "_http_download_parallel not found"
    parallel_returns = [node for node in ast.walk(parallel)
                        if isinstance(node, ast.Return)
                        and node.value is not None]
    assert parallel_returns and all(isinstance(node.value, ast.Tuple)
                                    for node in parallel_returns), (
        "_http_download_parallel does not return the transferred count, so "
        "delegating to it loses what the caller is about to record.")


BD_GATE_SCOPE = "repo-wide"
