"""Rows 547, 560/561 and 563: completion needs current identity + a counter.

``history.library_id`` is historical: several rows may point at one UNIQUE
``library.file_path``.  ``library.history_id`` is current: it names the row
whose URL most recently wrote the bytes at that path.  A skip may trust only
that current identity, and only when its byte counter was actually recorded.
Zero is recorded, not unknown: HTTP 416 promotes an already-complete ``.part``
while fetching exactly zero bytes on the completing call.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bulk_downloader import db, migrations


BD_GATE_SCOPE = "module"

_SITE_ID = "row547-site"
_URL_A = "https://example.test/scenes/a"
_URL_B = "https://example.test/scenes/b"


@pytest.fixture
def history_db(monkeypatch, tmp_path):
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.db_init()
    migrated = migrations.apply_pending(backup_first=False)
    assert migrated["errors"] == 0, migrated
    assert db_path.is_file(), "precondition: the isolated history DB was created"
    return tmp_path


def _log_done(url: str, path: Path, *, bytes_fetched, transfer_mode="http"):
    db.db_log(
        _SITE_ID,
        "Row 547 Site",
        url,
        "done",
        path.name,
        path.stat().st_size,
        "",
        bytes_fetched=bytes_fetched,
        transfer_mode=transfer_mode,
        file_path=str(path),
        title="Scene A" if url == _URL_A else "Scene B",
        title_source="page",
    )


def _rows():
    with db.db_conn() as cx:
        history = [dict(row) for row in cx.execute(
            "SELECT id,url,status,file_size,bytes_fetched,transfer_mode,"
            "library_id,ts FROM history ORDER BY id"
        ).fetchall()]
        library = [dict(row) for row in cx.execute(
            "SELECT id,history_id,file_path,title FROM library ORDER BY id"
        ).fetchall()]
    return history, library


def test_row547_a_reused_library_row_does_not_give_the_old_url_new_bytes(
    history_db,
):
    shared = history_db / "same-name.mp4"
    a_bytes = b"scene A original bytes"
    b_bytes = b"scene B replacement bytes are different"

    shared.write_bytes(a_bytes)
    _log_done(_URL_A, shared, bytes_fetched=len(a_bytes))
    history, library = _rows()
    assert len(history) == 1 and len(library) == 1
    assert history[0]["url"] == _URL_A
    assert history[0]["bytes_fetched"] == len(a_bytes)
    assert history[0]["library_id"] == library[0]["id"]
    assert library[0]["history_id"] == history[0]["id"]
    assert shared.read_bytes() == a_bytes

    # Preconditions for 547: A's file vanished, B then landed at the SAME
    # path, and UNIQUE library.file_path caused B to reuse A's library row.
    shared.unlink()
    assert not shared.exists()
    shared.write_bytes(b_bytes)
    _log_done(_URL_B, shared, bytes_fetched=len(b_bytes))
    history, library = _rows()
    assert len(history) == 2 and len(library) == 1
    a_row, b_row = history
    assert a_row["url"] == _URL_A and b_row["url"] == _URL_B
    assert a_row["library_id"] == b_row["library_id"] == library[0]["id"]
    assert library[0]["history_id"] == b_row["id"], (
        "precondition: the library currently attributes the reused path to B"
    )
    assert shared.read_bytes() == b_bytes
    assert a_bytes != b_bytes

    verdicts = [db.db_skip_identity(_URL_A, shared)]
    assert len(verdicts) == 1, "the identity branch must fire exactly once"
    assert verdicts == [("different", None)], (
        "row 547: URL A was returned SAME over URL B's replacement bytes"
    )


class _Cookies:
    def cookies(self):
        return []


class _Response416:
    status_code = 416
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _RateSlot:
    def __init__(self, fired):
        self._fired = fired

    def release(self):
        self._fired["slot_releases"] += 1


def test_rows560_561_a_416_completed_zero_byte_row_is_current_ownership(
    history_db, monkeypatch,
):
    from bulk_downloader import rate_limit, runner_transport, staging_claim

    final_path = history_db / "resume-complete.mp4"
    payload = b"already complete partial bytes"
    staging = staging_claim.claim(
        final_path, staging_claim.job_identity(_URL_A)
    )
    staging.write_bytes(payload)
    assert staging.is_file() and staging.read_bytes() == payload
    assert not final_path.exists()

    fired = {"streams": 0, "slot_releases": 0, "accumulator_finishes": 0}

    def _stream(*args, **kwargs):
        fired["streams"] += 1
        return _Response416()

    def _acquire(_url):
        return _RateSlot(fired)

    runner = runner_transport.TransportMixin()
    runner.site_id = _SITE_ID
    runner.config = {
        "parallel_chunks": 1,
        "use_curl_cffi": False,
        "use_ramdisk_stage": False,
    }
    runner._pick_fastest_mirror = lambda url: url
    runner._recommended_chunk_bytes = lambda: 64 * 1024
    runner._current_cap_mbps = lambda: 0
    runner._download_proxy_url = lambda: None
    runner._start_daily_byte_accumulator = lambda: object()

    def _finish(_accumulator):
        fired["accumulator_finishes"] += 1

    runner._finish_daily_byte_accumulator = _finish
    monkeypatch.setattr(runner_transport.httpx, "stream", _stream)
    monkeypatch.setattr(rate_limit, "acquire", _acquire)

    size_on_disk, bytes_fetched = runner._http_download(
        _URL_A,
        None,
        _Cookies(),
        "https://cdn.example.test/resume-complete.mp4",
        final_path,
    )

    # Preconditions for 560/561: the real 416 arm fired exactly once, promoted
    # the complete partial, and truthfully returned a zero transfer count.
    assert fired == {
        "streams": 1,
        "slot_releases": 1,
        "accumulator_finishes": 1,
    }
    assert (size_on_disk, bytes_fetched) == (len(payload), 0)
    assert final_path.is_file() and final_path.read_bytes() == payload
    assert not staging.exists()

    _log_done(
        _URL_A,
        final_path,
        bytes_fetched=bytes_fetched,
        transfer_mode="http",
    )
    history, library = _rows()
    assert len(history) == 1 and len(library) == 1
    assert history[0]["status"] == "done"
    assert history[0]["file_size"] == len(payload)
    assert history[0]["bytes_fetched"] == 0
    assert history[0]["transfer_mode"] == "http"
    assert history[0]["library_id"] == library[0]["id"]
    assert library[0]["history_id"] == history[0]["id"]

    verdicts = [db.db_skip_identity(_URL_A, final_path)]
    assert len(verdicts) == 1, "the identity branch must fire exactly once"
    assert verdicts == [("same", str(final_path))], (
        "rows 560/561: a completed HTTP 416 row was misclassified unproven"
    )


def test_row563_pruning_old_transfer_proof_keeps_the_current_repeated_skip(
    history_db,
):
    final_path = history_db / "healthy-repeat.mp4"
    payload = b"one real transfer followed by measured skips"
    final_path.write_bytes(payload)
    _log_done(_URL_A, final_path, bytes_fetched=len(payload))
    _log_done(_URL_A, final_path, bytes_fetched=0, transfer_mode=None)
    _log_done(_URL_A, final_path, bytes_fetched=0, transfer_mode=None)

    with db.db_conn() as cx:
        cx.execute(
            "UPDATE history SET ts='2000-01-01T00:00:00' WHERE id=("
            "SELECT MIN(id) FROM history)"
        )

    history, library = _rows()
    assert len(history) == 3 and len(library) == 1
    assert [row["bytes_fetched"] for row in history] == [len(payload), 0, 0]
    assert library[0]["history_id"] == history[-1]["id"]
    assert final_path.read_bytes() == payload
    assert db.db_skip_identity(_URL_A, final_path) == ("same", str(final_path)), (
        "precondition: the repeated skip is healthy before pruning"
    )

    pruned = db.db_prune(1)
    history, library = _rows()
    assert pruned == 1, "the prune branch must remove exactly the old proof row"
    assert len(history) == 2 and len(library) == 1
    assert [row["bytes_fetched"] for row in history] == [0, 0]
    assert library[0]["history_id"] == history[-1]["id"]
    assert final_path.is_file() and final_path.read_bytes() == payload

    verdicts = [db.db_skip_identity(_URL_A, final_path)]
    assert len(verdicts) == 1, "the post-prune identity branch must fire once"
    assert verdicts == [("same", str(final_path))], (
        "row 563: pruning turned a healthy repeated skip into a failure"
    )


# Negative controls ---------------------------------------------------------


def test_a_stale_zero_counter_without_current_identity_remains_unproven(
    history_db,
):
    """A recorded zero is valid only when the library currently names its row."""
    final_path = history_db / "stale-zero.mp4"
    payload = b"bytes currently owned by A"
    final_path.write_bytes(payload)
    _log_done(_URL_A, final_path, bytes_fetched=len(payload))
    history, library = _rows()
    assert len(history) == 1 and len(library) == 1

    with db.db_conn() as cx:
        cx.execute(
            "INSERT INTO history(site_id,site_name,url,status,filename,file_size,"
            "message,bytes_fetched,library_id,ts) "
            "VALUES(?,?,?,?,?,?,?,?,?,datetime('now'))",
            (_SITE_ID, "Row 547 Site", _URL_B, "done", final_path.name,
             len(payload), "already on disk", 0, library[0]["id"]),
        )

    history, library = _rows()
    assert len(history) == 2 and len(library) == 1
    assert history[-1]["url"] == _URL_B
    assert history[-1]["bytes_fetched"] == 0
    assert history[-1]["library_id"] == library[0]["id"]
    assert library[0]["history_id"] == history[0]["id"], (
        "precondition: B's zero row is historical, while A is current"
    )
    assert final_path.read_bytes() == payload

    verdicts = [db.db_skip_identity(_URL_B, final_path)]
    assert len(verdicts) == 1
    assert verdicts == [("unproven", str(final_path))], (
        "the zero-counter exception was widened to a row with stale identity"
    )


def test_a_current_null_counter_remains_unproven(history_db):
    """NULL is UNKNOWN, not another spelling of the legitimate zero count."""
    final_path = history_db / "unknown-counter.mp4"
    payload = b"a file whose transfer counter was not recorded"
    final_path.write_bytes(payload)
    _log_done(_URL_A, final_path, bytes_fetched=None)

    history, library = _rows()
    assert len(history) == 1 and len(library) == 1
    assert history[0]["bytes_fetched"] is None
    assert history[0]["library_id"] == library[0]["id"]
    assert library[0]["history_id"] == history[0]["id"]
    assert final_path.read_bytes() == payload

    verdicts = [db.db_skip_identity(_URL_A, final_path)]
    assert len(verdicts) == 1
    assert verdicts == [("unproven", str(final_path))], (
        "the counter guard was widened to accept an unmeasured NULL"
    )


def test_pruning_the_current_identity_does_not_leave_bare_library_proof(
    history_db,
):
    final_path = history_db / "dangling-library.mp4"
    payload = b"bytes with no surviving history identity"
    final_path.write_bytes(payload)
    _log_done(_URL_A, final_path, bytes_fetched=len(payload))
    with db.db_conn() as cx:
        cx.execute("UPDATE history SET ts='2000-01-01T00:00:00'")

    history, library = _rows()
    assert len(history) == 1 and len(library) == 1
    removed_history_id = history[0]["id"]
    assert library[0]["history_id"] == removed_history_id

    assert db.db_prune(1) == 1
    history, library = _rows()
    assert history == []
    assert len(library) == 1
    assert library[0]["history_id"] == removed_history_id
    assert final_path.is_file() and final_path.read_bytes() == payload

    verdicts = [db.db_skip_identity(_URL_A, final_path)]
    assert len(verdicts) == 1
    assert verdicts == [("unknown", None)], (
        "a bare library row was accepted after its current identity was pruned"
    )
