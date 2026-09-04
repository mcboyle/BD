from __future__ import annotations

import threading
from unittest import mock

import pytest

from bulk_downloader import db
from bulk_downloader import runner as runner_module
from bulk_downloader.runner import SiteRunner


BD_GATE_SCOPE = "repo-wide"

_SITE_ID = "row664-history"
_SITE_NAME = "Row 664 History"
_URL = "https://example.test/videos/already-downloaded"
_DUPLICATE_REASON = "Duplicate of history row 17"
_RUN_GENERATION = 23


@pytest.fixture
def isolated_history_db(monkeypatch: pytest.MonkeyPatch, tmp_path):
    db_path = tmp_path / "row664.sqlite3"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.db_init()
    with db.db_conn() as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {"history", "queue"} <= tables
    assert db.db_search(site_id=_SITE_ID) == []
    return db_path


class _DedupRefusalRunner:
    site_id = _SITE_ID
    config = {"name": _SITE_NAME}
    _WORKER_CLAIM_PROCESSED = SiteRunner._WORKER_CLAIM_PROCESSED

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._worker_heartbeats_lock = threading.Lock()
        self._worker_url_generations = {0: _RUN_GENERATION}
        self._worker_current_urls = {0: _URL}
        self.jobs = {_URL: {"status": "pending", "message": ""}}
        self.claims: list[tuple[int, str, int | None]] = []
        self.preflight_jobs: list[dict[str, str]] = []
        self.process_calls: list[tuple[object, str, object]] = []
        self.updates: list[tuple[str, str, str]] = []

    def _claim_worker_item(
        self, worker_idx: int, url: str, run_generation: int | None = None
    ) -> tuple[str, int]:
        self.claims.append((worker_idx, url, run_generation))
        return "claimed", _RUN_GENERATION

    def _update_job(self, url: str, status: str, message: str, **_kwargs) -> None:
        self.updates.append((url, status, message))
        self.jobs[url].update(status=status, message=message)

    def _process_one(self, browser, url: str, persistent_ctx=None):
        self.process_calls.append((browser, url, persistent_ctx))
        return SiteRunner._process_one(self, browser, url, persistent_ctx)

    def _dedup_preflight(self, url: str, job: dict[str, str]) -> str:
        assert url == _URL
        self.preflight_jobs.append(dict(job))
        return _DUPLICATE_REASON


def _assert_single_dedup_history_row() -> dict:
    rows = db.db_search(site_id=_SITE_ID, status="skipped_duplicate")
    assert len(rows) == 1, (
        "dedup refusal reached the worker but expected exactly 1 history row; "
        f"got {len(rows)}: {rows!r}"
    )
    return rows[0]


def _run_dedup_refusal(subject: _DedupRefusalRunner):
    browser = object()
    persistent_ctx = object()
    result = SiteRunner._process_worker_url(
        subject,
        0,
        browser,
        _URL,
        persistent_ctx=persistent_ctx,
        run_generation=_RUN_GENERATION,
    )
    assert result == SiteRunner._WORKER_CLAIM_PROCESSED
    assert subject.claims == [(0, _URL, _RUN_GENERATION)]
    assert subject.process_calls == [(browser, _URL, persistent_ctx)]
    assert subject.preflight_jobs == [
        {"status": "running", "message": "Claimed by worker"}
    ]
    assert subject.updates == [
        (_URL, "running", "Claimed by worker"),
        (_URL, "skipped_duplicate", _DUPLICATE_REASON),
    ]
    assert subject.jobs[_URL] == {
        "status": "skipped_duplicate",
        "message": _DUPLICATE_REASON,
    }


def test_dedup_refusal_writes_one_history_row_with_reason(
    isolated_history_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _DedupRefusalRunner()
    history_write = mock.Mock(wraps=db.db_log)
    monkeypatch.setattr(runner_module, "db_log", history_write)

    _run_dedup_refusal(subject)

    row = _assert_single_dedup_history_row()
    assert history_write.call_count == 1
    assert row["site_name"] == _SITE_NAME
    assert row["url"] == _URL
    assert row["message"] == _DUPLICATE_REASON


def test_missing_history_write_fails_the_single_row_verdict(
    isolated_history_db,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = _DedupRefusalRunner()
    suppressed_history_write = mock.Mock()
    monkeypatch.setattr(runner_module, "db_log", suppressed_history_write)

    _run_dedup_refusal(subject)

    assert suppressed_history_write.call_count == 1
    assert db.db_search(site_id=_SITE_ID, status="skipped_duplicate") == []
    with pytest.raises(
        AssertionError,
        match=r"expected exactly 1 history row; got 0",
    ):
        _assert_single_dedup_history_row()


def test_runner_import_transform_control() -> None:
    assert runner_module.SiteRunner is SiteRunner
