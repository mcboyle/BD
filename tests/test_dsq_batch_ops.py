"""dsq-batch-ops (deep-spa-sites-queue-history FINDINGS F1, F4, F6): /api/batch/* answers.

F1  POST /api/batch/retry (live) rewrote history rows and re-queued nothing; it took any status word.
F4  a filter the server cannot read answered 200 "matched 0", exactly like "nothing matched".
F6  a non-object body answered 500 with the raw exception text; dry_run "false" (a string) ran as a dry run.
Real Flask test client on the isolated BD_HOME the conftest autouse fixture provides, a scratch history DB
by absolute DB_PATH, a real SiteRunner made by POST /api/sites; no network.
"""

import sys

import pytest

from tests.frontend_vitest import run_vitest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

FAILED_URL = "https://example.invalid/dsq/failed-1"


@pytest.fixture(scope="module", autouse=True)
def _restore_bd_modules_after_file():
    """Put the bulk_downloader module table back when this file finishes (1034 leaker census)."""
    saved = {
        m: mod
        for m, mod in sys.modules.items()
        if m == "bulk_downloader" or m.startswith("bulk_downloader.")
    }
    try:
        yield
    finally:
        for m in [
            m
            for m in sys.modules
            if m == "bulk_downloader" or m.startswith("bulk_downloader.")
        ]:
            del sys.modules[m]
        sys.modules.update(saved)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a
    from bulk_downloader import db as _db

    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "history.db"))
    _db.db_init()
    for i in range(3):
        _db.db_log(
            "site-x",
            "Site X",
            f"https://example.invalid/dsq/done-{i}",
            "done",
            f"d{i}.mp4",
            1024,
        )
    for i in range(2):
        _db.db_log(
            "site-x", "Site X", f"https://example.invalid/dsq/fail-{i}", "failed", "", 0
        )
    return a.app.test_client(), a, _db


def _statuses(_db):
    with _db.db_conn() as cx:
        return sorted(r[0] for r in cx.execute("SELECT status FROM history").fetchall())


# ---- F1 -------------------------------------------------------------------------------------------------------------
def test_f1_live_retry_refuses_a_status_word_it_does_not_know(env):
    c, _, _db = env
    before = _statuses(_db)
    r = c.post(
        "/api/batch/retry",
        json={
            "filter": {"status": "failed"},
            "dry_run": False,
            "reset_to_status": "banana",
        },
    )
    assert r.status_code == 400, (r.status_code, r.get_json())
    assert "reset_to_status" in (r.get_json() or {}).get("error", ""), r.get_json()
    assert _statuses(_db) == before, "a refused retry still rewrote history"


def test_f1_control_known_status_words_are_accepted(env):
    c, _, _ = env
    for word in ("pending", "needs_review"):
        r = c.post(
            "/api/batch/retry",
            json={
                "filter": {"status": "failed"},
                "dry_run": True,
                "reset_to_status": word,
            },
        )
        assert r.status_code == 200 and r.get_json()["candidates_matched"] == 2, (
            word,
            r.get_json(),
        )


def test_f1_live_retry_requeues_the_job_in_its_runner(env):
    c, a, _db = env
    sid = c.post("/api/sites", json={"name": "dsq-retry"}).get_json()["id"]
    runner = a.runners[sid]
    with runner._lock:
        runner.jobs[FAILED_URL] = {
            "status": "failed",
            "message": "boom",
            "ts": "now",
            "ord": 0,
            "priority": "",
            "retries": 3,
            "retry_after": 99,
        }
    _db.db_log(sid, "dsq-retry", FAILED_URL, "failed", "", 0, message="boom")
    r = c.post(
        "/api/batch/retry",
        json={"filter": {"site_id": sid, "status": "failed"}, "dry_run": False},
    )
    body = r.get_json()
    assert r.status_code == 200 and body["processed"] == 1, body
    assert runner.jobs[FAILED_URL]["status"] == "pending", (
        f"batch retry reset history but the runner still holds {runner.jobs[FAILED_URL]['status']!r}: nothing re-queued"
    )
    assert runner.jobs[FAILED_URL]["retries"] == 0
    assert body.get("requeued") == 1 and body.get("not_requeued") == 0, body


def test_f1_live_retry_names_rows_it_could_not_requeue(env):
    c, _, _db = env
    r = c.post(
        "/api/batch/retry",
        json={"filter": {"site_id": "site-x", "status": "failed"}, "dry_run": False},
    )
    body = r.get_json()
    assert r.status_code == 200 and body["processed"] == 2, body
    assert body.get("requeued") == 0 and body.get("not_requeued") == 2, body
    assert body.get("not_requeued_reasons", {}).get("no_runner") == 2, body


def test_f1_live_retry_refuses_and_lists_rows_that_are_not_retryable(env):
    """PM option B: a done row reset to pending is a row nothing will ever run."""
    c, _, _db = env
    r = c.post(
        "/api/batch/retry", json={"filter": {"site_id": "site-x"}, "dry_run": False}
    )
    body = r.get_json()
    assert r.status_code == 200, body
    assert (body["candidates_matched"], body.get("retryable"), body.get("refused")) == (
        5,
        2,
        3,
    ), body
    assert sorted(x["status"] for x in body.get("refused_rows", [])) == ["done"] * 3, (
        body
    )
    assert body["processed"] == 2, body
    assert _statuses(_db) == ["done", "done", "done", "pending", "pending"], _statuses(
        _db
    )


def test_f1_retryable_statuses_match_the_runner():
    from bulk_downloader import batch_ops
    from bulk_downloader.runner_queue import BULK_RETRY_STATUSES

    assert set(batch_ops.RETRYABLE_STATUSES) == set(BULK_RETRY_STATUSES)


def test_r3_batchops_reset_statuses_match_the_server():
    """r3 (T281c eject): BatchOps.tsx types reset_to_status as the server's RETRY_STATUSES, so the
    body-contract probe sends a word /api/batch/retry accepts. The two lists must not drift."""
    import re
    from pathlib import Path

    from bulk_downloader import batch_ops

    root = Path(__file__).resolve().parents[1]
    src = (root / "frontend/src/routes/BatchOps.tsx").read_text(encoding="utf-8")
    decl = re.findall(r"^const RETRY_STATUSES = \[(.*)\] as const;$", src, re.MULTILINE)
    assert len(decl) == 1, f"BatchOps.tsx RETRY_STATUSES declarations: {decl}"
    assert re.findall(r'"([^"]*)"', decl[0]) == list(batch_ops.RETRY_STATUSES)


# ---- F4 (server side) -----------------------------------------------------------------------------------------------
BAD_FILTERS = [
    ({"older_than_days": "abc"}, "older_than_days"),
    ({"older_than_days": True}, "older_than_days"),
    ({"limit": -5}, "limit"),
    ({"limit": "10"}, "limit"),
    ({"bogus": 1}, "bogus"),
    ([1], "filter"),
    ({"status": ["failed"]}, "status"),
    ({"id_in": "1,2"}, "id_in"),
    ({"id_in": [1, "x"]}, "id_in"),
]


@pytest.mark.parametrize(
    "route", ["/api/batch/retry", "/api/batch/delete", "/api/batch/move"]
)
@pytest.mark.parametrize("flt,field", BAD_FILTERS, ids=[f for _, f in BAD_FILTERS])
def test_f4_unreadable_filter_is_a_400_not_matched_0(env, tmp_path, route, flt, field):
    c, _, _db = env
    body = {"filter": flt, "dry_run": True}
    if route.endswith("move"):
        body["target_dir"] = str(tmp_path / "moved")
    r = c.post(route, json=body)
    assert r.status_code == 400, (route, flt, r.status_code, r.get_json())
    assert field in (r.get_json() or {}).get("error", ""), (route, flt, r.get_json())


def test_f4_control_readable_filters_still_match(env):
    c, _, _ = env
    for flt, want in (
        ({}, 5),
        ({"status": "failed"}, 2),
        ({"older_than_days": 3650}, 0),
        ({"limit": 3}, 3),
        ({"id_in": []}, 0),
        ({"table": "history"}, 5),
    ):
        r = c.post("/api/batch/delete", json={"filter": flt, "dry_run": True})
        assert r.status_code == 200 and r.get_json()["candidates_matched"] == want, (
            flt,
            r.get_json(),
        )


# ---- F6 -------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "route",
    [
        "/api/batch/retry",
        "/api/batch/delete",
        "/api/batch/move",
        "/api/batch/dedup_scan",
    ],
)
@pytest.mark.parametrize("body", [[1], []], ids=["list", "empty-list"])
def test_f6_non_object_body_is_a_400(env, route, body):
    """r2 (cx219 R2): [] is a non-object body too; `request.json or {}` turned it into {}."""
    c, _, _db = env
    before = _statuses(_db)
    r = c.post(route, json=body)
    assert r.status_code == 400, (route, r.status_code, r.get_json())
    assert (r.get_json() or {}).get("error") == "request body must be a JSON object", (
        r.get_json()
    )
    assert _statuses(_db) == before


@pytest.mark.parametrize("route", ["/api/batch/retry", "/api/batch/delete"])
@pytest.mark.parametrize("val", ["false", "true", 0, 1, None])
def test_f6_dry_run_must_be_a_boolean(env, route, val):
    c, _, _db = env
    before = _statuses(_db)
    r = c.post(route, json={"filter": {"status": "failed"}, "dry_run": val})
    assert r.status_code == 400, (route, val, r.status_code, r.get_json())
    assert "dry_run" in (r.get_json() or {}).get("error", ""), r.get_json()
    assert _statuses(_db) == before


def test_f6_control_boolean_dry_run_and_absent_dry_run_preview(env):
    c, _, _db = env
    before = _statuses(_db)
    for body in (
        {"filter": {"status": "failed"}, "dry_run": True},
        {"filter": {"status": "failed"}},
    ):
        r = c.post("/api/batch/delete", json=body)
        assert r.status_code == 200 and r.get_json()["dry_run"] is True, (
            body,
            r.get_json(),
        )
    assert _statuses(_db) == before
    r = c.post("/api/batch/move", json=[1])
    assert r.status_code == 400  # the sibling that already answered 400


@pytest.mark.parametrize("body", [[1], []], ids=["list", "empty-list"])
def test_f6_saved_search_patch_with_a_list_body_is_a_400(env, body):
    c, _, _ = env
    r = c.patch("/api/saved_searches/1", json=body)
    assert r.status_code == 400, (r.status_code, r.get_json())


def test_r2_control_absent_and_null_body_still_mean_defaults(env):
    """r2 CONTROL: only a non-object body is refused; a JSON null is still "no body" (a dry run)."""
    c, _, _db = env
    before = _statuses(_db)
    r = c.post("/api/batch/delete", data="null", content_type="application/json")
    assert r.status_code == 200 and r.get_json()["dry_run"] is True, (
        r.status_code,
        r.get_json(),
    )
    assert _statuses(_db) == before


# ---- F4 (SPA side) --------------------------------------------------------------------------------------------------
def test_f4_batchops_executes_only_the_previewed_filter_vitest():
    """Execute delete / retry act only on the previewed filter; "1e2" is refused, never sent as 1."""
    receipt = run_vitest("src/routes/BatchOps.dsqF4.test.tsx", expected_tests=8)
    assert receipt["files_passed"] == receipt["files_collected"] == 1
    assert receipt["tests_passed"] == receipt["tests_collected"] == 8
