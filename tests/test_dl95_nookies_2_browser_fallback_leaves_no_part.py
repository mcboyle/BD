"""dl95-nookies-2 (test2, harness-work/DOT95-LANE/live-tpl95-nookies-1/LIVE-RESULT-B6-B.md, stage*.png):
the HTTP transfer failed mid-stream at 02:37:13Z ("HTTP failed (unexpected: ... curl (92) HTTP/2 stream 1 reset by
server)"). The browser fallback then saved the whole file ("done Saved: ... Nookies.mp4", 3,073,441,256 B), but
the first attempt's ``.part`` (1,077,943,841 B), its ``.part.meta`` and its ``.part.owner`` claim stayed beside it.

Why: a failed HTTP transfer keeps its ``.part`` and claim for a resume, which is correct. After ``_pw_save`` the
browser arm called ``staging_claim.release``, and release keeps a claim while bytes remain (row 489). Nothing
ever deleted the bytes the finished file superseded.

The transfer here is real. ``_http_download`` streams from a loopback origin that promises the whole body and
closes the connection halfway, on every attempt, including the resumes. ``BrowserMixin._pw_save`` is also real
and writes through a download double's ``save_as``. Only the page/locator and the history/db edges are stand-ins.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

BD_GATE_SCOPE = "module"

CHUNK = 1024 * 1024
BODY = bytes(range(256)) * (16 * 1024)    # 4 MiB: half of it is two whole 1 MiB chunks written to the .part
ETAG = '"nk2-etag"'                      # a validator, so the transport writes .part.meta
BROWSER_BYTES = b"browser-saved-whole-file"


class _ResettingOrigin(BaseHTTPRequestHandler):
    """Promises the rest of the body, sends half of it, then drops the connection."""

    protocol_version = "HTTP/1.1"
    requests: list = []

    def do_GET(self):  # noqa: N802 - stdlib handler API
        start = 0
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            start = int(rng[len("bytes="):].split("-")[0])
        payload = BODY[start:]
        self.requests.append({"path": urlsplit(self.path).path, "range": rng})
        self.send_response(206 if start else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", ETAG)
        self.send_header("Accept-Ranges", "bytes")
        if start:
            self.send_header("Content-Range", f"bytes {start}-{len(BODY) - 1}/{len(BODY)}")
        self.end_headers()
        self.wfile.write(payload[: max(1, len(payload) // 2)])
        self.wfile.flush()
        self.close_connection = True

    def log_message(self, _fmt, *_args):
        return


@pytest.fixture
def origin():
    _ResettingOrigin.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ResettingOrigin)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/members/video/3503/scene.mp4"
    finally:
        server.shutdown()
        server.server_close()


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


class _Download:
    suggested_filename = "scene.mp4"

    def __init__(self, url):
        self.url = url

    def save_as(self, path):
        Path(path).write_bytes(BROWSER_BYTES)


class _Page:
    """The browser fallback's download event. It records what was staged on disk when it fired."""

    def __init__(self, download_dir, fallback_works=True):
        self.url = "https://nookies.example/membersarea/video/3503"
        self.download_dir = download_dir
        self.fallback_works = fallback_works
        self.at_fallback = []

    def title(self):
        return "Scene"

    def expect_download(self, timeout):
        page = self

        class _Expect:
            def __enter__(self):
                d = page.download_dir
                page.at_fallback.append({
                    "part_bytes": sum(p.stat().st_size for p in d.glob("*.part")),
                    "meta": len(list(d.glob("*.part.meta"))),
                    "owner": len(list(d.glob("*.part.owner"))),
                })
                if not page.fallback_works:
                    from bulk_downloader.runner_transport import PWTimeout
                    raise PWTimeout(f"synthetic fallback timeout after {timeout}")
                self.value = _Download("https://nookies.example/dl/scene.mp4")
                return self

            def __exit__(self, *exc):
                return False

        return _Expect()


def _runner(use_http_dl=True):
    from bulk_downloader import runner_transport as rt
    from bulk_downloader.runner_browser import BrowserMixin

    class _Runner(rt.TransportMixin):
        def __init__(self):
            self.site_id = "nk2"
            self.config = {
                "name": "Nookies fixture",
                "filename_template": "{filename}",
                "skip_if_exists": False,
                "use_http_dl": use_http_dl,
                "use_curl_cffi": False,
                "parallel_chunks": 1,
                "use_ramdisk_stage": False,
                "chunk_size_mb": CHUNK // (1024 * 1024),
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
            self.log = logging.getLogger("nk2")
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

        def _pw_save(self, dl, final_path):
            return BrowserMixin._pw_save(self, dl, final_path)

        def _embed_metadata_if_mp4(self, *args, **kwargs):
            return None

        def _size_on_disk_after_tagging(self, final_path, fallback):
            return Path(final_path).stat().st_size

    return _Runner()


@pytest.fixture
def edges(monkeypatch):
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
    return {"locator": _Locator(href), "score": 1080, "text": "1080p", "size": 0,
            "_via_learned": False, "_all_candidates": []}


def _staged(d):
    return sorted(p.name for p in d.iterdir() if p.name.endswith((".part", ".meta", ".owner", ".json")))


def test_a_browser_fallback_after_a_reset_leaves_no_part_meta_or_claim(origin, edges, tmp_path):
    runner = _runner()
    page = _Page(tmp_path)
    page_url = "https://nookies.example/membersarea/video/3503"

    runner._do_download(page, _Ctx(), page_url, _best(origin), tmp_path, "1080p")

    # Preconditions, measured, not assumed. The HTTP attempt really failed mid-body and was retried from its
    # own .part (Range). When the browser fallback fired, the .part held bytes and its .meta and claim existed:
    # the live state at 02:37:13Z.
    assert any(r["range"] for r in _ResettingOrigin.requests), _ResettingOrigin.requests
    assert len(page.at_fallback) == 1, "precondition: the browser fallback never fired"
    snap = page.at_fallback[0]
    assert snap["part_bytes"] > 0 and snap["meta"] == 1 and snap["owner"] == 1, snap
    assert any("retrying via browser" in m for _u, _s, m in runner.job_updates), runner.job_updates

    final = tmp_path / "scene.mp4"
    assert final.read_bytes() == BROWSER_BYTES
    assert [s for _u, s, _m in runner.job_updates].count("done") == 1, runner.job_updates
    assert _staged(tmp_path) == [], (
        "DL95-NOOKIES-2: the browser fallback saved the whole file but the failed HTTP attempt's staging "
        f"was left beside it: {_staged(tmp_path)} (snapshot at fallback {snap})")


def test_a_failed_browser_fallback_still_keeps_the_part_for_resume(origin, edges, tmp_path):
    # Negative control: the staged bytes are only superseded once the browser has saved the file.
    from bulk_downloader import staging_claim as sc

    runner = _runner()
    page = _Page(tmp_path, fallback_works=False)
    page_url = "https://nookies.example/membersarea/video/3503"

    runner._do_download(page, _Ctx(), page_url, _best(origin), tmp_path, "1080p")

    assert len(runner.failures) == 1 and "no fallback download event" in runner.failures[0][1]
    staging = sc.staging_path_for(tmp_path / "scene.mp4")
    assert staging.stat().st_size > 0
    assert sc._read_owner_identity(sc.owner_path_for(staging)) == sc.job_identity(page_url)
    sc.release(staging, force=True)


def test_a_browser_only_retry_clears_its_own_earlier_part(edges, tmp_path):
    # Suppose an earlier run of this job left its own claimed .part, and this run goes straight to the
    # browser. The saved file supersedes that .part the same way.
    from bulk_downloader import staging_claim as sc

    page_url = "https://nookies.example/membersarea/video/3503"
    final = tmp_path / "scene.mp4"
    identity = sc.job_identity(page_url)
    staging = sc.claim(final, identity)
    staging.write_bytes(b"x" * 4096)
    staging.with_suffix(staging.suffix + ".meta").write_text(json.dumps({"etag": ETAG}))
    assert _staged(tmp_path) == ["scene.mp4.part", "scene.mp4.part.meta", "scene.mp4.part.owner"]

    runner = _runner(use_http_dl=False)
    page = _Page(tmp_path)
    # Not a media href, so the transport clicks and takes the browser's download event (no direct fetch).
    best = _best("https://nookies.example/membersarea/download/3503")

    class _ClickDownloads:
        def __enter__(self_inner):
            self_inner.value = _Download("https://nookies.example/dl/scene.mp4")
            return self_inner

        def __exit__(self_inner, *exc):
            return False

    page.expect_download = lambda timeout: _ClickDownloads()
    runner._do_download(page, _Ctx(), page_url, best, tmp_path, "1080p")

    assert final.read_bytes() == BROWSER_BYTES
    assert _staged(tmp_path) == [], _staged(tmp_path)


def test_a_rejected_browser_file_after_a_reset_keeps_the_part_for_resume(origin, edges, tmp_path):
    # Lens REFUTE R1 (.review/VERDICT-correctness-bd-cx-worker-1.md). The browser's file must pass acceptance
    # before it supersedes the staged bytes. Here the page advertised 4 MiB and the browser saved 24 B, so
    # size sanity quarantines it and fails the job. The HTTP attempt's .part must survive for the next resume.
    from bulk_downloader import staging_claim as sc

    runner = _runner()
    page = _Page(tmp_path)
    page_url = "https://nookies.example/membersarea/video/3503"
    best = _best(origin)
    best["size"] = len(BODY)

    runner._do_download(page, _Ctx(), page_url, best, tmp_path, "1080p")

    assert len(page.at_fallback) == 1 and page.at_fallback[0]["part_bytes"] > 0, page.at_fallback
    assert (tmp_path / "_failed" / "scene.mp4").read_bytes() == BROWSER_BYTES, "precondition: size sanity did not reject"
    assert [s for _u, s, _m in runner.job_updates][-1] == "failed", runner.job_updates
    staging = sc.staging_path_for(tmp_path / "scene.mp4")
    assert staging.stat().st_size == page.at_fallback[0]["part_bytes"], (
        "DL95-NOOKIES-2: a REJECTED browser file destroyed the job's resumable .part")
    assert sc._read_owner_identity(sc.owner_path_for(staging)) == sc.job_identity(page_url)
    sc.release(staging, force=True)


def test_a_rejected_browser_only_file_keeps_the_jobs_earlier_part(edges, tmp_path):
    from bulk_downloader import staging_claim as sc

    page_url = "https://nookies.example/membersarea/video/3503"
    final = tmp_path / "scene.mp4"
    identity = sc.job_identity(page_url)
    staging = sc.claim(final, identity)
    staging.write_bytes(b"x" * 4096)

    runner = _runner(use_http_dl=False)
    page = _Page(tmp_path)
    best = _best("https://nookies.example/membersarea/download/3503")
    best["size"] = len(BODY)

    runner._do_download(page, _Ctx(), page_url, best, tmp_path, "1080p")

    assert (tmp_path / "_failed" / "scene.mp4").read_bytes() == BROWSER_BYTES, "precondition: size sanity did not reject"
    assert staging.read_bytes() == b"x" * 4096, "DL95-NOOKIES-2: a REJECTED browser file destroyed the job's .part"
    assert sc._read_owner_identity(sc.owner_path_for(staging)) == identity
    sc.release(staging, force=True)


def test_another_jobs_part_is_never_discarded(tmp_path):
    # Identity gate: bytes a different job claimed are not this job's to delete, finished file or not.
    from bulk_downloader import staging_claim as sc

    final = tmp_path / "scene.mp4"
    other = sc.job_identity("https://nookies.example/membersarea/video/9999")
    staging = sc.claim(final, other)
    staging.write_bytes(b"y" * 4096)

    assert sc.discard(final, sc.job_identity("https://nookies.example/membersarea/video/3503")) is False
    assert staging.read_bytes() == b"y" * 4096
    assert sc._read_owner_identity(sc.owner_path_for(staging)) == other
    # The owner discards all of it, including the parallel transfer's checkpoint sidecar.
    from bulk_downloader import resume

    staging.with_suffix(staging.suffix + ".meta").write_text("{}")
    resume.sidecar_path(final).write_text("{}")
    assert len(_staged(tmp_path)) == 4, _staged(tmp_path)
    assert sc.discard(final, other) is True
    assert _staged(tmp_path) == []
