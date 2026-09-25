"""Queue snapshot metadata must survive reload after import."""

BD_GATE_SCOPE = "module"

from threading import RLock

from bulk_downloader import app as app_module
from bulk_downloader import app_sites_queue, db


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


def test_import_persists_priority_and_force_download(clean_workdir, monkeypatch):
    db.db_init()
    runner = _Runner("site")
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: {"site": runner})
    url = "https://example.test/new"

    with app_module.app.test_request_context(
        "/api/sites/site/queue/import", method="POST",
        json={"mode": "append", "rows": [
            {"url": url, "priority": "high", "force_download": True},
        ]},
    ):
        response = app_sites_queue.api_queue_import("site")
    assert response.get_json()["added"] == 1
    assert runner.jobs[url]["priority"] == "high"
    assert runner.jobs[url]["force_download"] is True
    persisted = next(row for row in db.queue_load("site") if row["url"] == url)
    assert persisted["priority"] == "high"
    assert persisted["force_download"] == 1


def test_import_ignores_non_string_priority(clean_workdir, monkeypatch):
    db.db_init()
    runner = _Runner("site")
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: {"site": runner})
    url = "https://example.test/bad-priority"

    with app_module.app.test_request_context(
        "/api/sites/site/queue/import", method="POST",
        json={"mode": "append", "rows": [
            {"url": url, "priority": {"x": 1}, "force_download": True},
        ]},
    ):
        response = app_sites_queue.api_queue_import("site")
    assert response.get_json()["added"] == 1
    assert runner.jobs[url]["priority"] == ""
    persisted = next(row for row in db.queue_load("site") if row["url"] == url)
    assert persisted["priority"] == ""
    assert persisted["force_download"] == 1
