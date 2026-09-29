"""Row 127 HIGH (MOD3-CUTOVER-DUPLICATE-RUN-ROWS-correctness-A6-A, test2 2026-09-28):
with MOD3_SHADOW_READ=1 the dual-write seam re-executed EVERY statement on a
table outside the mirror -- INSERT and UPDATE included -- "for the shadow
comparison". record_run_start() wrote two job_runs rows (the second with no
events) and both run_events rows landed on the first id.

shadow_compare() only compares a SELECT, so only a SELECT may be re-executed.
"""
from __future__ import annotations

import importlib

import pytest

BD_GATE_SCOPE = "module"
UNREACHABLE = "postgresql://nobody@127.0.0.1:1/none"


@pytest.fixture(autouse=True)
def _isolated_history_db(tmp_path, monkeypatch):
    monkeypatch.setenv("BD_INSTALL_DIR", str(tmp_path))


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setenv("MOD3_PG_DSN", UNREACHABLE)
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    monkeypatch.delenv("MOD3_CUTOVER", raising=False)
    from bulk_downloader import db, pg_backend, run_history
    importlib.reload(pg_backend)
    importlib.reload(db)
    importlib.reload(run_history)
    assert pg_backend.shadow_read_enabled() is True
    db.db_init()
    run_history.init()
    return db, pg_backend, run_history


def test_the_row_one_run_start_writes_one_run_and_one_start_event(monkeypatch, tmp_path):
    db, _, rh = _reload(monkeypatch, tmp_path)
    rid = rh.record_run_start("site-a", "https://x.test/v/1")
    with db.db_conn() as cx:
        runs = cx.execute("SELECT id FROM job_runs WHERE site_id='site-a'").fetchall()
        starts = cx.execute("SELECT run_id FROM run_events WHERE event_type='start'").fetchall()
    assert [r[0] for r in runs] == [rid], (
        f"shadow-read re-executed the job_runs INSERT: runs={[r[0] for r in runs]}")
    assert [r[0] for r in starts] == [rid], f"start events={[r[0] for r in starts]}"


def test_a_write_through_a_cursor_is_not_rerun_either(monkeypatch, tmp_path):
    db, _, rh = _reload(monkeypatch, tmp_path)
    with db.db_conn() as cx:
        cx.cursor().execute("INSERT INTO job_runs(site_id, url, status) VALUES(?,?,?)",
                            ("site-b", "u", "running"))
        n = cx.execute("SELECT COUNT(*) FROM job_runs WHERE site_id='site-b'").fetchone()[0]
    assert n == 1, f"cursor path re-executed the INSERT: {n} rows"


def test_a_select_is_still_shadow_compared(monkeypatch, tmp_path):
    """Positive control: the guard must not blind the shadow for reads."""
    db, pg, _ = _reload(monkeypatch, tmp_path)
    monkeypatch.setattr(pg, "_shadow_fetch", lambda sql, params=(): ([(0,)], False))
    before = pg.shadow_stats()["compared"]
    with db.db_conn() as cx:
        cx.execute("SELECT COUNT(*) FROM history").fetchone()
        cx.cursor().execute("SELECT COUNT(*) FROM history").fetchone()
    assert pg.shadow_stats()["compared"] == before + 2, pg.shadow_stats()
