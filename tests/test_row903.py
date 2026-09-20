"""Row 903: RESILIENT-BYTE-RANGE-TRANSPORT-RESUMPTION.

THE DEFECT (register row EVIDENCE): a transient network disconnect during a
multi-gigabyte media download forces the whole file to restart from byte 0.

The byte-offset/Range resume machinery ALREADY EXISTS in
`_http_download_claimed` (`.part` file, `resume_from = tmp_path.stat().st_size`,
`Range: bytes=<offset>-`, If-Range validators, 416 completeness proof) --
that plumbing is correct and this row does not change it (see the
integration test below, which passes against it unmodified).

The actual bug is one level up, in `_do_download`'s HTTP arm: on ANY
`_HTTPDownloadFailed` it moved straight to the NEXT entry in `attempt_urls`
(mirrors). With the common single-URL config (no mirrors), that is exactly
one attempt before falling through to the Playwright browser save path,
which does not touch the `.part`/resume machinery at all -- so a mid-stream
disconnect on a large file restarted from byte 0, matching the register
row's measured evidence. A mirror swap is a DIFFERENT url with no `.part`
to resume, so moving on immediately there is correct and unchanged.

THE FIX: `_run_http_attempts_with_resume` (new) retries a TRANSIENT failure
on the SAME url (bounded) before advancing to the next mirror, so the
existing resume machinery actually gets exercised instead of being
short-circuited by the mirror loop's one-shot-then-give-up behavior.

RED-first: `_run_http_attempts_with_resume` and `_is_transient_download_error`
do not exist on the base tree; every test below imports/calls them and fails
(ImportError at collection, or AttributeError on call) until the fix lands.
"""
from __future__ import annotations

import contextlib
import hashlib
import threading
from pathlib import Path

import httpx
import pytest

from bulk_downloader.runner_transport import (
    TransportMixin, _HTTPDownloadFailed, _is_transient_download_error,
)
from bulk_downloader import staging_claim

BD_GATE_SCOPE = "module"


# ── _is_transient_download_error: pure unit tests ────────────────────────────

@pytest.mark.parametrize("msg", [
    "http error: ReadTimeout",
    "unexpected: [Errno 104] Connection reset by peer",
    "http error: RemoteProtocolError: peer closed connection without "
    "sending complete message (peer closed connection without ...)",
    "unexpected: ConnectionResetError: forcibly closed",
    "http error: connection aborted.",
    "unexpected: EOF occurred in violation of protocol",
])
def test_transient_messages_are_recognized(msg):
    assert _is_transient_download_error(_HTTPDownloadFailed(msg)) is True


@pytest.mark.parametrize("msg", [
    "HTTP 404",
    "stopped",
    "rate limit exceeded, backing off",
    "http error: SSLCertVerificationError",
    "HTTP 416 with no resume position",
])
def test_non_transient_messages_are_not_recognized(msg):
    assert _is_transient_download_error(_HTTPDownloadFailed(msg)) is False


# ── _run_http_attempts_with_resume: retry-loop decision unit tests ──────────

class _FakeHost(TransportMixin):
    """Minimal TransportMixin host: only the attributes/methods the two
    methods under test touch, so a failure here is never masked by an
    unrelated SiteRunner dependency."""

    def __init__(self):
        self.config = {}
        self.site_id = "row903_test"
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self.updates = []
        self.events = []

    def _update_job(self, *a, **kw):
        self.updates.append((a, kw))

    def log_event(self, *a, **kw):
        self.events.append((a, kw))

    def _extract_host(self, url):
        return url

    def _download_proxy_url(self):
        return None


def test_transient_failure_retries_same_url_then_succeeds():
    host = _FakeHost()
    calls = []

    def fake_http_download(page_url, page, ctx, attempt_url, final_path, **kw):
        calls.append(attempt_url)
        if len(calls) == 1:
            raise _HTTPDownloadFailed("http error: ReadTimeout")
        return (100, 100)

    host._http_download = fake_http_download
    result = host._run_http_attempts_with_resume(
        "page", None, None, "https://a.test/f.bin",
        ["https://a.test/f.bin"], Path("/tmp/row903_f.bin"))
    assert result == (100, 100)
    assert calls == ["https://a.test/f.bin", "https://a.test/f.bin"]


def test_non_transient_failure_advances_to_next_mirror_without_retry():
    host = _FakeHost()
    calls = []

    def fake_http_download(page_url, page, ctx, attempt_url, final_path, **kw):
        calls.append(attempt_url)
        if attempt_url == "https://a.test/f.bin":
            raise _HTTPDownloadFailed("HTTP 404")
        return (50, 50)

    host._http_download = fake_http_download
    result = host._run_http_attempts_with_resume(
        "page", None, None, "https://a.test/f.bin",
        ["https://a.test/f.bin", "https://b.test/f.bin"],
        Path("/tmp/row903_f.bin"))
    assert result == (50, 50)
    assert calls == ["https://a.test/f.bin", "https://b.test/f.bin"]


def test_stopped_reraises_immediately_without_retry():
    host = _FakeHost()
    calls = []

    def fake_http_download(*a, **kw):
        calls.append(1)
        raise _HTTPDownloadFailed("stopped")

    host._http_download = fake_http_download
    with pytest.raises(_HTTPDownloadFailed):
        host._run_http_attempts_with_resume(
            "page", None, None, "https://a.test/f.bin",
            ["https://a.test/f.bin", "https://b.test/f.bin"],
            Path("/tmp/row903_f.bin"))
    assert len(calls) == 1


def test_rate_limit_reraises_immediately_without_retry():
    host = _FakeHost()
    calls = []

    def fake_http_download(*a, **kw):
        calls.append(1)
        raise _HTTPDownloadFailed("rate limited by domain policy")

    host._http_download = fake_http_download
    with pytest.raises(_HTTPDownloadFailed):
        host._run_http_attempts_with_resume(
            "page", None, None, "https://a.test/f.bin",
            ["https://a.test/f.bin"], Path("/tmp/row903_f.bin"))
    assert len(calls) == 1


def test_transient_retries_are_bounded_then_raises_last_error():
    host = _FakeHost()
    calls = []

    def fake_http_download(*a, **kw):
        calls.append(1)
        raise _HTTPDownloadFailed("http error: ReadTimeout")

    host._http_download = fake_http_download
    with pytest.raises(_HTTPDownloadFailed):
        host._run_http_attempts_with_resume(
            "page", None, None, "https://a.test/f.bin",
            ["https://a.test/f.bin"], Path("/tmp/row903_f.bin"),
            max_transient_retries=2)
    assert len(calls) == 3  # 1 initial attempt + 2 retries


def test_negative_control_zero_retries_gives_up_after_one_attempt():
    """Documents the ORIGINAL defect shape: with no transient retry
    (max_transient_retries=0) a single-URL config gives up on the very
    first transient failure -- exactly what used to fall straight through
    to the Playwright browser fallback and lose the resume state."""
    host = _FakeHost()
    calls = []

    def fake_http_download(*a, **kw):
        calls.append(1)
        raise _HTTPDownloadFailed("http error: ReadTimeout")

    host._http_download = fake_http_download
    with pytest.raises(_HTTPDownloadFailed):
        host._run_http_attempts_with_resume(
            "page", None, None, "https://a.test/f.bin",
            ["https://a.test/f.bin"], Path("/tmp/row903_f.bin"),
            max_transient_retries=0)
    assert len(calls) == 1


# ── acceptance-level integration: real resume plumbing, real bytes ─────────
#
# Exercises `_http_download_claimed` directly (unmodified by this row) to
# prove the three register-row acceptance points end to end: (1) a retry
# after a simulated disconnect resumes from the last byte offset, (2) the
# final file has zero duplicated bytes, (3) its checksum matches the
# original payload exactly. The wire transport is faked (no real sockets);
# every other code path (staging claim, .part resume, Range header, 206
# handling, promotion) is the real production code.

class _FakeResponse:
    def __init__(self, status_code, headers):
        self.status_code = status_code
        self.headers = httpx.Headers(headers)
        self.iter_bytes = None  # set by the caller


def _chunked(data, size=8192):
    return [data[i:i + size] for i in range(0, len(data), size)]


class _FakeCtx:
    """Stand-in for a Playwright BrowserContext: `_http_download_claimed`
    only ever reads `.cookies()` from it."""

    def cookies(self):
        return []


def test_resume_after_disconnect_has_no_duplicate_bytes_and_verified_checksum(
        tmp_path, monkeypatch):
    payload = (hashlib.sha256(b"row903-seed").digest()) * 4000  # 128,000 B
    expected_checksum = hashlib.sha256(payload).hexdigest()
    cut_at = 40000  # simulated mid-stream disconnect offset

    host = _FakeHost()
    host.config["use_curl_cffi"] = False  # force the httpx path (testable)
    final_path = tmp_path / "movie.mp4"
    file_url = "https://cdn.example.test/media/movie.mp4"
    page_url = "https://site.example.test/scene/1"

    call_n = {"n": 0}

    @contextlib.contextmanager
    def fake_owning_stream(client, method, url, **kwargs):
        call_n["n"] += 1
        headers = kwargs.get("headers") or {}
        rng = headers.get("Range")
        if call_n["n"] == 1:
            assert rng is None, "first attempt must not send a Range header"
            resp = _FakeResponse(200, {"Content-Length": str(len(payload))})

            def _iter(chunk_size=None):
                for c in _chunked(payload[:cut_at]):
                    yield c
                raise ConnectionResetError("Connection reset by peer")
            resp.iter_bytes = _iter
        else:
            assert rng == f"bytes={cut_at}-", (
                f"retry must resume from byte {cut_at}, got Range={rng!r}")
            remaining = payload[cut_at:]
            resp = _FakeResponse(206, {
                "Content-Length": str(len(remaining)),
                "Content-Range": f"bytes {cut_at}-{len(payload) - 1}/{len(payload)}",
            })

            def _iter2(chunk_size=None):
                yield from _chunked(remaining)
            resp.iter_bytes = _iter2
        yield resp

    monkeypatch.setattr(
        "bulk_downloader.ssrf_transport.owning_stream", fake_owning_stream)
    monkeypatch.setattr(
        "bulk_downloader.ssrf_transport.guarded_transport",
        lambda policy, **kw: httpx.MockTransport(lambda r: httpx.Response(200)))
    monkeypatch.setattr("bulk_downloader.db.queue_upsert", lambda *a, **kw: None)

    def _noop(*a, **kw):
        pass

    # First attempt: disconnects mid-stream. Caller (the fixed retry loop)
    # would retry the same url on this; here we call it directly to prove
    # the resume plumbing itself, independent of the retry-loop's decision
    # (which the earlier unit tests already cover).
    with pytest.raises(_HTTPDownloadFailed):
        host._http_download_claimed(
            page_url, None, _FakeCtx(), file_url, final_path, [],
            _noop, _noop, _noop)

    part_path = staging_claim.staging_path_for(final_path)
    assert part_path.exists()
    assert part_path.stat().st_size == cut_at, (
        "the interrupted attempt must leave exactly the bytes it "
        "actually received on disk -- neither more nor less")

    size, transferred = host._http_download_claimed(
        page_url, None, _FakeCtx(), file_url, final_path, [],
        _noop, _noop, _noop)

    final_bytes = final_path.read_bytes()
    assert len(final_bytes) == len(payload)
    # Byte-for-byte equality is the strongest proof there is no duplicated
    # or dropped byte range: any splice, repeat, or gap changes this.
    assert final_bytes == payload
    assert hashlib.sha256(final_bytes).hexdigest() == expected_checksum
    assert size == len(payload)
    assert transferred == len(payload) - cut_at
