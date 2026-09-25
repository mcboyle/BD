"""Queue template metadata survives a reload after apply."""

BD_GATE_SCOPE = "module"

from threading import RLock

from bulk_downloader import app as app_module
from bulk_downloader import app_queue_templates, db, queue_templates


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


def test_apply_persists_priority_and_force_download(clean_workdir, monkeypatch):
    db.db_init()
    sid = "site"
    url = "https://example.test/new"
    runner = _Runner(sid)
    monkeypatch.setattr(app_queue_templates, "_app_runners", lambda: {sid: runner})
    tid = queue_templates.create(
        "saved", sid, [url], priority_map={url: "high"}, force_set=[url]
    )

    with app_module.app.test_request_context(
        f"/api/queue_templates/{tid}/apply/{sid}", method="POST"
    ):
        response = app_queue_templates.api_queue_template_apply(tid, sid)
    assert response.get_json()["added"] == 1
    assert runner.jobs[url]["priority"] == "high"
    assert runner.jobs[url]["force_download"] is True
    persisted = next(row for row in db.queue_load(sid) if row["url"] == url)
    assert persisted["priority"] == "high"
    assert persisted["force_download"] == 1


def test_apply_ignores_non_string_priority(clean_workdir, monkeypatch):
    db.db_init()
    sid = "site"
    url = "https://example.test/bad-priority"
    runner = _Runner(sid)
    monkeypatch.setattr(app_queue_templates, "_app_runners", lambda: {sid: runner})
    tid = queue_templates.create(
        "bad", sid, [url], priority_map={url: {"x": 1}}, force_set=[url]
    )

    with app_module.app.test_request_context(
        f"/api/queue_templates/{tid}/apply/{sid}", method="POST"
    ):
        response = app_queue_templates.api_queue_template_apply(tid, sid)
    assert response.get_json()["added"] == 1
    assert runner.jobs[url]["priority"] == ""
    persisted = next(row for row in db.queue_load(sid) if row["url"] == url)
    assert persisted["priority"] == ""
    assert persisted["force_download"] == 1
