"""dl95-africancasting-1 (O1513, harness-work/UIUX-20260928/download-95/A1-A/FINDINGS-A1-A.md#A1).

Measured on test2 v3.66.1706: add_url(<scene>) -> added 1; start; POST /api/sites/<id>/stop moved the job to "stopped";
start again ran nothing; add_url of the SAME URL -> {"added": 0, "dupes": 1} with no history row for it. The URL could
never be retried through add_url: QueueMixin.load_urls counted ANY URL already in runner.jobs as a duplicate, including a
job that Stop parked in "stopped" and that never downloaded.

Contract after the fix: re-adding a "stopped" URL re-arms that job to "pending" (the transition bulk_resume already
performs), counts it as added and persists it; done/failed/pending/running jobs are still duplicates.
"""

from __future__ import annotations

import logging
import threading

import pytest

import bulk_downloader.content_rights as cr
import bulk_downloader.runner_queue as rq

BD_GATE_SCOPE = "module"

URL = "https://africancasting.example.invalid/bonus/4/african-sex-trip"


class _Runner(rq.QueueMixin):
    def __init__(self):
        self.config = {"download_dir": ""}
        self.jobs = {}
        self.urls = []
        self.site_id = "dl95"
        self._lock = threading.RLock()
        self.log = logging.getLogger("test_dl95")
        self.events = []

    def log_event(self, kind, msg=None, **k):
        self.events.append((kind, msg))


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.setattr(cr, "url_is_blocked", lambda u: None, raising=False)
    persisted = {"upsert": [], "update": []}
    monkeypatch.setattr(
        rq,
        "queue_bulk_upsert",
        lambda site, urls, **k: persisted["upsert"].append(list(urls)),
    )
    monkeypatch.setattr(
        rq,
        "queue_bulk_update",
        lambda site, urls, **k: persisted["update"].append(
            (list(urls), k.get("status"))
        ),
    )
    r = _Runner()
    r.persisted = persisted
    return r


def _stop_after_add(r):
    """What the O1513 run did: add_url -> job pending; site Stop -> "stopped" (runner.stop's transition)."""
    assert r.load_urls([URL])[:2] == (1, 0), "precondition: first add must land"
    r.jobs[URL].update({"status": "stopped", "message": "Stopped", "ts": "x"})


def test_readd_of_stopped_url_rearms_it(runner):
    _stop_after_add(runner)
    added, dupes, _ = runner.load_urls([URL])
    assert (added, dupes) == (1, 0), (
        f"DL95_STOPPED_URL_COUNTED_AS_DUPE: added={added} dupes={dupes}"
    )
    job = runner.jobs[URL]
    assert (
        job["status"] == "pending" and job["retries"] == 0 and job["retry_after"] == 0
    ), job
    assert runner.urls.count(URL) == 1, runner.urls
    assert ([URL], "pending") in runner.persisted["update"], runner.persisted


def test_readd_of_paused_url_rearms_it(runner):
    """bulk_pause parks a job as "stopped" too; an explicit re-add is the operator asking for it again."""
    assert runner.load_urls([URL])[:2] == (1, 0)
    assert runner.bulk_pause([URL]) == 1 and runner.jobs[URL]["status"] == "stopped"
    assert runner.load_urls([URL])[:2] == (1, 0)
    assert runner.jobs[URL]["status"] == "pending" and not runner.jobs[URL].get(
        "_paused_by_user"
    ), runner.jobs[URL]


@pytest.mark.parametrize("status", ["done", "failed", "pending", "running"])
def test_other_states_stay_duplicates(runner, status):
    """Negative control: only a stopped job is re-armed; everything else is still a dupe and untouched."""
    assert runner.load_urls([URL])[:2] == (1, 0)
    runner.jobs[URL]["status"] = status
    before = dict(runner.jobs[URL])
    assert runner.load_urls([URL])[:2] == (0, 1)
    assert runner.jobs[URL] == before
    assert runner.urls.count(URL) == 1


def test_blocked_stopped_url_is_not_rearmed(runner, monkeypatch):
    """The re-add still passes the content-rights gate: a blocklisted stopped URL stays stopped."""
    _stop_after_add(runner)
    monkeypatch.setattr(
        cr,
        "url_is_blocked",
        lambda u: {"blocked": True, "id": 7, "reason": "test"},
        raising=False,
    )
    monkeypatch.setattr(cr, "record_refusal", lambda *a, **k: None, raising=False)
    assert runner.load_urls([URL])[:2] == (0, 1)
    assert runner.jobs[URL]["status"] == "stopped"
