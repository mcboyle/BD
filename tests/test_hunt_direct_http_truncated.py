"""A library direct download must reject a short successful HTTP response."""

import contextlib
import threading

import httpx

from bulk_downloader import ssrf_transport
from bulk_downloader.runner_transport import TransportMixin

BD_GATE_SCOPE = "module"


class _Response:
    status_code = 200

    def __init__(self, body, headers=None):
        self.headers = {"content-length": "4", **(headers or {})}
        self.body = body

    def iter_bytes(self, _size):
        yield self.body


class _Runner(TransportMixin):
    site_id = "test"

    def __init__(self):
        self.config = {}
        self._stop = threading.Event()

    def _download_proxy_url(self):
        return None

    def _regional_gateway_router(self):
        return None

    def _update_job(self, *args, **kwargs):
        raise AssertionError("unexpected progress update")


def test_direct_http_rejects_short_200(monkeypatch, tmp_path):
    response = [_Response(b"abcd")]
    monkeypatch.setattr(ssrf_transport, "guarded_transport", lambda *a, **k: None)
    monkeypatch.setattr(ssrf_transport, "owning_stream", lambda *a, **k: contextlib.nullcontext(response[0]))
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: object())
    runner = _Runner()
    output = tmp_path / "video.mp4"

    assert runner._do_direct_http_download("https://example.org/page", "https://example.org/video", str(output))
    assert output.read_bytes() == b"abcd"

    response[0] = _Response(b"ab")
    assert not runner._do_direct_http_download("https://example.org/page", "https://example.org/video", str(output))

    # Content-Length is the encoded size; decoded bytes from a gzip body differ.
    response[0] = _Response(b"abcdefgh", {"content-encoding": "gzip"})
    assert runner._do_direct_http_download("https://example.org/page", "https://example.org/video", str(output))
    assert output.read_bytes() == b"abcdefgh"
