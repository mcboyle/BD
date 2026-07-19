"""Cut 8 write surface: POST /api/bulk/enqueue.

Batch wrapper over the existing per-site run path (SiteRunner.load_urls)
-- the same seam add_url / discovery / capture_schedules use. Validated +
CSRF-gated. Idempotency on double-submit is inherent: load_urls de-dupes
against the live queue, so a resubmitted batch reports dupes and adds 0.

RED on pristine 379: the route is absent (404 / endpoint-not-found).
"""
from __future__ import annotations


def _new_client():
    from bulk_downloader import app as A
    from bulk_downloader.db import db_init
    db_init()
    c = A.app.test_client()
    token = c.get("/api/pair").get_json()["token"]
    csrf = c.post("/api/pair/redeem", json={"token": token}).get_json()["csrf_token"]
    return c, csrf


class _FakeRunner:
    """Stand-in runner whose load_urls de-dupes like the real one."""
    def __init__(self):
        self._seen = set()

    def load_urls(self, urls):
        added = dupes = 0
        for u in urls:
            if u in self._seen:
                dupes += 1
            else:
                self._seen.add(u)
                added += 1
        return added, dupes, 0


def _install_fake_site(sid="bulksite"):
    from bulk_downloader import app as A
    A.s_cfg[sid] = {"name": sid}
    A.runners[sid] = _FakeRunner()
    return sid


def _uninstall(sid):
    from bulk_downloader import app as A
    A.s_cfg.pop(sid, None)
    A.runners.pop(sid, None)


def test_valid_batch_enqueues():
    c, csrf = _new_client()
    sid = _install_fake_site("bulk_ok")
    try:
        r = c.post("/api/bulk/enqueue",
                   json={"site_id": sid,
                         "urls": ["http://x/1", "http://x/2", "http://x/3"]},
                   headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["ok"] is True
        assert d["site_id"] == sid
        assert d["added"] == 3
        assert d["dupes"] == 0
        assert d["requested"] == 3
    finally:
        _uninstall(sid)


def test_resubmit_is_idempotent_via_dedup():
    c, csrf = _new_client()
    sid = _install_fake_site("bulk_idem")
    H = {"X-CSRF-Token": csrf}
    try:
        body = {"site_id": sid, "urls": ["http://y/1", "http://y/2"]}
        c.post("/api/bulk/enqueue", json=body, headers=H)
        d = c.post("/api/bulk/enqueue", json=body, headers=H).get_json()
        assert d["added"] == 0
        assert d["dupes"] == 2
    finally:
        _uninstall(sid)


def test_unknown_site_is_400():
    c, csrf = _new_client()
    r = c.post("/api/bulk/enqueue",
               json={"site_id": "nope", "urls": ["http://z/1"]},
               headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400


def test_missing_or_empty_urls_is_400():
    c, csrf = _new_client()
    sid = _install_fake_site("bulk_empty")
    H = {"X-CSRF-Token": csrf}
    try:
        assert c.post("/api/bulk/enqueue", json={"site_id": sid},
                      headers=H).status_code == 400
        assert c.post("/api/bulk/enqueue", json={"site_id": sid, "urls": []},
                      headers=H).status_code == 400
    finally:
        _uninstall(sid)


def test_non_list_urls_is_400():
    c, csrf = _new_client()
    sid = _install_fake_site("bulk_nonlist")
    try:
        r = c.post("/api/bulk/enqueue",
                   json={"site_id": sid, "urls": "http://not-a-list/1"},
                   headers={"X-CSRF-Token": csrf})
        assert r.status_code == 400
    finally:
        _uninstall(sid)


def test_csrf_required():
    c, _csrf = _new_client()
    sid = _install_fake_site("bulk_csrf")
    try:
        r = c.post("/api/bulk/enqueue",
                   json={"site_id": sid, "urls": ["http://w/1"]})
        assert r.status_code == 403
    finally:
        _uninstall(sid)


def test_over_cap_is_truncated():
    c, csrf = _new_client()
    sid = _install_fake_site("bulk_cap")
    try:
        urls = [f"http://big/{i}" for i in range(1200)]
        d = c.post("/api/bulk/enqueue",
                   json={"site_id": sid, "urls": urls},
                   headers={"X-CSRF-Token": csrf}).get_json()
        assert d["ok"] is True
        assert d["added"] == 1000          # capped at the 1000 safety limit
        assert d["skipped"] == 200          # overflow reported, not silently lost
    finally:
        _uninstall(sid)
