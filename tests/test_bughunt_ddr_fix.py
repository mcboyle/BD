"""Bug-hunt ddr-fix (deep-dl-runner-state sweep, finding F13).

POST /api/discovery/run counted one "enqueued" per load_urls() CALL, so a URL
the queue refused as a duplicate was still reported as newly enqueued.
load_urls() answers (added, dupes, skipped_on_disk); only `added` is new work.

The route is driven through a bare Flask app carrying the discovery blueprint,
with a recording stand-in runner and discover_one replaced by a function that
hands the route's own enqueue callback a fixed URL list. No app boot, no DB.
"""
from __future__ import annotations

import flask
import pytest

from bulk_downloader import app_discovery, discovery

BD_GATE_SCOPE = "module"


class _Runner:
    """load_urls with the real return contract and exact-string dedupe."""

    def __init__(self, fail_on=(), answer=None):
        self.seen: list[str] = []
        self.calls: list[list[str]] = []
        self.fail_on = set(fail_on)
        self.answer = answer

    def load_urls(self, urls, dedupe=True, folder_scan=False):
        self.calls.append(list(urls))
        added = dupes = 0
        for u in urls:
            if u in self.fail_on:
                raise RuntimeError("queue refused")
            if u in self.seen:
                dupes += 1
            else:
                self.seen.append(u)
                added += 1
        if self.answer is not None:
            return self.answer
        return added, dupes, 0


@pytest.fixture
def run(monkeypatch):
    """post(urls, runner, site_known=True) -> the route's JSON for one run."""
    app = flask.Flask(__name__)
    app.register_blueprint(app_discovery.discovery_bp)
    state = {"urls": []}

    def fake_discover_one(site_id, _dcfg, *, enqueue_fn=None):
        return {"site_id": site_id,
                "enqueued": int(enqueue_fn(site_id, list(state["urls"])) or 0)}

    monkeypatch.setattr(app_discovery, "_check_csrf", lambda *a, **k: None)
    monkeypatch.setattr(discovery, "discover_one", fake_discover_one)

    def post(urls, runner, site_known=True):
        state["urls"] = urls
        monkeypatch.setattr(app_discovery, "_app_runners",
                            lambda: {"s1": runner} if site_known else {})
        monkeypatch.setattr(app_discovery, "_app_s_cfg",
                            lambda: {"s1": {"discovery": {}}})
        r = app.test_client().post("/api/discovery/run", json={"site_id": "s1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        return r.get_json()

    return post


def test_f13_duplicates_are_not_counted_as_enqueued(run):
    runner = _Runner()
    out = run(["https://e.test/a", "https://e.test/a", "https://e.test/b"], runner)
    assert out["enqueued"] == 2
    assert runner.seen == ["https://e.test/a", "https://e.test/b"]
    assert len(runner.calls) == 3          # every URL was still offered


def test_f13_second_run_of_the_same_urls_enqueues_nothing(run):
    runner = _Runner()
    urls = ["https://e.test/a", "https://e.test/b"]
    assert run(urls, runner)["enqueued"] == 2
    assert run(urls, runner)["enqueued"] == 0


def test_f13_all_new_urls_count_as_before(run):
    urls = [f"https://e.test/{i}" for i in range(5)]
    assert run(urls, _Runner())["enqueued"] == 5


def test_f13_a_failing_url_does_not_stop_or_count(run):
    runner = _Runner(fail_on={"https://e.test/bad"})
    out = run(["https://e.test/a", "https://e.test/bad", "https://e.test/b"],
              runner)
    assert out["enqueued"] == 2
    assert runner.seen == ["https://e.test/a", "https://e.test/b"]


@pytest.mark.parametrize("answer", [None, (), "x", ("n", 0, 0), (-3, 0, 0), 7])
def test_f13_an_unreadable_answer_counts_nothing(run, answer):
    """A count we cannot read is not a success: 0, and no 500."""

    class Odd(_Runner):
        def load_urls(self, urls, dedupe=True, folder_scan=False):
            return answer

    assert run(["https://e.test/a"], Odd())["enqueued"] == 0


def test_f13_unknown_site_enqueues_nothing(run):
    assert run(["https://e.test/a"], _Runner(), site_known=False)["enqueued"] == 0


def test_f13_safety_cap_still_1000(run):
    runner = _Runner()
    out = run([f"https://e.test/{i}" for i in range(1200)], runner)
    assert out["enqueued"] == 1000
    assert len(runner.calls) == 1000
