"""O1826 C42 -- per-call query cost in the DB helpers stays constant.

M014 collect_site_health ran one session_history SELECT per site.
M128 retention.find_candidates ran one history_tags SELECT per history row,
     twice (age pass and size pass).
M117 maintenance._ensure_tables ran two CREATE TABLE statements on every
     add/remove/list call, so is_action_paused paid DDL on every check.
M120 mass_import._ensure_table swallowed a schema failure with a bare pass.

Statements are counted with sqlite3's trace callback on every leased
connection. Each count is checked at two population sizes, so a constant
cost and an N+1 cost cannot give the same answer. Returned data is compared
with the unbatched per-row helpers on the same fixtures.
"""

import contextlib
import os
import pathlib
import sqlite3
import time

import pytest

from bulk_downloader import app_data_layer as D
from bulk_downloader import db
from bulk_downloader import maintenance as mw
from bulk_downloader import mass_import
from bulk_downloader import retention
from bulk_downloader import tags

BD_GATE_SCOPE = "module"


@pytest.fixture
def sql_log(clean_workdir, monkeypatch):
    # fresh DB per test: module-global "schema ready" flags must not carry over
    monkeypatch.setattr(tags, "_tags_table_ready", False)
    monkeypatch.setattr(retention, "_tables_ready", False)
    monkeypatch.setattr(mass_import, "_TABLE_READY", False)
    db.db_init()
    stmts = []
    real = db.db_conn

    @contextlib.contextmanager
    def traced(*args, **kwargs):
        with real(*args, **kwargs) as cx:
            cx.set_trace_callback(stmts.append)
            try:
                yield cx
            finally:
                cx.set_trace_callback(None)

    monkeypatch.setattr(db, "db_conn", traced)
    return stmts


def _count(stmts, *needles):
    return sum(1 for s in stmts if all(n in s for n in needles))


# -- M014 collect_site_health ------------------------------------------------

def _seed_sites(n):
    with db.db_conn() as cx:
        cx.execute("DELETE FROM session_history")
    now = time.time()
    for i in range(n):
        sid = f"site{i:02d}"
        # two closed lifetimes per site. A failure before any login and a second
        # failure after a close must be ignored, and the trailing open login
        # must not leak into the next site's leading failure.
        events = [(now - 9500, "heartbeat_fail"),
                  (now - 9000, "login"), (now - 9000 + 600 * (i + 1), "heartbeat_fail"),
                  (now - 8000, "heartbeat_fail"),
                  (now - 4000, "auto_relogin_ok"), (now - 4000, "heartbeat_ok"),
                  (now - 4000 + 60 * (i + 1), "auto_relogin_fail"),
                  (now - 100, "login")]
        with db.db_conn() as cx:
            for ts, et in events:
                cx.execute(
                    "INSERT INTO session_history(ts, site_id, account_idx, event_type, detail) "
                    "VALUES(?,?,?,?,?)", (ts, sid, i % 2, et, ""))


@pytest.mark.parametrize("n", [3, 7])
def test_site_health_session_history_reads_do_not_scale_with_sites(sql_log, n):
    _seed_sites(n)
    del sql_log[:]
    out = D.collect_site_health(lookback_days=7)
    assert out["site_count"] == n
    # one failure-cluster read + one lifetime read, whatever the site count
    reads = _count(sql_log, "FROM session_history")
    assert reads == 2, f"M014: {reads} session_history reads for {n} sites"


def test_site_health_lifetimes_match_per_site_helper(sql_log):
    _seed_sites(5)
    out = D.collect_site_health(lookback_days=7)
    from statistics import median
    for s in out["sites"]:
        expected = db.session_lifetime_observations(s["site_id"], lookback_days=7)
        assert expected, s["site_id"]
        assert len(expected) == 2, (s["site_id"], expected)
        assert s["median_lifetime_sec"] == float(median(expected)), s["site_id"]


# -- M128 retention.find_candidates ------------------------------------------

def _seed_history(n, tagged):
    retention._ensure_tables()
    with db.db_conn() as cx:
        cx.execute("DELETE FROM history")
        for i in range(n):
            cx.execute(
                "INSERT INTO history(site_id, status, filename, file_size, ts) "
                "VALUES(?,?,?,?,?)",
                ("rs", "done", f"f{i}.bin", 1024 * 1024,
                 f"2020-01-{(i % 28) + 1:02d}T00:00:{i % 60:02d}"))
        ids = [r[0] for r in cx.execute("SELECT id FROM history ORDER BY id")]
    tags.add_tag([ids[i] for i in tagged], "keep")
    return ids


_POLICY = {"retention_days": 1, "retention_max_gb": 0.001,
           "retention_keep_tagged_with": ["keep"]}


@pytest.mark.parametrize("n", [6, 14])
def test_retention_tag_reads_do_not_scale_with_rows(sql_log, n):
    _seed_history(n, tagged=[0, n - 1])
    del sql_log[:]
    cands = retention.find_candidates("rs", _POLICY)
    assert len(cands) == n - 2
    tag_reads = _count(sql_log, "FROM history_tags")
    assert tag_reads == 1, f"M128: {tag_reads} history_tags reads for {n} rows"


def test_retention_tag_batch_chunks_and_keeps_tagged_rows(sql_log, monkeypatch):
    # chunk boundary: tagged rows land in the first, a middle and the last chunk
    monkeypatch.setattr(retention, "_TAG_LOOKUP_CHUNK", 4)
    ids = _seed_history(10, tagged=[0, 5, 9])
    del sql_log[:]
    cands = retention.find_candidates("rs", _POLICY)
    assert _count(sql_log, "FROM history_tags") == 3
    kept = {ids[0], ids[5], ids[9]}
    assert {c["id"] for c in cands} == set(ids) - kept
    by_reason = {}
    for c in cands:
        by_reason.setdefault(c["reason"].split()[0], set()).add(c["id"])
    assert by_reason == {"older": set(ids) - kept}


def test_retention_no_keep_tags_reads_no_tags(sql_log):
    _seed_history(4, tagged=[1])
    del sql_log[:]
    cands = retention.find_candidates("rs", dict(_POLICY, retention_keep_tagged_with=[]))
    assert len(cands) == 4
    assert _count(sql_log, "FROM history_tags") == 0


class _FailingTagRead:
    """Leased connection whose history_tags SELECT raises while armed."""

    def __init__(self, cx, state):
        self._cx = cx
        self._state = state

    def execute(self, sql, *args):
        if self._state["armed"] and "FROM history_tags" in sql:
            self._state["armed"] = False
            self._state["fired"] += 1
            raise sqlite3.OperationalError("c42-injected history_tags read failure")
        return self._cx.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._cx, name)


@pytest.fixture
def tag_read_fault(sql_log, monkeypatch):
    state = {"armed": False, "fired": 0}
    traced = db.db_conn

    @contextlib.contextmanager
    def faulty(*args, **kwargs):
        with traced(*args, **kwargs) as cx:
            yield _FailingTagRead(cx, state)

    monkeypatch.setattr(db, "db_conn", faulty)
    return state


def _seed_files(n, tagged=()):
    ids = _seed_history(n, tagged=list(tagged))
    for i in range(n):
        with open(f"f{i}.bin", "wb") as fh:
            fh.write(b"x")
    return ids


def _select_then(monkeypatch, after):
    real = retention.find_candidates

    def select(site_id, cfg):
        cands = real(site_id, cfg)
        after()
        return cands

    monkeypatch.setattr(retention, "find_candidates", select)


def test_retention_tag_added_after_selection_survives_delete(sql_log, monkeypatch):
    ids = _seed_files(3)
    _select_then(monkeypatch, lambda: tags.add_tag([ids[1]], "keep"))
    res = retention.apply_retention({"rs": _POLICY}, dry_run=False)
    survivors = sorted(p for p in ("f0.bin", "f1.bin", "f2.bin") if os.path.exists(p))
    assert survivors == ["f1.bin"], (
        f"M128 F1: row tagged 'keep' after selection was deleted; survivors={survivors}")
    assert res["total_deleted"] == 2
    audit = retention.audit_log(dry_run_only=False)
    assert sorted(a["history_id"] for a in audit) == [ids[0], ids[2]]


def test_retention_tag_write_waits_for_unlinks(sql_log, monkeypatch):
    ids = _seed_files(2)
    with db.db_conn() as cx:
        db_file = cx.execute("PRAGMA database_list").fetchone()[2]
    attempts = []
    real_unlink = pathlib.Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name.endswith(".bin"):
            other = sqlite3.connect(db_file, timeout=0)
            try:
                other.execute(
                    "INSERT INTO history_tags(history_id, tag, ts_assigned) VALUES(?,?,?)",
                    (ids[1], "keep", time.time()))
                other.commit()
                attempts.append("committed")
            except sqlite3.OperationalError as e:
                attempts.append(str(e))
            finally:
                other.close()
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    retention.apply_retention({"rs": _POLICY}, dry_run=False)
    assert len(attempts) == 2
    assert all("locked" in a for a in attempts), (
        f"M128 F1: a tag committed between the protect-tag re-check and the unlink: {attempts}")


def test_retention_tag_read_failure_selects_no_tagged_rows(tag_read_fault, monkeypatch):
    monkeypatch.setattr(retention, "_TAG_LOOKUP_CHUNK", 4)
    ids = _seed_history(10, tagged=[0, 5, 9])
    tag_read_fault["armed"] = True
    cands = retention.find_candidates("rs", _POLICY)
    assert tag_read_fault["fired"] == 1
    leaked = {c["id"] for c in cands} & {ids[0], ids[5], ids[9]}
    assert not leaked, f"M128 F2: tag read failed and tagged rows were selected: {leaked}"


def test_retention_recheck_failure_deletes_nothing(tag_read_fault, monkeypatch):
    _seed_files(3)
    _select_then(monkeypatch, lambda: tag_read_fault.update(armed=True))
    res = retention.apply_retention({"rs": _POLICY}, dry_run=False)
    survivors = [p for p in ("f0.bin", "f1.bin", "f2.bin") if os.path.exists(p)]
    assert len(survivors) == 3, (
        f"M128 F2: protect-tag re-check failed and files were deleted; survivors={survivors}")
    assert tag_read_fault["fired"] == 1
    assert res["total_deleted"] == 0
    assert any("c42-injected" in e for e in res["sites"]["rs"]["errors"])


# Tag lookup slots at every chunk edge for chunk=4 over 10 ids: first and last
# slot of each full chunk, so one past each boundary, and both ends of the
# short final chunk. A chunk slice that drops its first or last id skips some.
_EDGE_SLOTS = (0, 3, 4, 7, 8, 9)


def test_retention_find_candidates_keeps_tags_at_chunk_edges(sql_log, monkeypatch):
    monkeypatch.setattr(retention, "_TAG_LOOKUP_CHUNK", 4)
    # find_candidates looks ids up newest first; ts rises with i, so slot k is row 9-k
    ids = _seed_history(10, tagged=[9 - k for k in _EDGE_SLOTS])
    del sql_log[:]
    cands = retention.find_candidates("rs", _POLICY)
    assert _count(sql_log, "FROM history_tags") == 3
    leaked = {c["id"] for c in cands} & {ids[9 - k] for k in _EDGE_SLOTS}
    assert not leaked, f"M128 F3: tagged rows at a chunk edge were selected: {leaked}"
    assert {c["id"] for c in cands} == {ids[9 - k] for k in (1, 2, 5, 6)}


def test_retention_recheck_keeps_tags_at_chunk_edges(sql_log, monkeypatch):
    monkeypatch.setattr(retention, "_TAG_LOOKUP_CHUNK", 4)
    ids = _seed_files(10)
    # candidates come back oldest first, so the re-check looks up row k at slot k
    _select_then(monkeypatch, lambda: tags.add_tag([ids[k] for k in _EDGE_SLOTS], "keep"))
    res = retention.apply_retention({"rs": _POLICY}, dry_run=False)
    deleted = sorted(k for k in range(10) if not os.path.exists(f"f{k}.bin"))
    assert deleted == [1, 2, 5, 6], (
        f"M128 F3: re-check missed tags at a chunk edge; deleted rows={deleted}")
    assert res["total_deleted"] == 4
    audit = retention.audit_log(dry_run_only=False)
    assert sorted(a["history_id"] for a in audit) == [ids[k] for k in (1, 2, 5, 6)]


# -- M117 maintenance._ensure_tables -------------------------------------------

def test_maintenance_ddl_runs_once_per_database(sql_log):
    mw.list_windows()
    first = _count(sql_log, "CREATE TABLE")
    assert first == 2
    del sql_log[:]
    for _ in range(3):
        mw.list_windows()
        mw.is_action_paused("workers")
    ddl = _count(sql_log, "CREATE TABLE")
    assert ddl == 0, f"M117: {ddl} CREATE TABLE statements after the schema was ready"


def test_maintenance_new_database_still_gets_tables(sql_log, tmp_path, monkeypatch):
    mw.list_windows()
    other = tmp_path / "second"
    other.mkdir()
    monkeypatch.chdir(other)
    monkeypatch.setenv("BD_INSTALL_DIR", str(other))
    db.db_init()
    del sql_log[:]
    wid = mw.add_window(label="x", start_iso="2030-01-01T00:00:00",
                        end_iso="2030-01-01T01:00:00")
    assert isinstance(wid, int)
    assert _count(sql_log, "CREATE TABLE") == 2


# -- M120 mass_import._ensure_table --------------------------------------------

def test_mass_import_schema_failure_is_reported(clean_workdir, monkeypatch, capsys):
    monkeypatch.setattr(mass_import, "_TABLE_READY", False)

    def broken(*args, **kwargs):
        raise RuntimeError("c42-schema-boom")

    monkeypatch.setattr(mass_import._db, "db_conn", broken)
    mass_import._ensure_table()
    assert mass_import._TABLE_READY is False
    err = capsys.readouterr().err
    assert "[mass_import] schema" in err and "c42-schema-boom" in err, (
        f"M120: schema failure was swallowed silently; stderr={err!r}")


def test_mass_import_ready_after_success(sql_log, monkeypatch):
    monkeypatch.setattr(mass_import, "_TABLE_READY", False)
    mass_import._ensure_table()
    assert mass_import._TABLE_READY is True
    del sql_log[:]
    mass_import._ensure_table()
    assert _count(sql_log, "CREATE TABLE") == 0
    assert _count(sql_log, "UPDATE mass_imports") == 0
