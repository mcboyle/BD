"""dl95-wowgirls-1 D1: one media resource belongs to one job of a run.

MEASURED on test2 2026-09-29 (download-95/A5-A/RESULT-wowgirls.md heading D1, _passbar/pb/PB__WG-site.png,
PASSBAR-bytes-0055Z.txt): the site queued two films, /film/ba91fb1f/her-deepest-needs and
/film/fc43abef/can-t-be-hotter-than-this. Both jobs staged BellaSpark_HerDeepestNeeds_7680x4320_60fps[_1].mp4.part:
two .part.owner records with different job ids but the SAME resource identity, and both .part.meta carrying the
same ETag. The second film's job was downloading the first film's media under a ``_1`` name. ``staging_claim.reserve``
only asks whether a NAME is free, so a second job resolving an already-owned resource just moved to ``X_1``.

Fix: the transport binds each resolved media resource to the job (page) that first resolved it in this run, and a
different job that resolves the same resource ends needs_review before any byte is fetched or any name reserved.
The origin is a loopback server; every other host is ``.example``. No byte leaves the test.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

BODY = bytes(range(256)) * 1024          # 256 KiB
ETAG = '"6aa68a6e-18bd8abf8"'
FILM_A = "https://venus.wowgirls.example/film/ba91fb1f/her-deepest-needs"
FILM_B = "https://venus.wowgirls.example/film/fc43abef/can-t-be-hotter-than-this"
NAME_A = "BellaSpark_HerDeepestNeeds_7680x4320_60fps.mp4"
NAME_B = "Someone_CantBeHotterThanThis_7680x4320_60fps.mp4"


class _Origin(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list = []

    def do_GET(self):  # noqa: N802 - stdlib handler API
        self.requests.append(urlsplit(self.path).path)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("ETag", ETAG)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture
def origin():
    _Origin.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Origin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _media(origin, film_id, name, sig="s1"):
    return f"{origin}/download/{film_id}/7680x4320_60FPS.mp4?filename={name}&sig={sig}"


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
    def __init__(self, url):
        self.url = url

    def title(self):
        return "Wow Girls"

    def expect_download(self, timeout):
        raise AssertionError("the direct-URL path must not wait on a browser download")


def _runner():
    from bulk_downloader import runner_transport as rt

    class _Runner(rt.TransportMixin):
        def __init__(self):
            self.site_id = "ddcde6ee"
            self.config = {
                "name": "wowgirls fixture",
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
            self.log = logging.getLogger("wg1")
            self.job_updates = []
            self.failures = []
            self._recent_completions = deque()
            self._recent_per_min = 0.0

        def _update_job(self, url, status, message, **extra):
            self.job_updates.append((url, status, message))

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

        def _embed_metadata_if_mp4(self, *args, **kwargs):
            return None

        def _size_on_disk_after_tagging(self, final_path, fallback):
            return Path(final_path).stat().st_size

    return _Runner()


@pytest.fixture
def edges(monkeypatch, clean_workdir):
    # clean_workdir: GEN 3 reads the site's download history; it must be this test's own.
    import importlib

    from bulk_downloader import runner_transport as rt

    logs = []
    monkeypatch.setattr(rt, "gate_candidate_url", lambda locator, page_url, **kw: (locator.href, None))
    monkeypatch.setattr(rt, "db_skip_identity", lambda page_url, final_path: ("different", ""))
    monkeypatch.setattr(rt, "history_title_kwargs", lambda runner, url: {})
    monkeypatch.setattr(rt, "db_log", lambda *args, **kwargs: logs.append(args))
    monkeypatch.setattr(importlib.import_module("bulk_downloader.hooks"), "fire_event", lambda *a, **k: None)

    class _Slot:
        def release(self):
            return None

    monkeypatch.setattr(importlib.import_module("bulk_downloader.rate_limit"), "acquire", lambda url: _Slot())
    return logs


def _best(href):
    return {"locator": _Locator(href), "score": 4320, "text": "8K", "size": 0,
            "_via_learned": True, "_all_candidates": []}


def _statuses(runner, url):
    return [(s, m) for u, s, m in runner.job_updates if u == url]


def _files(d):
    # Media and staging files only; the history/hash databases may live in the same tmp dir.
    return sorted(p.name for p in d.iterdir() if p.is_file() and ".mp4" in p.name)


def test_a_second_film_resolving_the_first_films_media_is_refused(origin, edges, tmp_path):
    runner = _runner()
    runner._do_download(_Page(FILM_A), _Ctx(), FILM_A, _best(_media(origin, "ba91fb1f", NAME_A, "a")),
                        tmp_path, "8K")
    assert [s for s, _m in _statuses(runner, FILM_A)][-1] == "done", runner.job_updates
    assert (tmp_path / NAME_A).read_bytes() == BODY
    fetched_for_a = len(_Origin.requests)

    # Film B's page resolved Film A's media: same host and path, a freshly signed query.
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "ba91fb1f", NAME_A, "b")),
                        tmp_path, "8K")

    b = _statuses(runner, FILM_B)
    assert b and b[-1][0] == "needs_review" and "already owned by another job" in b[-1][1], (
        "DL95-WOWGIRLS-1 SAME-RESOURCE-TWO-JOBS: film fc43abef was allowed to download film ba91fb1f's media; "
        f"job updates={b} files={_files(tmp_path)}")
    assert FILM_A in b[-1][1], b[-1][1]
    assert len(_Origin.requests) == fetched_for_a, "the refused job fetched bytes"
    assert _files(tmp_path) == [NAME_A], _files(tmp_path)


def test_a_film_resolving_its_own_media_downloads_beside_another(origin, edges, tmp_path):
    # Negative control: two films, two resources -- both download.
    runner = _runner()
    runner._do_download(_Page(FILM_A), _Ctx(), FILM_A, _best(_media(origin, "ba91fb1f", NAME_A)), tmp_path, "8K")
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "fc43abef", NAME_B)), tmp_path, "8K")

    assert [s for s, _m in _statuses(runner, FILM_A)][-1] == "done", runner.job_updates
    assert [s for s, _m in _statuses(runner, FILM_B)][-1] == "done", runner.job_updates
    assert (tmp_path / NAME_B).read_bytes() == BODY


def test_the_same_job_may_resolve_its_resource_again(origin, edges, tmp_path):
    # A retry of the owning job is not "another job".
    runner = _runner()
    href = _media(origin, "ba91fb1f", NAME_A)
    runner._do_download(_Page(FILM_A), _Ctx(), FILM_A, _best(href), tmp_path, "8K")
    runner._do_download(_Page(FILM_A), _Ctx(), FILM_A, _best(href), tmp_path, "8K")

    statuses = [s for s, _m in _statuses(runner, FILM_A)]
    assert "needs_review" not in statuses and statuses[-1] == "done", runner.job_updates


def test_the_run_registry_binds_path_identity_not_the_signature():
    from bulk_downloader import staging_claim as sc

    owners = sc.MediaResourceOwners()
    assert owners.bind("https://cdn.example/download/ba91fb1f/8k.mp4?sig=1", FILM_A) is None
    assert owners.bind("https://cdn.example/download/ba91fb1f/8k.mp4?sig=2", FILM_A) is None
    assert owners.bind("https://CDN.example/download/ba91fb1f/8k.mp4?sig=3", FILM_B) == FILM_A
    assert owners.bind("https://cdn.example/download/fc43abef/8k.mp4?sig=3", FILM_B) is None
    assert owners.bind("https://cdn2.example/download/ba91fb1f/8k.mp4", FILM_B) is None
    # A path that is not a media file is named by its query: two ids are two objects.
    assert owners.bind("https://site.example/download.php?id=1", FILM_A) is None
    assert owners.bind("https://site.example/download.php?id=2", FILM_B) is None
    assert owners.bind("https://site.example/download.php?id=1", FILM_B) == FILM_A


# ── GEN 2 (pre-lens REFUTE): ownership is per PAGE, not per first resolver ──


def test_a_skipped_owner_still_owns_its_media(origin, edges, tmp_path, monkeypatch):
    # Run 2 of the same queue: film A is already on disk ("Already have"),
    # then film B resolves film A's media again. The skip must still bind.
    from bulk_downloader import runner_transport as rt

    runner = _runner()
    runner.config["skip_if_exists"] = True
    (tmp_path / NAME_A).write_bytes(BODY)
    monkeypatch.setattr(rt, "db_skip_identity", lambda page_url, final_path: (
        ("same", str(tmp_path / NAME_A)) if page_url == FILM_A else ("different", "")))
    runner._do_download(_Page(FILM_A), _Ctx(), FILM_A, _best(_media(origin, "ba91fb1f", NAME_A, "a")),
                        tmp_path, "8K")
    assert _statuses(runner, FILM_A)[-1][0] == "done", runner.job_updates
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "ba91fb1f", NAME_A, "b")),
                        tmp_path, "8K")

    b = _statuses(runner, FILM_B)
    assert b and b[-1][0] == "needs_review", (
        "DL95-WOWGIRLS-1 SKIPPED-OWNER-NOT-BOUND: film A was skipped as Already have, so film B "
        f"saved film A's media: job updates={b} files={_files(tmp_path)}")
    assert _files(tmp_path) == [NAME_A] and not _Origin.requests, (_files(tmp_path), _Origin.requests)


def test_the_page_the_media_names_owns_it_in_either_order(origin, edges, tmp_path):
    # Parallel workers: film B's job reaches the transfer FIRST. Both films are
    # queued; the media url names film A's id, so it is film A's.
    runner = _runner()
    runner.jobs = {FILM_A: {"status": "running"}, FILM_B: {"status": "running"}}
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "ba91fb1f", NAME_A, "b")),
                        tmp_path, "8K")
    runner._do_download(_Page(FILM_A), _Ctx(), FILM_A, _best(_media(origin, "ba91fb1f", NAME_A, "a")),
                        tmp_path, "8K")

    a = _statuses(runner, FILM_A)
    b = _statuses(runner, FILM_B)
    assert b and b[-1][0] == "needs_review" and FILM_A in b[-1][1], (
        "DL95-WOWGIRLS-1 FIRST-RESOLVER-WINS: film fc43abef reached the transfer first and kept film "
        f"ba91fb1f's media: B={b[-1:]} A={a[-1:]} files={_files(tmp_path)}")
    assert a and a[-1][0] == "done", a
    assert _files(tmp_path) == [NAME_A], _files(tmp_path)


def test_run_two_other_film_first_with_owner_only_on_disk(origin, edges, tmp_path, monkeypatch):
    # A fresh runner (new run, empty registry): film B is processed before film
    # A, whose file is on disk from the earlier run. The queue names the owner.
    from bulk_downloader import runner_transport as rt

    runner = _runner()
    runner.config["skip_if_exists"] = True
    runner.jobs = {FILM_A: {"status": "pending"}, FILM_B: {"status": "running"}}
    (tmp_path / NAME_A).write_bytes(BODY)
    monkeypatch.setattr(rt, "db_skip_identity", lambda page_url, final_path: (
        ("same", str(tmp_path / NAME_A)) if page_url == FILM_A else ("different", "")))
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "ba91fb1f", NAME_A, "b")),
                        tmp_path, "8K")

    b = _statuses(runner, FILM_B)
    assert b and b[-1][0] == "needs_review", (
        "DL95-WOWGIRLS-1 RUN-TWO-NO-MEMORY: a new run's registry was empty and film B saved film A's "
        f"media again: job updates={b} files={_files(tmp_path)}")
    assert _files(tmp_path) == [NAME_A] and not _Origin.requests, (_files(tmp_path), _Origin.requests)


def test_per_page_resolution_rules():
    from bulk_downloader import staging_claim as sc

    media_a = "https://cdn.example/download/ba91fb1f/7680x4320_60FPS.mp4?filename=x&sig=1"
    # Rule 2: the media names another queued page -- refused, even as the first resolver.
    owners = sc.MediaResourceOwners()
    assert owners.bind(media_a, FILM_B, [FILM_A, FILM_B]) == FILM_A
    # Rule 1: the media names this page -- it is this job's, even after another job bound it.
    owners = sc.MediaResourceOwners()
    assert owners.bind(media_a, FILM_B) is None          # no id evidence, no queue: first resolver
    assert owners.bind(media_a, FILM_A) is None, "DL95-WOWGIRLS-1 AFFINITY-LOST: the named page was refused"
    assert owners.bind(media_a, FILM_B) == FILM_A
    # Negative controls: no page id in the media, no id-like page segment.
    owners = sc.MediaResourceOwners()
    assert owners.bind("https://cdn.example/v/7680x4320.mp4?t=1", FILM_B, [FILM_A, FILM_B]) is None
    assert sc.page_id_tokens("https://x.example/video/2024/her-deepest-needs") == frozenset()
    assert sc.page_id_tokens(FILM_A) == frozenset({"ba91fb1f"})


# GEN 3 (A10-A REFUTE R1, 14:56Z): the owner left the queue (clear_completed,
# queue replace, restart) but its history row -- written by db_log when it
# finished -- still names it. The media names that page's id: refused.

def _history_row(site_id, page_url, filename):
    from bulk_downloader import db

    db.db_init()
    db.db_log(site_id, "wowgirls fixture", page_url, "done", filename, len(BODY), "Saved: " + filename)


def test_owner_done_and_cleared_from_the_queue_still_owns_its_media(origin, edges, tmp_path):
    runner = _runner()
    runner.jobs = {FILM_B: {"status": "running"}}           # film A finished earlier and was cleared
    _history_row(runner.site_id, FILM_A, NAME_A)
    (tmp_path / NAME_A).write_bytes(BODY)
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "ba91fb1f", NAME_A, "b")),
                        tmp_path, "8K")

    b = _statuses(runner, FILM_B)
    assert b and b[-1][0] == "needs_review" and FILM_A in b[-1][1], (
        "DL95-WOWGIRLS-1 OWNER-NOT-QUEUED: film fc43abef saved film ba91fb1f's media though history names "
        f"ba91fb1f's page: job updates={b[-1:]} files={_files(tmp_path)} fetched={len(_Origin.requests)}")
    assert _files(tmp_path) == [NAME_A] and not _Origin.requests, (_files(tmp_path), _Origin.requests)


def test_history_of_another_site_does_not_claim_the_media(origin, edges, tmp_path):
    """Control: ownership evidence is this SITE's history; another site's row is not evidence."""
    runner = _runner()
    runner.jobs = {FILM_B: {"status": "running"}}
    _history_row("another-site", FILM_A, NAME_A)
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "ba91fb1f", NAME_A, "b")),
                        tmp_path, "8K")
    assert [s for s, _m in _statuses(runner, FILM_B)][-1] == "done", runner.job_updates


def test_history_naming_this_page_keeps_its_own_media(origin, edges, tmp_path):
    """Control: film B's own history row and film A's row do not refuse B's own media."""
    runner = _runner()
    runner.jobs = {FILM_B: {"status": "running"}}
    _history_row(runner.site_id, FILM_A, NAME_A)
    _history_row(runner.site_id, FILM_B, NAME_B)
    runner._do_download(_Page(FILM_B), _Ctx(), FILM_B, _best(_media(origin, "fc43abef", NAME_B, "b")),
                        tmp_path, "8K")
    assert [s for s, _m in _statuses(runner, FILM_B)][-1] == "done", runner.job_updates


def test_media_id_candidates_are_the_other_pages_ids():
    from bulk_downloader import staging_claim as sc

    media_a = "https://cdn.example/download/ba91fb1f/7680x4320_60FPS.mp4?filename=x&sig=1"
    got = sc.media_id_candidates(media_a, FILM_B)
    assert "ba91fb1f" in got and "fc43abef" not in got and "download" not in got, got
    assert sc.media_id_candidates(media_a, FILM_A) == [t for t in got if t != "ba91fb1f"]
