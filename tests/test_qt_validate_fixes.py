"""qt-validate (O1789 bughunt queue-templates-io, finder bd-worker-S34-D): queue template bodies are type-checked.

M1  apply mode=replace with a stored list priority_map: 500 AFTER the queue was cleared -> 400 before any change.
M5  PUT urls "https://x" stored one URL per character; urls 123 -> 500. Both -> 400, template unchanged.
L2  create with origin_site_id {"a":1}: sqlite bind error 500 -> 400.
L4  an empty template listed with url_count 1 -> 0.
L5  PUT {} / null answered 200 {"ok": false} -> 400; a PUT to a missing id -> 404.
Each defect sits beside a legit control that must still succeed. Views are called the way
test_hunt_queue_template_apply.py calls them (request context, stub runner); an exception is reported as a 500.
"""

BD_GATE_SCOPE = "module"

from threading import RLock

import pytest

from bulk_downloader import app as app_module
from bulk_downloader import app_queue_templates, db, queue_templates

SID = "site"
OLD = [
    "https://example.test/old1",
    "https://example.test/old2",
    "https://example.test/old3",
]
NEW = "https://example.test/n1"


class _Runner:
    def __init__(self, sid):
        self.sid = sid
        self._lock = RLock()
        self.jobs = {}
        self.urls = []

    def load_urls(self, urls, folder_scan=False):
        for url in urls:
            self.jobs[url] = {"url": url, "priority": "", "force_download": False}
            self.urls.append(url)
            db.queue_upsert(self.sid, url, status="pending")
        return len(urls), 0


def _call(view, *args, method="POST", path="/api/queue_templates", **kw):
    """(status, json) of a view; an exception escaping the view is the 500 Flask would answer."""
    with app_module.app.test_request_context(path, method=method, **kw):
        try:
            rv = view(*args)
        except Exception as exc:
            return 500, {"exception": repr(exc)}
    resp, status = rv if isinstance(rv, tuple) else (rv, rv.status_code)
    return status, resp.get_json()


def _world(monkeypatch):
    db.db_init()
    runner = _Runner(SID)
    runner.load_urls(OLD)
    monkeypatch.setattr(app_queue_templates, "_app_runners", lambda: {SID: runner})
    return runner


def _queued():
    return sorted(row["url"] for row in db.queue_load(SID))


def test_m1_replace_with_a_bad_stored_template_changes_nothing(
    clean_workdir, monkeypatch
):
    runner = _world(monkeypatch)
    tid = queue_templates.create(
        "T034 badmap", SID, [NEW], priority_map=[NEW]
    )  # stored before validation existed
    status, body = _call(
        app_queue_templates.api_queue_template_apply,
        tid,
        SID,
        path=f"/api/queue_templates/{tid}/apply/{SID}?mode=replace",
    )
    assert status == 400, f"QT_M1_REPLACE_NOT_ATOMIC status={status} body={body}"
    assert _queued() == sorted(OLD) and sorted(runner.jobs) == sorted(OLD), (
        f"QT_M1_QUEUE_CHANGED rows={_queued()} jobs={sorted(runner.jobs)}"
    )
    assert queue_templates.get(tid)["use_count"] == 0


@pytest.mark.parametrize(
    "force_set", [[[]], [{"a": 1}]], ids=["list-entry", "object-entry"]
)
def test_r2_replace_with_a_nested_stored_force_set_changes_nothing(
    clean_workdir, monkeypatch, force_set
):
    """REFUTE cx172 F1: BASE POST stored force_set=[[]] / [{"a":1}]; r1 cleared the queue, then set() raised -> 500."""
    runner = _world(monkeypatch)
    tid = queue_templates.create(
        "T034 badforce", SID, [NEW], force_set=force_set
    )  # stored before validation existed
    status, body = _call(
        app_queue_templates.api_queue_template_apply,
        tid,
        SID,
        path=f"/api/queue_templates/{tid}/apply/{SID}?mode=replace",
    )
    assert status == 400, f"QT_R2_FORCE_SET_NOT_ATOMIC status={status} body={body}"
    assert _queued() == sorted(OLD) and sorted(runner.jobs) == sorted(OLD), (
        f"QT_R2_QUEUE_CHANGED rows={_queued()} jobs={sorted(runner.jobs)}"
    )
    assert queue_templates.get(tid)["use_count"] == 0


def test_m1_control_replace_with_a_good_template_replaces(clean_workdir, monkeypatch):
    runner = _world(monkeypatch)
    tid = queue_templates.create("T034 goodmap", SID, [NEW], priority_map={NEW: "high"})
    status, body = _call(
        app_queue_templates.api_queue_template_apply,
        tid,
        SID,
        path=f"/api/queue_templates/{tid}/apply/{SID}?mode=replace",
    )
    assert status == 200 and body["added"] == 1, (
        f"QT_CONTROL_APPLY status={status} body={body}"
    )
    assert _queued() == [NEW] and runner.jobs[NEW]["priority"] == "high"


def test_m1_create_refuses_a_list_priority_map(clean_workdir):
    db.db_init()
    status, body = _call(
        app_queue_templates.api_queue_templates,
        json={"name": "T034 badmap", "urls": [NEW], "priority_map": [NEW]},
    )
    assert status == 400 and "priority_map" in body["error"], (
        f"QT_M1_CREATE_ACCEPTS_LIST status={status} body={body}"
    )


def test_m5_put_urls_of_the_wrong_type_is_400_and_stores_nothing(clean_workdir):
    db.db_init()
    tid = queue_templates.create("T034 urls", SID, [NEW])
    for urls in ("https://x", 123, [1, "https://y"]):
        status, body = _call(
            app_queue_templates.api_queue_template_one,
            tid,
            method="PUT",
            path=f"/api/queue_templates/{tid}",
            json={"urls": urls},
        )
        assert status == 400 and "urls" in body["error"], (
            f"QT_M5_URLS_COERCED urls={urls!r} status={status} body={body}"
        )
        assert queue_templates.get(tid)["urls"] == [NEW], (
            f"QT_M5_STORED {queue_templates.get(tid)['urls']}"
        )


def test_m5_control_put_a_url_list_is_stored(clean_workdir):
    db.db_init()
    tid = queue_templates.create("T034 urls", SID, [NEW])
    urls = ["https://example.test/a", "https://example.test/b"]
    status, body = _call(
        app_queue_templates.api_queue_template_one,
        tid,
        method="PUT",
        path=f"/api/queue_templates/{tid}",
        json={"urls": urls, "name": "renamed"},
    )
    assert status == 200 and body["ok"] is True, (
        f"QT_CONTROL_PUT status={status} body={body}"
    )
    stored = queue_templates.get(tid)
    assert stored["urls"] == urls and stored["name"] == "renamed"


def test_l2_create_with_an_object_origin_is_400(clean_workdir):
    db.db_init()
    status, body = _call(
        app_queue_templates.api_queue_templates,
        json={"name": "T034 origin", "urls": [NEW], "origin_site_id": {"a": 1}},
    )
    assert status == 400 and "origin_site_id" in body["error"], (
        f"QT_L2_ORIGIN_500 status={status} body={body}"
    )
    assert queue_templates.list_all() == []


def test_l2_control_create_with_a_string_origin(clean_workdir):
    db.db_init()
    status, body = _call(
        app_queue_templates.api_queue_templates,
        json={"name": "T034 origin", "urls": [NEW], "origin_site_id": SID, "note": "n"},
    )
    assert status == 200 and body["ok"] is True, (
        f"QT_CONTROL_CREATE status={status} body={body}"
    )
    assert queue_templates.get(body["id"])["origin_site_id"] == SID


def test_l4_empty_template_counts_zero_urls(clean_workdir):
    db.db_init()
    empty = queue_templates.create("T034 empty", SID, [])
    one = queue_templates.create("T034 one", SID, [NEW])
    two = queue_templates.create("T034 two", SID, [NEW, "https://example.test/n2"])
    counts = {t["id"]: t["url_count"] for t in queue_templates.list_all()}
    assert counts[empty] == 0, f"QT_L4_EMPTY_COUNT {counts[empty]}"
    assert (counts[one], counts[two]) == (1, 2), f"QT_CONTROL_COUNT {counts}"


def test_l5_put_with_nothing_to_update_is_400(clean_workdir):
    db.db_init()
    tid = queue_templates.create("T034 put", SID, [NEW])
    for kw in (
        {"json": {}},
        {"data": "null", "content_type": "application/json"},
        {"json": [1]},
    ):
        status, body = _call(
            app_queue_templates.api_queue_template_one,
            tid,
            method="PUT",
            path=f"/api/queue_templates/{tid}",
            **kw,
        )
        assert status == 400, f"QT_L5_PUT_NOT_400 {kw} status={status} body={body}"


def test_l5_put_to_a_missing_template_is_404(clean_workdir):
    db.db_init()
    status, body = _call(
        app_queue_templates.api_queue_template_one,
        999999,
        method="PUT",
        path="/api/queue_templates/999999",
        json={"name": "x"},
    )
    assert status == 404, f"QT_L5_MISSING_NOT_404 status={status} body={body}"


def test_create_body_must_be_an_object_with_a_string_name(clean_workdir):
    db.db_init()
    for kw in (
        {"json": ["x"]},
        {"json": {"name": 5}},
        {"json": {"name": "x", "urls": [None]}},
    ):
        status, body = _call(app_queue_templates.api_queue_templates, **kw)
        assert status == 400, f"QT_CREATE_500 {kw} status={status} body={body}"
