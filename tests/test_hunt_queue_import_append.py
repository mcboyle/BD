BD_GATE_SCOPE = "module"

"""Append import must preserve metadata of jobs already in the queue."""

from threading import RLock

from bulk_downloader import app as app_module
from bulk_downloader import app_sites_queue, db


class _Runner:
    def __init__(self, sid, old_url):
        self.sid = sid
        self._lock = RLock()
        self.jobs = {old_url: {"url": old_url, "priority": "low",
                               "force_download": False}}
        self.urls = [old_url]

    def load_urls(self, urls, folder_scan=False):
        for url in urls:
            self.jobs[url] = {"url": url, "priority": "", "force_download": False}
            self.urls.append(url)
            db.queue_upsert(self.sid, url, status="pending")
        return len(urls), 0


def test_append_import_leaves_existing_job_metadata(clean_workdir, monkeypatch):
    db.db_init()
    old_url = "https://example.test/old"
    new_url = "https://example.test/new"
    db.queue_upsert("site", old_url, status="pending", priority="low",
                    force_download=0)
    runner = _Runner("site", old_url)
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: {"site": runner})

    with app_module.app.test_request_context(
        "/api/sites/site/queue/import", method="POST",
        json={"mode": "append", "rows": [
            {"url": old_url, "priority": "high", "force_download": True},
            {"url": new_url, "priority": "normal", "force_download": True},
        ]},
    ):
        response = app_sites_queue.api_queue_import("site")
    assert response.get_json()["added"] == 1
    assert runner.jobs[new_url]["priority"] == "normal"
    assert runner.jobs[new_url]["force_download"] is True
    assert runner.jobs[old_url]["priority"] == "low"
    assert runner.jobs[old_url]["force_download"] is False
    rows = {row["url"]: row for row in db.queue_load("site")}
    assert rows[old_url]["priority"] == "low"
    assert rows[old_url]["force_download"] == 0
