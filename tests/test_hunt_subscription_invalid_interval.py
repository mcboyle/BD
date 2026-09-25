BD_GATE_SCOPE = "module"

import sys
import threading
from types import SimpleNamespace

import pytest

import bulk_downloader
from bulk_downloader.runner_scheduler import SchedulerMixin


class _Runner(SchedulerMixin):
    def __init__(self, subscriptions):
        self.config = {"subscriptions": subscriptions}
        self._lock = threading.RLock()
        self.urls = []
        self.seen = []
        self.log = SimpleNamespace(warning=lambda *_args: None)

    def log_event(self, *_args):
        pass

    def _scrape_listing_urls(self, url):
        self.seen.append(url)
        return []


@pytest.mark.parametrize("bad_fields", [
    {"interval_hours": "oops"},
    {"interval_hours": -1},
    {"interval_hours": float("nan")},
    {"interval_hours": float("inf")},
    {"last_run_ts": "oops"},
    {"last_run_ts": float("nan")},
])
def test_invalid_interval_does_not_block_later_subscription(monkeypatch, bad_fields):
    saved = []
    fake_app = SimpleNamespace(_save_sites_config=lambda: saved.append(True))
    monkeypatch.setattr(bulk_downloader, "app", fake_app, raising=False)
    monkeypatch.setitem(sys.modules, "bulk_downloader.app", fake_app)
    runner = _Runner([
        {"name": "bad", "url": "https://example.test/bad", **bad_fields},
        {"name": "good", "url": "https://example.test/good", "interval_hours": 1},
    ])
    runner._scan_subscriptions()
    assert runner.seen == ["https://example.test/good"]
    assert saved == [True]


def test_valid_subscription_still_scans(monkeypatch):
    fake_app = SimpleNamespace(_save_sites_config=lambda: None)
    monkeypatch.setattr(bulk_downloader, "app", fake_app, raising=False)
    monkeypatch.setitem(sys.modules, "bulk_downloader.app", fake_app)
    runner = _Runner([{"url": "https://example.test/good", "interval_hours": 1}])
    runner._scan_subscriptions()
    assert runner.seen == ["https://example.test/good"]
