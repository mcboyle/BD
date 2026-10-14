"""Bug-hunt qti-queue-meta (deep-queue-templates-io F5, F14, F15).

F5  a queue-import row or template entry written "url<TAB>Header=value" lost
    its status / priority / force flag: the job is keyed by the URL alone,
    the metadata was looked up by the whole string.
F14 POST/PUT /api/queue_templates stored a non-object priority_map or a
    non-list force_set; apply then answered 500 after loading the URLs, or
    forced the URL "h" (one character of a string).
F15 a template URL entry holding "\\r\\n" was split on "\\n" only.

Real SiteRunner on a scratch workdir + scratch DB (clean_workdir). No network.
"""
from __future__ import annotations

import json

import pytest

from bulk_downloader import app as app_module
from bulk_downloader import app_queue_templates, app_sites_queue, db
from bulk_downloader import queue_templates as qt
from bulk_downloader.runner import SiteRunner

BD_GATE_SCOPE = "module"

SID = "qmeta"
U1 = "https://a.test/1"
U2 = "https://a.test/2"
TAB1 = U1 + "\tReferer=https://a.test/\tX-Token=abc"


@pytest.fixture
def runner(clean_workdir, monkeypatch):
    db.db_init()
    r = SiteRunner(SID, {"name": SID})
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: {SID: r})
    monkeypatch.setattr(app_queue_templates, "_app_runners", lambda: {SID: r})
    return r


def _call(view, path, *args, method="POST", body=None):
    with app_module.app.test_request_context(path, method=method, json=body):
        out = view(*args)
    resp, code = out if isinstance(out, tuple) else (out, out.status_code)
    return resp.get_json(), code


def _import(rows, mode="append"):
    return _call(app_sites_queue.api_queue_import,
                 f"/api/sites/{SID}/queue/import", SID,
                 body={"mode": mode, "rows": rows})


def _create(**body):
    body.setdefault("name", "t")
    return _call(app_queue_templates.api_queue_templates,
                 "/api/queue_templates", body=body)


def _apply(tid, mode="append"):
    return _call(app_queue_templates.api_queue_template_apply,
                 f"/api/queue_templates/{tid}/apply/{SID}?mode={mode}", tid, SID)


def _persisted(url):
    return next(row for row in db.queue_load(SID) if row["url"] == url)


# ── F5: tab-header rows keep their metadata ───────────────────────────

def test_f5_import_row_with_headers_keeps_status_priority_and_force(runner):
    body, code = _import([
        {"url": TAB1, "status": "done", "message": "ok",
         "priority": "high", "force_download": True},
        {"url": U2, "status": "done", "message": "ok",          # control: no header
         "priority": "high", "force_download": True},
    ])
    assert code == 200 and body["added"] == 2
    assert set(runner.jobs) == {U1, U2}
    for url in (U1, U2):
        job = runner.jobs[url]
        assert (job["status"], job["priority"], job.get("force_download")) == \
            ("done", "high", True), url
        row = _persisted(url)
        assert (row["status"], row["priority"], row["force_download"]) == ("done", "high", 1)
    assert runner.jobs[U1]["custom_headers"] == {
        "Referer": "https://a.test/", "X-Token": "abc"}
    assert "custom_headers" not in runner.jobs[U2]


def test_f5_import_of_a_header_row_already_queued_is_skipped(runner):
    _import([{"url": U1, "priority": "low"}])
    body, _ = _import([{"url": TAB1, "status": "done", "priority": "high",
                        "force_download": True}])
    assert body["added"] == 0
    assert runner.jobs[U1]["priority"] == "low"                 # append leaves it alone
    assert runner.jobs[U1]["status"] == "pending"
    assert not runner.jobs[U1].get("force_download")


@pytest.mark.parametrize("keyed_by", ["entry", "url"])
def test_f5_template_entry_with_headers_keeps_priority_and_force(runner, keyed_by):
    key = TAB1 if keyed_by == "entry" else U1
    body, code = _create(urls=[TAB1, U2], priority_map={key: "high", U2: "low"},
                         force_set=[key])
    assert code == 200
    body, code = _apply(body["id"])
    assert code == 200 and body["added"] == 2
    assert runner.jobs[U1]["priority"] == "high"
    assert runner.jobs[U1]["force_download"] is True
    assert _persisted(U1)["priority"] == "high" and _persisted(U1)["force_download"] == 1
    assert runner.jobs[U2]["priority"] == "low"                 # control
    assert not runner.jobs[U2].get("force_download")


def test_f5_template_apply_twice_adds_nothing_the_second_time(runner):
    tid = _create(urls=[TAB1], priority_map={TAB1: "high"})[0]["id"]
    assert _apply(tid)[0]["added"] == 1
    runner.jobs[U1]["priority"] = "low"
    assert _apply(tid)[0]["added"] == 0
    assert runner.jobs[U1]["priority"] == "low"                 # already queued: untouched


def test_f5_job_url_matches_what_load_urls_keys_the_job_by(runner):
    for entry in [TAB1, "  " + U2 + " \tA=b", "https://a.test/3"]:
        runner.load_urls([entry])
        assert qt.job_url(entry) in runner.jobs, entry
    assert qt.job_url(None) == "" and qt.job_url("") == ""


# ── F14: priority_map / force_set shapes ──────────────────────────────

BAD_PMAP = ["https://a.test/1", ["x"], 5, {"https://a.test/1": 5}, {"https://a.test/1": None}]
BAD_FSET = ["https://a.test/z", {"a": 1}, 5, [5], [["x"]]]


@pytest.mark.parametrize("bad", BAD_PMAP, ids=repr)
def test_f14_create_refuses_a_bad_priority_map(runner, bad):
    body, code = _create(urls=[U1, U2], priority_map=bad)
    assert code == 400 and "priority_map" in body["error"]
    assert qt.list_all() == []


@pytest.mark.parametrize("bad", BAD_FSET, ids=repr)
def test_f14_create_refuses_a_bad_force_set(runner, bad):
    body, code = _create(urls=[U1, U2], force_set=bad)
    assert code == 400 and "force_set" in body["error"]
    assert qt.list_all() == []


@pytest.mark.parametrize("field,bad", [("priority_map", "x"), ("priority_map", []),
                                       ("force_set", "https://a.test/z"), ("force_set", {}),
                                       ("urls", "https://a.test/9")])
def test_f14_update_refuses_bad_shapes_and_changes_nothing(runner, field, bad):
    tid = _create(urls=[U1], priority_map={U1: "high"}, force_set=[U1])[0]["id"]
    body, code = _call(app_queue_templates.api_queue_template_one,
                       f"/api/queue_templates/{tid}", tid, method="PUT", body={field: bad})
    assert code == 400 and field in body["error"]
    t = qt.get(tid)
    assert (t["urls"], t["priority_map"], t["force_set"]) == ([U1], {U1: "high"}, [U1])


# r2 (REFUTE cx220 F1): a wrong-typed FALSEY value was coerced by `or` before
# the shape check and stored as an empty template field with a 200.
FALSEY_WRONG = [("priority_map", []), ("priority_map", ""), ("priority_map", 0),
                ("priority_map", False), ("force_set", {}), ("force_set", ""),
                ("force_set", 0), ("force_set", False), ("urls", {}), ("urls", ""),
                ("urls", 0), ("urls", False)]


@pytest.mark.parametrize("field,bad", FALSEY_WRONG, ids=repr)
def test_f14_r2_create_refuses_wrong_typed_falsey_values(runner, field, bad):
    payload = {"urls": [U1], field: bad}
    body, code = _create(**payload)
    assert code == 400 and field in body["error"], body
    assert qt.list_all() == []


@pytest.mark.parametrize("field,bad", FALSEY_WRONG, ids=repr)
def test_f14_r2_update_refuses_wrong_typed_falsey_values(runner, field, bad):
    tid = _create(urls=[U1], priority_map={U1: "high"}, force_set=[U1])[0]["id"]
    body, code = _call(app_queue_templates.api_queue_template_one,
                       f"/api/queue_templates/{tid}", tid, method="PUT", body={field: bad})
    assert code == 400 and field in body["error"], body
    t = qt.get(tid)
    assert (t["urls"], t["priority_map"], t["force_set"]) == ([U1], {U1: "high"}, [U1])


@pytest.mark.parametrize("payload", [
    {}, {"urls": None, "priority_map": None, "force_set": None},
    {"urls": [], "priority_map": {}, "force_set": []},
    {"urls": [U1], "priority_map": {}, "force_set": []}])
def test_f14_r2_absent_null_and_right_typed_empty_values_are_accepted(runner, payload):
    body, code = _create(**payload)
    assert code == 200, body
    t = qt.get(body["id"])
    assert (t["urls"], t["priority_map"], t["force_set"]) == (payload.get("urls") or [], {}, [])


def test_f14_good_shapes_still_work(runner):
    body, code = _create(urls=[U1, U2], priority_map={U1: "high"}, force_set=[U2])
    assert code == 200
    tid = body["id"]
    body, code = _call(app_queue_templates.api_queue_template_one,
                       f"/api/queue_templates/{tid}", tid, method="PUT",
                       body={"priority_map": {U2: "low"}, "note": "n"})
    assert code == 200 and body["ok"] is True
    assert _apply(tid)[1] == 200
    assert runner.jobs[U2]["priority"] == "low" and runner.jobs[U2]["force_download"] is True
    for empty in ({}, {"priority_map": None, "force_set": None},
                  {"priority_map": {}, "force_set": []}):
        assert _create(urls=[U1], **empty)[1] == 200


# r3: one design with qt-validate (train T281b). A template stored before the
# checks existed is REFUSED by apply (qt-validate M1), in both modes, before
# anything is loaded or cleared -- r1/r2 read it as empty and applied it.
@pytest.mark.parametrize("pmap,fset", [('"https://a.test/1"', "[]"), ("[1]", "[]"), ("5", "[]"),
                                       ("{}", '"https://a.test/z"'), ("{}", '{"a": 1}'),
                                       ("{}", "[5, null]"), ("{}", '[["x"], {"a": 1}]')])
@pytest.mark.parametrize("mode", ["append", "replace"])
def test_f14_a_template_stored_before_the_check_is_refused_and_loads_nothing(
        runner, pmap, fset, mode):
    runner.load_urls(["https://a.test/keep"])
    tid = qt.create("old", SID, [U1, U2, "h"])
    with db.db_conn() as cx:
        cx.execute("UPDATE queue_templates SET priority_map=?, force_set=? WHERE id=?",
                   (pmap, fset, tid))
    body, code = _apply(tid, mode)
    assert code == 400 and "template" in body["error"], body
    assert list(runner.jobs) == ["https://a.test/keep"]         # nothing loaded, nothing cleared
    assert [r["url"] for r in db.queue_load(SID)] == ["https://a.test/keep"]


def test_f14_a_stored_template_with_unparseable_json_still_applies_as_empty(runner):
    tid = qt.create("old", SID, [U1, "h"])
    with db.db_conn() as cx:
        cx.execute("UPDATE queue_templates SET priority_map=?, force_set=? WHERE id=?",
                   ("not json", "not json", tid))
    body, code = _apply(tid)
    assert code == 200 and body["added"] == 2
    assert not runner.jobs["h"].get("force_download")


# r3 parity: create and update run the SAME check, so they refuse the same values.
PARITY_BAD = FALSEY_WRONG + [("priority_map", b) for b in BAD_PMAP] + \
    [("force_set", b) for b in BAD_FSET] + [
    ("urls", "https://a.test/9"), ("urls", [5]), ("urls", [None]), ("urls", {"a": 1}),
    ("name", 5), ("name", ["x"]), ("note", 5), ("note", {}),
    ("origin_site_id", {}), ("origin_site_id", 5)]


@pytest.mark.parametrize("field,bad", PARITY_BAD, ids=repr)
def test_r3_create_and_update_refuse_the_same_values_with_the_same_message(runner, field, bad):
    tid = _create(urls=[U1], priority_map={U1: "high"}, force_set=[U1], note="n")[0]["id"]
    put, put_code = _call(app_queue_templates.api_queue_template_one,
                          f"/api/queue_templates/{tid}", tid, method="PUT", body={field: bad})
    payload = {"name": "t2", "urls": [U2]}
    payload[field] = bad
    post, post_code = _call(app_queue_templates.api_queue_templates,
                            "/api/queue_templates", body=payload)
    assert (post_code, put_code) == (400, 400), (post, put)
    assert post["error"] == put["error"] and field in post["error"]
    assert len(qt.list_all()) == 1                              # no second template
    t = qt.get(tid)
    assert (t["name"], t["urls"], t["priority_map"], t["force_set"], t["note"]) == \
        ("t", [U1], {U1: "high"}, [U1], "n")


PARITY_GOOD = [("priority_map", {U1: "low"}), ("priority_map", {}), ("force_set", [U2]),
               ("force_set", []), ("urls", [U2]), ("note", "x"), ("name", "renamed")]


@pytest.mark.parametrize("field,good", PARITY_GOOD, ids=repr)
def test_r3_create_and_update_accept_the_same_values(runner, field, good):
    tid = _create(urls=[U1])[0]["id"]
    put, put_code = _call(app_queue_templates.api_queue_template_one,
                          f"/api/queue_templates/{tid}", tid, method="PUT", body={field: good})
    payload = {"name": "t2", "urls": [U1]}
    payload[field] = good
    post, post_code = _call(app_queue_templates.api_queue_templates,
                            "/api/queue_templates", body=payload)
    assert (post_code, put_code) == (200, 200), (post, put)


# ── F15: line breaks inside one URL entry ─────────────────────────────

def test_f15_crlf_inside_an_entry_is_two_clean_urls(runner):
    body, code = _create(urls=[U1 + "\r\n" + U2, U1, " \r\n ", U2 + "\r"],
                         priority_map={U1: "high"})
    assert code == 200
    tid = body["id"]
    assert qt.get(tid)["urls"] == [U1, U2]
    assert [t["url_count"] for t in qt.list_all()] == [2]
    assert _apply(tid)[0]["added"] == 2
    assert set(runner.jobs) == {U1, U2}
    assert runner.jobs[U1]["priority"] == "high"


def test_f15_update_normalizes_the_same_way(runner):
    tid = _create(urls=[U1])[0]["id"]
    assert qt.update(tid, urls=[U2 + "\r\n" + U1 + "\n" + U2, "\r"]) is True
    assert qt.get(tid)["urls"] == [U2, U1]
    with db.db_conn() as cx:
        raw = cx.execute("SELECT urls FROM queue_templates WHERE id=?", (tid,)).fetchone()[0]
    assert raw == U2 + "\n" + U1 and "\r" not in raw


def test_f15_a_row_stored_with_carriage_returns_reads_clean(runner):
    tid = qt.create("old", SID, [U1])
    with db.db_conn() as cx:
        cx.execute("UPDATE queue_templates SET urls=?, priority_map=? WHERE id=?",
                   (U1 + "\r\n" + U2 + "\n" + U1, json.dumps({U1: "high"}), tid))
    assert qt.get(tid)["urls"] == [U1, U2]
    assert _apply(tid)[0]["added"] == 2
    assert runner.jobs[U1]["priority"] == "high"


def test_f15_a_lone_carriage_return_also_separates(runner):
    tid = _create(urls=[U1 + "\r" + U2])[0]["id"]
    assert qt.get(tid)["urls"] == [U1, U2]


def test_f15_plain_lists_are_unchanged(runner):
    tid = _create(urls=["  " + U1 + "  ", "", U2, U1])[0]["id"]
    assert qt.get(tid)["urls"] == [U1, U2]
