"""O1789 bug-hunt ssqh-f5: Queue "pin to front" accepted any priority and any URL.

Finding F5 (harness-work/O1789/bughunt/spa-sites-queue-history/FINDINGS.md): POST /api/sites/<sid>/priority
took any ``priority`` (live: "bogus" -> {"ok": true}, stored on the waiting row) and answered {"ok": true} for a
URL that is not queued, so the SPA toasted "Pinned" for nothing. bulk_priority already refuses a priority that is
not normal|high with 400. Fixed: the same 400 here, and 404 for a URL / row id that names no queued job.
"""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import pytest

pytestmark = pytest.mark.bd_module_wipe

_SID = "ssqhf5"
_A = "https://example.invalid/ssqh/a.mp4"
_B = "https://example.invalid/ssqh/b.mp4"
_GONE = "https://example.invalid/ssqh/never-queued.mp4"


@pytest.fixture
def runner(clean_workdir):
    from bulk_downloader import app_state as st
    from bulk_downloader.db import db_init, queue_upsert
    from bulk_downloader.runner import SiteRunner

    db_init()
    r = SiteRunner(_SID, {"name": _SID})
    for i, url in enumerate((_A, _B)):
        queue_upsert(_SID, url, status="pending", message="", ord=i)
        with r._lock:
            r.jobs[url] = {"status": "pending", "message": "", "ts": "00:00:01"}
            r.urls.append(url)
    st.s_cfg[_SID] = {"name": _SID}
    st.runners[_SID] = r
    yield r
    st.runners.pop(_SID, None)
    st.s_cfg.pop(_SID, None)


def _post(payload):
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")
    tok = (client.get("/api/csrf").get_json() or {}).get("csrf_token", "")
    return client.post(f"/api/sites/{_SID}/priority", json=payload, headers={"X-CSRF-Token": tok})


def _db_priority(url):
    from bulk_downloader.db import db_conn
    with db_conn() as cx:
        row = cx.execute("SELECT priority FROM queue WHERE site_id=? AND url=?", (_SID, url)).fetchone()
    return row[0] if row else None


def _snapshot(r):
    with r._lock:
        return list(r.urls), {u: j.get("priority") for u, j in r.jobs.items()}


@pytest.mark.parametrize("priority", ["bogus", "HIGH", "", None, 1, ["high"]], ids=repr)
def test_bad_priority_is_refused_and_not_stored(runner, priority):
    before = _snapshot(runner), _db_priority(_B)
    resp = _post({"url": _B, "priority": priority})
    body = resp.get_json() or {}
    after = _snapshot(runner), _db_priority(_B)
    assert resp.status_code == 400 and body.get("ok") is not True, (
        f"SSQH_F5 bad priority {priority!r} accepted: HTTP {resp.status_code} {body}")
    assert after == before, f"SSQH_F5 bad priority {priority!r} changed the queue: {before} -> {after}"


@pytest.mark.parametrize("payload", [{"url": _GONE, "priority": "high"}, {"row_id": "q0000000000000000"},
                                     {"url": "", "priority": "high"}, {"priority": "high"}], ids=repr)
def test_unqueued_url_is_404_not_ok(runner, payload):
    before = _snapshot(runner)
    resp = _post(payload)
    body = resp.get_json() or {}
    assert resp.status_code == 404 and body.get("ok") is not True, (
        f"SSQH_F5 priority on a URL that is not queued answered HTTP {resp.status_code} {body}")
    assert _snapshot(runner) == before and _db_priority(_GONE) is None


def test_non_object_body_is_400(runner):
    resp = _post(["high"])
    assert resp.status_code == 400, f"SSQH_F5 list body: HTTP {resp.status_code}"


def test_control_pin_queued_url_front_loads_and_persists(runner):
    resp = _post({"url": _B, "priority": "high"})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}
    urls, pri = _snapshot(runner)
    assert urls[0] == _B and pri[_B] == "high" and _db_priority(_B) == "high"


def test_control_row_id_and_normal_still_accepted(runner):
    from bulk_downloader.db import queue_row_id
    assert _post({"row_id": queue_row_id(_A), "priority": "high"}).status_code == 200
    assert _snapshot(runner)[1][_A] == "high"
    resp = _post({"row_id": queue_row_id(_A)})  # priority defaults to normal
    assert resp.status_code == 200 and _snapshot(runner)[1][_A] == "normal" and _db_priority(_A) == "normal"


def test_lens_non_json_content_type_keeps_415(runner):
    # lens SSQ5C-A: get_json(silent=True) turned a queued URL sent as text/plain into 404 "URL not queued";
    # BASE answered app.py's 415 "request body must be JSON".
    from bulk_downloader import app as a
    client = a.app.test_client()
    client.get("/")
    tok = (client.get("/api/csrf").get_json() or {}).get("csrf_token", "")
    before = _snapshot(runner)
    resp = client.post(f"/api/sites/{_SID}/priority", data='{"url": "%s", "priority": "high"}' % _B,
                       content_type="text/plain", headers={"X-CSRF-Token": tok})
    assert resp.status_code == 415, f"SSQH_F5 text/plain body: HTTP {resp.status_code} {resp.get_json()}"
    assert _snapshot(runner) == before


def test_lens_empty_list_body_is_400(runner):
    resp = _post([])
    assert resp.status_code == 400, f"SSQH_F5 empty list body: HTTP {resp.status_code} {resp.get_json()}"
