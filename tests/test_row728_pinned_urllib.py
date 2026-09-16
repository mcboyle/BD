"""Row 728: urllib must send to the address it vetted, not resolve twice."""

import pytest
from urllib.error import URLError
from urllib.request import Request

BD_GATE_SCOPE = "repo-wide"


def test_pinned_urllib_request_uses_the_single_vetted_answer(monkeypatch):
    """A rebinding answer after the check cannot become the open destination."""
    from bulk_downloader.urllib_ssrf import PinnedUrlOpener
    import bulk_downloader.urllib_ssrf as pinned

    answers = iter(["8.8.8.8", "169.254.169.254"])
    calls = []

    def rebind(host, port, *, type):
        calls.append((host, port, type))
        return [(2, type, 6, "", (next(answers), port))]

    monkeypatch.setattr(pinned.socket, "getaddrinfo", rebind)
    opener = PinnedUrlOpener(lambda _address, _host: (True, ""))
    request = opener.pin_request(Request("http://rebind.invalid:8080/manifest"))

    assert calls == [("rebind.invalid", 8080, pinned.socket.SOCK_STREAM)]
    assert request.full_url == "http://8.8.8.8:8080/manifest"
    assert request.get_header("Host") == "rebind.invalid:8080"


def test_three_urllib_guards_share_the_pinned_opener():
    """The three guarded urllib seams cannot drift back to classify-then-open."""
    from bulk_downloader import app_template, hooks
    from bulk_downloader.dev_suite import capture_diag

    assert "PinnedUrlOpener" in open(app_template.__file__, encoding="utf-8").read()
    assert "PinnedUrlOpener" in open(hooks.__file__, encoding="utf-8").read()
    assert "PinnedUrlOpener" in open(capture_diag.__file__, encoding="utf-8").read()


def test_hook_https_seam_surfaces_a_url_error_not_handler_attribute_error(
        monkeypatch):
    """A closed TLS port reaches the pinned HTTPS connection layer for hooks."""
    from bulk_downloader import hooks

    monkeypatch.setattr(
        hooks, "_hook_address_allowed", lambda _address, _host: (True, ""))
    with pytest.raises(URLError) as caught:
        hooks._hook_urlopen(Request("https://127.0.0.1:1/hook"), timeout=1)

    assert "AttributeError" not in str(caught.value)


def test_manifest_https_seam_surfaces_a_transport_error_not_handler_attribute_error(
        monkeypatch):
    """A closed TLS port reaches the pinned HTTPS connection layer for manifests."""
    from bulk_downloader.dev_suite import capture_diag
    from bulk_downloader.provider_resolve_impl import _common

    monkeypatch.setattr(
        _common, "_is_safe_public_host", lambda _host: (True, ""))
    monkeypatch.setattr(
        _common, "_classify_ip", lambda _address, _host: (True, ""))
    ok, detail = capture_diag._fetch_manifest_text("https://127.0.0.1:1/manifest")

    assert ok is False
    assert "AttributeError" not in detail
    assert "URLError" in detail


def test_template_https_seam_surfaces_a_transport_error_not_handler_attribute_error(
        fresh_app):
    """A closed TLS port reaches the pinned HTTPS connection layer for templates."""
    response = fresh_app.post(
        "/api/template/sandbox",
        json={"url": "https://127.0.0.1:1/template", "template": {},
              "mode": "http"},
    )
    body = response.get_json()

    assert response.status_code == 200
    assert body["ok"] is False
    assert "AttributeError" not in body["error"]
    assert "urlopen error" in body["error"]
