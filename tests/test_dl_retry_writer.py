"""dl-retry-writer: a 429/503 with Retry-After never reached the job.

The scheduler's auto-retry readers (runner_scheduler._auto_retry_scan) take
``status_code`` and ``retry_after_header`` from the job dict and hand them to
retry_policy.classify_failure / compute_next_delay. No writer ever set them:
the transport raised ``_HTTPDownloadFailed(f"HTTP {status}")`` and dropped the
response headers, so a 429 asking for an hour got the ~300 s class default.

The transfer here is real: ``_do_download`` -> ``_http_download`` streams from a
loopback origin that answers 429 (Retry-After: 3600) or 500 (no header). The
browser fallback has no download event, so the job fails. The scheduler half
runs the real ``SchedulerMixin._auto_retry_scan`` on the job the transport left.
"""

from __future__ import annotations

import importlib
import logging
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

BD_GATE_SCOPE = "module"

RETRY_AFTER_S = 3600   # above the rate_limited class base (300 s), so the header is what decides


class _StatusOrigin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    status = 429
    retry_after = None

    def do_GET(self):  # noqa: N802 - stdlib handler API
        body = b"busy"
        self.send_response(self.status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        if self.retry_after is not None:
            self.send_header("Retry-After", self.retry_after)
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, _fmt, *_args):
        return


def _serve(status, retry_after):
    handler = type("_H", (_StatusOrigin,), {"status": status, "retry_after": retry_after})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/members/video/1/scene.mp4"


@pytest.fixture
def origin_429():
    server, url = _serve(429, str(RETRY_AFTER_S))
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture
def origin_500():
    server, url = _serve(500, None)
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from bulk_downloader import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "dlrw_queue.db"))
    db.db_init()


@pytest.fixture
def edges(monkeypatch):
    from bulk_downloader import runner_transport as rt

    monkeypatch.setattr(rt, "gate_candidate_url", lambda locator, page_url, **kw: (locator.href, None))
    monkeypatch.setattr(rt, "db_skip_identity", lambda page_url, final_path: ("different", ""))
    monkeypatch.setattr(rt, "history_title_kwargs", lambda runner, url: {})
    monkeypatch.setattr(rt, "db_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(importlib.import_module("bulk_downloader.hooks"), "fire_event", lambda *a, **k: None)

    class _Slot:
        def release(self):
            return None

    monkeypatch.setattr(importlib.import_module("bulk_downloader.rate_limit"), "acquire", lambda url: _Slot())


class _Ctx:
    def cookies(self):
        return []


class _Locator:
    def __init__(self, href):
        self.href = href

    def get_attribute(self, name):
        return self.href if name == "href" else None

    def click(self):
        return None


class _Page:
    url = "https://site.example/members/video/1"

    def title(self):
        return "Scene"

    def expect_download(self, timeout):
        from bulk_downloader.runner_transport import PWTimeout
        raise PWTimeout(f"synthetic fallback timeout after {timeout}")


def _runner():
    from bulk_downloader import runner_transport as rt

    class _Runner(rt.TransportMixin):
        def __init__(self):
            self.site_id = "dlrw"
            self.config = {
                "name": "dl-retry-writer fixture",
                "filename_template": "{filename}",
                "skip_if_exists": False,
                "use_http_dl": True,
                "use_curl_cffi": False,
                "parallel_chunks": 1,
                "use_ramdisk_stage": False,
                "chunk_size_mb": 1,
                "auto_chunk_size": False,
                "verify_integrity": False,
                "verify_hash": False,
                "max_mbps": 0,
            }
            self.jobs = {}
            self._lock = threading.RLock()
            self._stop = threading.Event()
            self._pause = threading.Event()
            self._pause.set()
            self.log = logging.getLogger("dlrw")
            self.failures = []
            self._recent_completions = deque()
            self._recent_per_min = 0.0

        def _update_job(self, url, status, message, **extra):
            # As SiteRunner._update_job_current: extras merge into the job dict.
            self.jobs.setdefault(url, {}).update({"status": status, "message": message, **extra})

        def _handle_failure(self, url, message, screenshot=""):
            self.failures.append((url, message))
            self._update_job(url, "failed", message)

        def log_event(self, *args, **kwargs):
            return None

        def _screenshot(self, page, url):
            return ""

        def _probe_for_higher_tier(self, file_url, referer=""):
            return file_url

        def _build_mirror_urls(self, file_url):
            return []

        def _pick_fastest_mirror(self, file_url):
            return file_url

        def _download_proxy_url(self):
            return None

        def _current_cap_mbps(self):
            return 0

        def _start_daily_byte_accumulator(self):
            return None

        def _finish_daily_byte_accumulator(self, accumulator):
            return None

    return _Runner()


def _best(href):
    return {"locator": _Locator(href), "score": 1080, "text": "1080p", "size": 0,
            "_via_learned": False, "_all_candidates": []}


def _fail_through_transport(origin, tmp_path, page_url="https://site.example/members/video/1"):
    runner = _runner()
    runner.jobs[page_url] = {"status": "running", "message": "Claimed by worker"}
    runner._do_download(_Page(), _Ctx(), page_url, _best(origin), tmp_path, "1080p")
    assert len(runner.failures) == 1, (runner.failures, runner.jobs)
    assert runner.jobs[page_url]["status"] == "failed", runner.jobs[page_url]
    return runner.jobs[page_url]


class _Nolock:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False


def _scheduler_delay(job, url="https://site.example/members/video/1"):
    """Run the real _auto_retry_scan on the job; return the delay it scheduled."""
    from bulk_downloader.runner_scheduler import SchedulerMixin

    class _Sched(SchedulerMixin):
        def __init__(self):
            self.site_id = "dlrw"
            self.config = {"auto_retry_failed": True, "auto_retry_classify": True}
            self.jobs = {url: dict(job)}
            self._lock = _Nolock()
            self._run_lifecycle_lock = _Nolock()
            self._worker_heartbeats_lock = _Nolock()
            self.log = logging.getLogger("dlrw-sched")

        def _job_status_writer(self):
            class _W:
                def __enter__(s):
                    return lambda: None

                def __exit__(s, *a):
                    return False
            return _W()

        def log_event(self, *a, **k):
            return None

        def _fmt_dur(self, s):
            return f"{int(s)}s"

        def _worker_generation_is_current(self, g):
            return True

    sched = _Sched()
    before = time.time()
    sched._auto_retry_scan()
    after = sched.jobs[url]
    assert after["status"] == "failed", after
    assert float(after.get("next_auto_retry_at", 0) or 0) > 0, f"scan scheduled no retry: {after}"
    return after["next_auto_retry_at"] - before


def test_a_429_with_retry_after_reaches_the_job_and_the_scheduler_waits(origin_429, edges, tmp_path):
    job = _fail_through_transport(origin_429, tmp_path)
    assert job.get("status_code") == 429 and job.get("retry_after_header") == str(RETRY_AFTER_S), (
        f"DLRW_429_RETRY_AFTER_NOT_WRITTEN: {job}")
    delay = _scheduler_delay(job)
    assert delay >= RETRY_AFTER_S, (
        f"DLRW_429_SCHEDULER_IGNORES_RETRY_AFTER: scheduled {delay:.0f}s < Retry-After {RETRY_AFTER_S}s; job={job}")


def test_a_500_without_retry_after_keeps_the_default_backoff(origin_500, edges, tmp_path):
    # Negative control: no header -> nothing to honor; the transient class default applies.
    from bulk_downloader import retry_policy as rp

    job = _fail_through_transport(origin_500, tmp_path)
    assert job.get("status_code") == 500, f"DLRW_500_STATUS_NOT_WRITTEN: {job}"
    assert job.get("retry_after_header") is None, job
    delay = _scheduler_delay(job)
    cfg = rp.get_class_config("transient")
    assert delay < RETRY_AFTER_S, f"DLRW_500_DEFAULT_BACKOFF_CHANGED: {delay:.0f}s; job={job}"
    assert delay <= cfg["max_delay_s"] * 1.5, (delay, cfg)


def test_a_success_retires_the_last_http_failure(tmp_path):
    from bulk_downloader.runner import SiteRunner
    from bulk_downloader.runner_scheduler import SchedulerMixin

    url = "https://site.example/members/video/1"

    class _R(SchedulerMixin):
        def __init__(self):
            self.site_id = "dlrw"
            self.config = {}
            self.jobs = {url: {"status": "running", "message": "x", "status_code": 429,
                               "retry_after_header": "3600"}}
            self._job_progress_samples = {}
            self._worker_current_urls = {}
            self._worker_heartbeats = {}
            self._worker_heartbeats_lock = _Nolock()
            self._run_lifecycle_lock = _Nolock()
            self._lock = _Nolock()
            self.log = logging.getLogger("dlrw-done")

        def _job_status_writer(self):
            class _W:
                def __enter__(s):
                    return lambda: None

                def __exit__(s, *a):
                    return False
            return _W()

        def log_event(self, *a, **k):
            return None

    r = _R()
    SiteRunner._update_job_current(r, url, "done", "Saved: scene.mp4")
    job = r.jobs[url]
    assert job["status"] == "done", job
    assert "status_code" not in job and "retry_after_header" not in job, (
        f"DLRW_DONE_KEEPS_STALE_HTTP_FAILURE: {job}")


def test_a_new_claim_does_not_inherit_the_previous_http_failure():
    from bulk_downloader.runner import SiteRunner

    url = "https://site.example/members/video/1"

    class _R:
        _WORKER_CLAIM_STALE = SiteRunner._WORKER_CLAIM_STALE
        _WORKER_CLAIM_INELIGIBLE = SiteRunner._WORKER_CLAIM_INELIGIBLE

        def __init__(self):
            self.jobs = {url: {"status": "pending", "message": "Auto-retry 1/3",
                               "status_code": 429, "retry_after_header": "3600"}}
            self._worker_heartbeats_lock = _Nolock()
            self._worker_run_generation = 1
            self._worker_current_urls = {}
            self._worker_url_generations = {}

        def _job_status_writer(self):
            class _W:
                def __enter__(s):
                    return lambda: None

                def __exit__(s, *a):
                    return False
            return _W()

    r = _R()
    result, _gen = SiteRunner._claim_worker_item(r, 0, url, None)
    assert result == "claimed", result
    job = r.jobs[url]
    assert "status_code" not in job and "retry_after_header" not in job, (
        f"DLRW_CLAIM_INHERITS_STALE_HTTP_FAILURE: {job}")
