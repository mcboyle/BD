"""RED-first repro for F-RUN03-01.

runner_queue.QueueMixin.load_urls reuses the local `new_urls` for BOTH the
per-URL `_playlist_expand_one` result AND the added-URL accumulator. When a
listing URL is playlist-expanded, `new_urls` is left holding the last
expansion's children; the accumulator loop then appends the real children on
top, so `queue_bulk_upsert` receives the children DUPLICATED (with wrong
ordinals). After the fix the expansion result uses a distinct local.

Pristine RED: queue_bulk_upsert receives duplicated child URLs.
"""
import threading
import logging
import types


def test_no_accumulator_pollution(monkeypatch):
    import bulk_downloader.runner_queue as m
    import bulk_downloader.content_rights as cr

    monkeypatch.setattr(cr, "url_is_blocked", lambda u: None, raising=False)
    monkeypatch.setattr(m, "_PLAYLIST_AVAILABLE", True, raising=False)
    monkeypatch.setattr(m, "_playlist",
                        types.SimpleNamespace(is_likely_listing_url=lambda *a, **k: True),
                        raising=False)
    captured = {}
    monkeypatch.setattr(m, "queue_bulk_upsert",
                        lambda site, urls, **k: captured.setdefault("urls", list(urls)),
                        raising=False)

    class _FakeQ(m.QueueMixin):
        def __init__(self):
            self.config = {"use_playlist_extractor": True, "download_dir": ""}
            self.jobs = {}
            self.urls = []
            self.site_id = "s1"
            self._lock = threading.RLock()
            self.log = logging.getLogger("test_rq")
            self.events = []

        def log_event(self, kind, msg=None, **k):
            self.events.append((kind, msg))

        def _playlist_expand_one(self, u):
            return ["https://x/c1", "https://x/c2"]

    q = _FakeQ()
    q.load_urls(["https://x/listing"])
    assert captured.get("urls") == ["https://x/c1", "https://x/c2"], captured.get("urls")
