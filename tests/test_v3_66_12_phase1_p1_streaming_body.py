"""v3.66.12 — Phase 1, P1: streaming manifest fetch with hard byte cap.

The `_fetch_manifest_capped()` helper replaces the inline body-read
in deep_detect_live's manifest-follow loop. The fix closes a DoS
vector: pre-v3.66.12, a hostile server returning 100 MB of garbage
at an .m3u8 endpoint would consume 100 MB of process memory before
the post-hoc cap fired. Now the streaming path stops reading at
`cap_bytes + 1` bytes.

Tests cover:

  1. Streaming-aware client: when `http.stream()` is available, the
     helper uses it and stops after the cap is reached.
  2. Backward-compat fallback: non-streaming stubs (the existing
     test pattern) still work via the GET path.
  3. Truncation flag set correctly when body exceeds cap.
  4. Truncation flag NOT set when body fits.
  5. Network errors during streaming → caught, returned as
     {ok: False, error: ...}.
  6. Non-2xx response → caught, returned as
     {ok: False, error: "http status N"}.
  7. Content-Type extracted and lowercased.
  8. End-to-end: deep_detect_live with a 100 MB-claiming stub
     truncates correctly via the helper.
"""
import os
import sys

import pytest



# v3.66.13: this file used to define its own autouse isolated_bd_home
# fixture; the canonical one lives in tests/conftest.py. The marker
# opts this file back into the sys.modules wipe (needed so the package
# re-reads env vars at import; see conftest.py for the full rationale).
pytestmark = pytest.mark.bd_module_wipe

# ── Streaming stubs ───────────────────────────────────────────────────


class _StreamResp:
    """Context-manager stub mimicking httpx's streaming response.

    `chunks` is an iterable of bytes yielded by iter_bytes(). The
    test controls how much data the server "sends" — including
    impossibly-large streams (100 MB+) to verify the cap fires
    before the helper buffers everything.
    """

    def __init__(self, chunks, status_code=200, headers=None,
                 url="https://x.com/manifest.m3u8"):
        self._chunks = chunks
        self.status_code = status_code
        self.headers = headers or {"content-type": "application/vnd.apple.mpegurl"}
        self.url = url
        self._iter_count = 0
        self._read_bytes = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self, chunk_size=8192):
        """Yield chunks. Tracks read bytes so tests can assert we
        STOPPED reading early when the cap fires."""
        for c in self._chunks:
            self._iter_count += 1
            self._read_bytes += len(c)
            yield c


class _StreamingClient:
    """HTTP client that supports `.stream("GET", url, ...)`. Used
    by the streaming-path tests.
    """

    def __init__(self, chunks, status_code=200, headers=None,
                 url="https://x.com/manifest.m3u8",
                 raise_on_stream=None):
        self.chunks = chunks
        self.status_code = status_code
        self.headers = headers
        self.url = url
        self.raise_on_stream = raise_on_stream
        self.streams_opened = 0
        self.last_response = None

    def stream(self, method, url, headers=None, timeout=None,
               follow_redirects=True):
        self.streams_opened += 1
        if self.raise_on_stream:
            raise self.raise_on_stream
        resp = _StreamResp(
            self.chunks,
            status_code=self.status_code,
            headers=self.headers,
            url=self.url,
        )
        self.last_response = resp
        return resp

    def get(self, *a, **kw):
        # Fallback method — should NOT be called when .stream() works
        raise AssertionError("get() called but stream() should have been used")


class _NonStreamingClient:
    """HTTP client without `.stream()` — exercises the GET fallback."""

    def __init__(self, body, status_code=200, headers=None,
                 url="https://x.com/manifest.m3u8"):
        class _R:
            pass
        r = _R()
        r.status_code = status_code
        r.headers = headers or {
            "content-type": "application/vnd.apple.mpegurl"}
        r.text = body if isinstance(body, str) else body.decode(
            "utf-8", errors="replace")
        r.url = url
        self._resp = r
        self.gets_called = 0

    def get(self, url, headers=None, timeout=None,
            follow_redirects=True):
        self.gets_called += 1
        return self._resp


# ── P1 tests ──────────────────────────────────────────────────────────


def test_P1_helper_uses_streaming_when_available():
    """When the client exposes .stream(), the helper uses it
    (not .get())."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    chunks = [b"#EXTM3U\n", b"#EXT-X-VERSION:3\n", b"data"]
    client = _StreamingClient(chunks)

    result = _fetch_manifest_capped(
        client, "https://x.com/m.m3u8",
        cap_bytes=1024,
    )
    assert client.streams_opened == 1, "should have used .stream()"
    assert result["ok"] is True
    assert result["body"].startswith("#EXTM3U")
    assert result["truncated"] is False
    assert result["content_type"] == "application/vnd.apple.mpegurl"


def test_P1_streaming_path_caps_at_byte_limit():
    """The helper MUST stop reading once cap_bytes are buffered.

    Simulate a hostile server claiming to send 100 MB in 64 KB chunks
    — the helper should stop after ~cap_bytes are read, NOT buffer
    the full 100 MB. We assert via the response's `_read_bytes`
    counter that we stopped early.
    """
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    cap = 1024  # 1 KB cap
    # 100 MB in 64 KB chunks would be 1600 chunks; if the helper
    # didn't cap, we'd read all of them.
    big_chunk = b"x" * 65536  # 64 KB per chunk
    chunks = (big_chunk for _ in range(1600))  # generator: lazy
    client = _StreamingClient(chunks)

    result = _fetch_manifest_capped(
        client, "https://x.com/m.m3u8",
        cap_bytes=cap,
    )
    assert result["ok"] is True
    assert result["truncated"] is True, (
        "must flag truncation when body exceeded cap")
    assert len(result["body"]) == cap, (
        f"body should be capped at {cap} bytes; got {len(result['body'])}")
    # The streaming response should have read at most cap_bytes +
    # one chunk (because we break AFTER appending). The exact bound
    # is cap_bytes + chunk_size - 1, which here is 1024 + 65535.
    # The point is: NOT 100 MB.
    assert client.last_response._read_bytes < 200_000, (
        f"helper kept reading past cap; read "
        f"{client.last_response._read_bytes} bytes")


def test_P1_streaming_path_no_truncation_when_body_fits():
    """When body is smaller than cap_bytes, truncated must be False."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    chunks = [b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"]
    client = _StreamingClient(chunks)

    result = _fetch_manifest_capped(
        client, "https://x.com/m.m3u8",
        cap_bytes=1_048_576,  # 1 MB — much bigger than the body
    )
    assert result["ok"] is True
    assert result["truncated"] is False
    assert "EXTM3U" in result["body"]


def test_P1_falls_back_to_get_for_non_streaming_stubs():
    """When the client lacks .stream(), the helper uses .get().
    This preserves backward-compat with the test stubs every
    existing deep_detect_live test uses."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n"
    client = _NonStreamingClient(body)

    result = _fetch_manifest_capped(
        client, "https://x.com/m.m3u8",
        cap_bytes=1_048_576,
    )
    assert client.gets_called == 1, "should have used .get()"
    assert result["ok"] is True
    assert result["body"] == body
    assert result["truncated"] is False


def test_P1_get_fallback_also_truncates():
    """Even the GET-fallback path caps the body at cap_bytes."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    big_body = "x" * 5_000_000  # 5 MB
    client = _NonStreamingClient(big_body)

    result = _fetch_manifest_capped(
        client, "https://x.com/m.m3u8",
        cap_bytes=1024,
    )
    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["body"]) == 1024


def test_P1_stream_raises_then_falls_back_to_get():
    """If the streaming call raises (e.g. stub has .stream as a
    non-callable attribute, or a real connection error mid-stream),
    the helper falls through to .get()."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    body = "#EXTM3U\n"

    class _MixedClient:
        """Has both .stream() (broken) and .get() (working)."""

        def stream(self, *a, **kw):
            raise ConnectionError("simulated network drop")

        def get(self, url, **kw):
            class _R:
                status_code = 200
                headers = {"content-type": "application/vnd.apple.mpegurl"}
                text = body
                url = "https://x.com/m.m3u8"
            return _R()

    result = _fetch_manifest_capped(
        _MixedClient(), "https://x.com/m.m3u8", cap_bytes=1024)
    assert result["ok"] is True, (
        f"helper should fall back to GET on stream error; got {result}")
    assert result["body"] == body
    # error from the streaming attempt must NOT leak through on a
    # successful fallback
    assert result["error"] is None


def test_P1_non_2xx_response_is_error():
    """A 404/500 response surfaces as ok=False with a status message."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    client = _StreamingClient(
        chunks=[b""],
        status_code=404,
    )
    result = _fetch_manifest_capped(
        client, "https://x.com/m.m3u8", cap_bytes=1024)
    assert result["ok"] is False
    assert result["status"] == 404
    assert "404" in result["error"]


def test_P1_total_network_error_returns_error_dict():
    """If both .stream() AND .get() raise (or only .stream() exists
    and raises), the helper returns ok=False with the error."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    class _BrokenStreamOnly:
        """Has only .stream() and it raises."""

        def stream(self, *a, **kw):
            raise ConnectionError("total network drop")

    result = _fetch_manifest_capped(
        _BrokenStreamOnly(), "https://x.com/m.m3u8",
        cap_bytes=1024)
    # No .get() means the fallback ALSO raises (AttributeError).
    # Either way: ok=False with an error.
    assert result["ok"] is False
    assert result["error"]  # non-empty


def test_P1_handles_no_http_client():
    """If http is None, returns ok=False without raising."""
    from bulk_downloader.deep_detect import _fetch_manifest_capped

    result = _fetch_manifest_capped(None, "https://x.com/m.m3u8",
                                    cap_bytes=1024)
    assert result["ok"] is False
    assert "no http client" in result["error"]


def test_P1_end_to_end_dd_live_caps_huge_manifest_response():
    """End-to-end: deep_detect_live with a 100 MB-claiming server.
    The .m3u8 candidate gets a manifest-follow that streams from a
    client returning 100 MB chunks. Verify the report's
    follow_record says truncated=True and we never bloated memory."""
    from bulk_downloader.deep_detect import deep_detect_live

    # 100 MB of bytes split into 64 KB chunks (1600 chunks).
    # The streaming helper should stop after max_manifest_bytes.
    big_chunk = b"#EXT-X-STREAM-INF:BANDWIDTH=1\nv.m3u8\n" + (b"x" * 65000)
    n_chunks_offered = 1600  # would be ~100 MB if we read all

    # Track how many chunks the iter actually yielded
    chunks_yielded = []

    def _gen():
        for i in range(n_chunks_offered):
            chunks_yielded.append(i)
            yield big_chunk

    class _BigClient:
        def __init__(self):
            self.gen = _gen()

        def head(self, url, **kw):
            class _R:
                status_code = 200
                headers = {"content-type": "application/vnd.apple.mpegurl"}
                url = url
            return _R()

        def stream(self, method, url, **kw):
            # Return a fresh _StreamResp each call so iter_bytes works
            return _StreamResp(
                chunks=self.gen,
                status_code=200,
                headers={"content-type":
                         "application/vnd.apple.mpegurl"},
                url=url,
            )

        def close(self):
            pass

    html = '<html><body><a href="/big.m3u8">play</a></body></html>'

    r = deep_detect_live(
        html,
        base_url="https://x.com/",
        http=_BigClient(),
        follow_manifests=True,
        max_manifest_bytes=1024,  # 1 KB cap
    )

    follows = r["probes"].get("manifests_followed", [])
    fetched_records = [f for f in follows if f.get("fetched")]
    assert len(fetched_records) >= 1, (
        f"manifest should have been followed; got follows={follows}")
    # The truncation flag must be set on the follow_record.
    assert fetched_records[0].get("truncated") is True, (
        f"truncated should be True; got {fetched_records[0]}")
    # And the generator must have stopped early — far fewer than
    # 1600 chunks consumed.
    assert len(chunks_yielded) < 100, (
        f"helper kept reading past cap; yielded {len(chunks_yielded)} "
        f"chunks of 1600 offered")
