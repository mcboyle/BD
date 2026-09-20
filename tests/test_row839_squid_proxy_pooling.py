"""Row 839: optional local HTTP proxy pooling at the guarded egress seam."""
from __future__ import annotations

import sys
import types
import urllib.error


BD_GATE_SCOPE = "module"


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_guarded_open_uses_the_configured_proxy_transport(monkeypatch):
    from bulk_downloader import deep_http

    direct_calls = []
    proxy_calls = []

    class DirectOpener:
        def open(self, request, *, timeout):
            direct_calls.append((request, timeout))
            return _Response()

    def proxy_open(request, *, timeout, direct_open):
        proxy_calls.append((request, timeout, direct_open))
        return _Response()

    with monkeypatch.context() as patch:
        patch.setenv("BD_HTTP_PROXY", "http://127.0.0.1:3128")
        patch.setattr(deep_http, "_OPENER", DirectOpener())
        patch.setitem(sys.modules, "bulk_downloader.http_client",
                      types.SimpleNamespace(proxy_open=proxy_open))
        response = deep_http.guarded_open("http://example.com/media", timeout=1,
                                          allow_private_hosts=True)

    assert isinstance(response, _Response)
    assert len(proxy_calls) == 1, "configured proxy transport must receive the request"
    assert direct_calls == [], "configured proxy must not bypass its pooled transport"


def test_proxy_pool_reuses_one_opener_and_opens_the_circuit_after_three_5xx(monkeypatch):
    from bulk_downloader import http_client

    class ProxyOpener:
        def __init__(self):
            self.calls = 0

        def open(self, _request, *, timeout):
            self.calls += 1
            raise urllib.error.HTTPError("http://127.0.0.1:3128", 503, "down", {}, None)

    created = []
    direct_calls = []

    def build_opener(*_handlers):
        opener = ProxyOpener()
        created.append(opener)
        return opener

    def direct_open(_request, *, timeout):
        direct_calls.append(timeout)
        return _Response()

    with monkeypatch.context() as patch:
        patch.setenv("BD_HTTP_PROXY", "http://127.0.0.1:3128")
        patch.setattr(http_client.urllib.request, "build_opener", build_opener)
        patch.setattr(http_client, "_POOLS", {})
        for _ in range(4):
            assert isinstance(http_client.proxy_open(object(), timeout=0.1,
                                                     direct_open=direct_open), _Response)

    assert len(created) == 1, "the same configured proxy must reuse one pooled opener"
    assert created[0].calls == 3, "the fourth call must use the open circuit directly"
    assert direct_calls == [0.1, 0.1, 0.1, 0.1]
