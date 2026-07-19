"""Cut 4 — /api/runs?status=failed filter + job_runs.reason_code.

`/api/runs` gains an optional `?status=` filter (no new route), and `job_runs`
gains an additive `reason_code` column that `record_run_finish` persists and the
read side surfaces. The failed-run grouping in the SPA JobErrorModal reads
`/api/runs?status=failed`.

Tested by seeding rows directly through run_history (the runner's live
population is LIVE-only). RED on pristine 373: list_runs has no `status` arg /
no `reason_code`, and the route ignores `?status=`.
"""


def test_record_finish_accepts_reason_code_and_list_filters_status():
    from bulk_downloader import run_history as rh, db
    db.db_init()
    rh.init()
    ok_id = rh.record_run_start("siteA", "http://a/ok")
    rh.record_run_finish(ok_id, "done")
    fail_id = rh.record_run_start("siteB", "http://b/fail")
    rh.record_run_finish(fail_id, "failed", reason_code="auth")

    # unfiltered list returns reason_code on every row
    allruns = rh.list_runs(limit=50)
    by_id = {r["id"]: r for r in allruns}
    assert "reason_code" in by_id[fail_id]
    assert by_id[fail_id]["reason_code"] == "auth"
    assert by_id[ok_id]["reason_code"] in (None, "")

    # status filter narrows to failed only
    failed = rh.list_runs(limit=50, status="failed")
    ids = {r["id"] for r in failed}
    assert fail_id in ids
    assert ok_id not in ids


def test_api_runs_status_param_filters():
    from bulk_downloader import app as A, run_history as rh, db
    db.db_init()
    rh.init()
    fid = rh.record_run_start("siteC", "http://c/fail")
    rh.record_run_finish(fid, "failed", reason_code="rate_limited")
    did = rh.record_run_start("siteC", "http://c/done")
    rh.record_run_finish(did, "done")

    c = A.app.test_client()
    failed = c.get("/api/runs?status=failed").get_json()["runs"]
    fids = {r["id"] for r in failed}
    assert fid in fids
    assert did not in fids
    row = next(r for r in failed if r["id"] == fid)
    assert row["reason_code"] == "rate_limited"
