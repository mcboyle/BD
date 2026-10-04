"""O1826 C25: media-server integrations (M079 jd_bridge, M080/M081 jellyfin_deep).

M079 RED on base: real JD answers addLinks with ``{"data": {"id": <job>}}``; base
submit returns the dict's repr, poll sends an empty ``linkIds`` filter, JD answers
with every link and base reports rows[0] -- another link's status. GREEN: poll
filters by the job, then reports only the row whose URL/uuid is this link, else
"unknown". M080 RED on base: the lookup lists only the 50 newest items, so item
#60 is never found. GREEN: it pages. M081 RED on base: an auth/config/blocked
error is swallowed and polled for the full 30 s. GREEN (PM ruling option A):
returns None after exactly one request. The single-link, newest-item and
network-error tests pass on base and cut alike (negative controls).
"""
from __future__ import annotations

import pytest

from bulk_downloader import jd_bridge, jellyfin_deep

BD_GATE_SCOPE = "module"

pytestmark = pytest.mark.skipif(not jd_bridge._HAS_HTTPX, reason="httpx missing")

OURS = "https://host-a.example/video/ours"
OTHER = "https://host-b.example/video/other"


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeJD:
    """JD Remote API: addLinks -> {"data": {"id": job}}; queryLinks honours
    linkIds / jobUUIDs when non-empty and answers an empty filter with every link."""

    def __init__(self, links, ignore_filters=False):
        self.links = links  # [{"uuid", "job", "url", ...status fields}]
        self.ignore_filters = ignore_filters
        self.queries = []
        self._next_job = 9000

    def post(self, path, json=None, timeout=None):
        if path == "/linkgrabberv2/addLinks":
            job = self._next_job
            self._next_job += 1
            for link in self.links:
                if link["url"] == json["links"] and link["job"] is None:
                    link["job"] = job
            return _Resp({"data": {"id": job}})
        assert path == "/downloadsV2/queryLinks", path
        self.queries.append(dict(json))
        rows = list(self.links)
        if not self.ignore_filters:
            if json.get("linkIds"):
                rows = [r for r in rows if r["uuid"] in json["linkIds"]]
            elif json.get("jobUUIDs"):
                rows = [r for r in rows if r["job"] in json["jobUUIDs"]]
        return _Resp({"data": [{k: v for k, v in r.items() if k != "job"}
                               for r in rows]})


def _link(uuid, url, job=None, **status):
    return {"uuid": uuid, "job": job, "url": url, "name": url.rsplit("/", 1)[-1],
            "bytesLoaded": 0, "bytesTotal": 0, "finished": False,
            "running": False, "status": "", **status}


def _client(fake):
    c = jd_bridge.JDClient()
    c._client = fake
    return c


def test_m079_two_links_poll_reports_this_link_not_rows0():
    # The other link sits first in JD's list and is finished; ours is running.
    fake = _FakeJD([
        _link(111, OTHER, job=1, finished=True, bytesLoaded=5, bytesTotal=5),
        _link(222, OURS, running=True, bytesLoaded=10, bytesTotal=100),
    ])
    c = _client(fake)
    job = c.submit(OURS)
    st = c.poll(job)
    assert st["filename"] == "ours", st
    assert st["status"] == "running", st
    assert st["bytes_done"] == 10, st
    # Once resolved, later polls filter by the real link id, not the job id.
    c.poll(job)
    assert fake.queries[-1].get("linkIds") == [222], fake.queries[-1]


def test_m079_filter_ignored_without_id_match_is_unknown():
    # JD ignores the filter and returns only some other link: never report it.
    fake = _FakeJD([_link(111, OTHER, job=1, finished=True)],
                   ignore_filters=True)
    c = _client(fake)
    job = c.submit(OURS)
    st = c.poll(job)
    assert st["status"] == "unknown", st
    assert st["filename"] == "", st


def test_m079_non_numeric_id_never_sends_empty_filter():
    fake = _FakeJD([_link(111, OTHER, job=1, finished=True)])
    c = _client(fake)
    st = c.poll("{'id': 9000}")
    assert st["status"] == "unknown", st
    assert fake.queries == [], fake.queries


def test_m079_neg_single_link_status_unchanged():
    fake = _FakeJD([_link(222, OURS, finished=True, bytesLoaded=7, bytesTotal=7)])
    c = _client(fake)
    st = c.poll(c.submit(OURS))
    assert st["status"] == "done", st
    assert st["filename"] == "ours", st
    assert st["bytes_total"] == 7, st


def test_m079_same_url_older_finished_link_is_not_this_job():
    # A resubmitted URL meets its old, finished link (another job). Only the
    # job filter tells them apart: poll must report this job's running link.
    fake = _FakeJD([
        _link(111, OURS, job=1, finished=True, bytesLoaded=5, bytesTotal=5),
        _link(222, OURS, running=True, bytesLoaded=10, bytesTotal=100),
    ])
    c = _client(fake)
    job = c.submit(OURS)
    st = c.poll(job)
    assert st["status"] == "running", st
    assert st["bytes_done"] == 10, st
    assert fake.queries[-1].get("jobUUIDs") == [int(job)], fake.queries[-1]


def test_m079_resolved_link_ignored_filter_reports_ours_not_first_row():
    # Resolve the link, then JD ignores linkIds and lists another job's
    # finished link first: only the cached uuid may pick the row.
    fake = _FakeJD([
        _link(222, OURS, running=True, bytesLoaded=10, bytesTotal=100),
    ])
    c = _client(fake)
    job = c.submit(OURS)
    assert c.poll(job)["status"] == "running"
    fake.links.insert(0, _link(111, OTHER, job=1, finished=True,
                               bytesLoaded=5, bytesTotal=5))
    fake.ignore_filters = True
    st = c.poll(job)
    assert fake.queries[-1].get("linkIds") == [222], fake.queries[-1]
    assert st["filename"] == "ours", st
    assert st["status"] == "running", st
    assert st["bytes_done"] == 10, st


class _Clock:
    """Module-local stand-in for jellyfin_deep.time: sleep advances time()."""

    def __init__(self):
        self.now = 1000.0

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class _FakeLibrary:
    def __init__(self, paths):
        self.paths = paths  # newest first
        self.calls = []

    def __call__(self, method, path, params=None, **kw):
        self.calls.append(dict(params or {}))
        start = int(params.get("StartIndex", 0))
        limit = int(params["Limit"])
        items = [{"Id": f"id{i}", "Name": f"n{i}", "Path": p,
                  "ProductionYear": 2020}
                 for i, p in enumerate(self.paths)][start:start + limit]
        return {"Items": items, "TotalRecordCount": len(self.paths)}


def _jellyfin(monkeypatch, paths):
    monkeypatch.setattr(jellyfin_deep, "time", _Clock())
    c = jellyfin_deep.JellyfinClient("http://jf.example", "key")
    lib = _FakeLibrary(paths)
    monkeypatch.setattr(c, "_request", lib)
    return c, lib


def test_m080_item_60th_newest_is_found(monkeypatch):
    paths = [f"/media/lib/file{i:03d}.mp4" for i in range(300)]
    c, lib = _jellyfin(monkeypatch, paths)
    item = c.find_item_by_path("u1", "/media/lib/file059.mp4", timeout_s=30)
    assert item is not None, f"not found after {len(lib.calls)} requests"
    assert item["id"] == "id59"


def test_m080_neg_newest_item_found_on_first_request(monkeypatch):
    paths = [f"/media/lib/file{i:03d}.mp4" for i in range(300)]
    c, lib = _jellyfin(monkeypatch, paths)
    item = c.find_item_by_path("u1", "/media/lib/file000.mp4", timeout_s=30)
    assert item is not None and item["id"] == "id0"
    assert len(lib.calls) == 1


@pytest.mark.parametrize("kind", ["auth", "config", "blocked"])
def test_m081_fatal_error_returns_none_after_one_request(monkeypatch, kind):
    c, _ = _jellyfin(monkeypatch, [])
    calls = []

    def _fail(method, path, params=None, **kw):
        calls.append(path)
        raise jellyfin_deep.JellyfinError(kind, f"{kind} failure")

    monkeypatch.setattr(c, "_request", _fail)
    assert c.find_item_by_path("u1", "/media/lib/x.mp4", timeout_s=30) is None
    assert len(calls) == 1, f"{kind} error polled {len(calls)} times"


def test_m081_neg_network_error_keeps_polling(monkeypatch):
    c, _ = _jellyfin(monkeypatch, [])
    calls = []

    def _fail(method, path, params=None, **kw):
        calls.append(path)
        raise jellyfin_deep.JellyfinError("network", "connection failed")

    monkeypatch.setattr(c, "_request", _fail)
    assert c.find_item_by_path("u1", "/media/lib/x.mp4", timeout_s=30) is None
    assert len(calls) == 15, calls


def test_m080_absent_item_still_times_out_none(monkeypatch):
    paths = [f"/media/lib/file{i:03d}.mp4" for i in range(300)]
    c, _ = _jellyfin(monkeypatch, paths)
    assert c.find_item_by_path("u1", "/media/lib/missing.mp4",
                               timeout_s=10) is None
