"""A bad replacement snapshot must leave the existing queue intact."""

from threading import RLock

from bulk_downloader import app as app_module
from bulk_downloader import app_sites_queue, db


class _Runner:
    def __init__(self):
        self._lock = RLock()
        self.jobs = {"https://example.test/old": {"status": "pending"}}
        self.urls = ["https://example.test/old"]

    def load_urls(self, urls, folder_scan=False):
        for url in urls:
            self.jobs[url] = {"status": "pending"}
            self.urls.append(url)
        return len(urls), 0


def test_replace_import_validates_rows_before_deleting(monkeypatch):
    runner = _Runner()
    deleted = []
    monkeypatch.setattr(app_sites_queue, "_app_runners", lambda: {"site": runner})
    monkeypatch.setattr(db, "queue_delete_site", lambda sid: deleted.append(sid))

    with app_module.app.test_request_context(
        "/api/sites/site/queue/import", method="POST",
        json={"mode": "replace", "rows": [{"url": "https://example.test/new"}]},
    ):
        response = app_sites_queue.api_queue_import("site")
    assert response.get_json()["ok"] is True
    assert deleted == ["site"]
    assert set(runner.jobs) == {"https://example.test/new"}

    runner.jobs = {"https://example.test/old": {"status": "pending"}}
    runner.urls = ["https://example.test/old"]
    with app_module.app.test_request_context(
        "/api/sites/site/queue/import", method="POST",
        json={"mode": "replace", "rows": [None]},
    ):
        try:
            response = app_sites_queue.api_queue_import("site")
        except AttributeError:
            response = None
    assert deleted == ["site"]
    assert set(runner.jobs) == {"https://example.test/old"}
    assert response is not None and response[1] == 400

    # Lens: every malformed shape is rejected before the replacement deletes anything.
    for bad_rows in ([{"url": 5}], ["https://example.test/bare-string"], {"url": "x"}, 5):
        with app_module.app.test_request_context(
            "/api/sites/site/queue/import", method="POST",
            json={"mode": "replace", "rows": bad_rows},
        ):
            try:
                response = app_sites_queue.api_queue_import("site")
            except AttributeError:
                response = None
        assert deleted == ["site"], bad_rows
        assert set(runner.jobs) == {"https://example.test/old"}, bad_rows
        assert response is not None and response[1] == 400, bad_rows
