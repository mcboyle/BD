"""Exercise the three urllib seams through the actual socket boundary."""

BD_GATE_SCOPE = "repo-wide"

import io
import ipaddress
import socket
import ssl
import urllib.error
import urllib.request

import pytest

SEAMS = ("hook", "manifest", "template")
PUBLIC = "8.8.8.8"
METADATA = "169.254.169.254"


class FakeSocket:
    def __init__(self, response, wire):
        self.response = response
        self.wire = wire

    def sendall(self, data):
        self.wire.append(data)

    def makefile(self, *_args, **_kwargs):
        return io.BytesIO(self.response)

    def close(self):
        pass

    def setsockopt(self, *_args):
        pass

    def settimeout(self, *_args):
        pass


def http_response(location=None):
    if location:
        return (f"HTTP/1.1 302 Found\r\nLocation: {location}\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n").encode("iso-8859-1")
    body = b"<html><body>fixture</body></html>"
    return (b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: "
            + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)


@pytest.fixture
def network(monkeypatch):
    """No real connect: retain stdlib opener/HTTP parsing, replace its socket."""
    state = {"dns": [], "connect": [], "wire": [], "sni": [], "tls": [],
             "answers": [(PUBLIC,)], "responses": []}

    def resolve(host, port, *args, **kwargs):
        state["dns"].append(host)
        configured = state["answers"]
        answer = configured[min(len(state["dns"]) - 1, len(configured) - 1)]
        return [(socket.AF_INET6 if ":" in address else socket.AF_INET,
                 socket.SOCK_STREAM, 6, "", (address, port or 80))
                for address in answer]

    def connect(address, *_args, **_kwargs):
        host, port = address
        try:
            reached = str(ipaddress.ip_address(host))
        except ValueError:
            reached = resolve(host, port)[0][4][0]
        state["connect"].append((host, port, reached))
        response = (state["responses"].pop(0) if state["responses"]
                    else http_response())
        return FakeSocket(response, state["wire"])

    def wrap(_context, sock, *args, server_hostname=None, **kwargs):
        state["sni"].append(server_hostname)
        state["tls"].append((_context.check_hostname, _context.verify_mode))
        return sock

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(socket, "create_connection", connect)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", wrap)
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(urllib.request, "_opener", None)
    from bulk_downloader import hooks
    monkeypatch.setattr(hooks, "_HOOK_OPENER", None, raising=False)
    return state


def invoke(seam, url, monkeypatch):
    if seam == "hook":
        from bulk_downloader import hooks
        ok, why = hooks._validate_webhook_url(url)
        if not ok:
            return {"ok": False, "error": str(why)}
        try:
            with hooks._hook_urlopen(urllib.request.Request(url)) as response:
                return {"ok": bool(response.read()), "final_url": response.geturl()}
        except urllib.error.URLError as error:
            return {"ok": False, "error": str(error)}
    if seam == "manifest":
        from bulk_downloader.dev_suite import capture_diag
        ok, value = capture_diag._fetch_manifest_text(url)
        return {"ok": ok, "value": value}
    from flask import Flask
    from bulk_downloader import app_template
    monkeypatch.setattr(app_template, "_check_csrf", lambda: None)
    app = Flask(__name__)
    with app.test_request_context(json={"url": url, "template": {}, "mode": "http"}):
        response = app_template.api_template_sandbox()
        if isinstance(response, tuple):
            response = response[0]
        return response.get_json()


@pytest.mark.parametrize("seam", SEAMS)
@pytest.mark.parametrize("scheme", ("http", "https"))
def test_each_seam_connects_only_to_vetted_literal(seam, scheme, network, monkeypatch):
    result = invoke(seam, f"{scheme}://rebind.invalid/resource", monkeypatch)
    assert len(SEAMS) == 3
    assert result["ok"] is True, result
    assert len(network["connect"]) == 1, network
    assert network["connect"][0][0] == PUBLIC, network
    assert network["connect"][0][2] == PUBLIC, network
    assert b"Host: rebind.invalid\r\n" in b"".join(network["wire"]), network
    if scheme == "https":
        assert network["sni"] == ["rebind.invalid"], network
        assert network["tls"] == [(True, ssl.CERT_REQUIRED)], network


@pytest.mark.parametrize("seam", SEAMS)
def test_rebind_after_guard_is_refused_before_socket(seam, network, monkeypatch):
    network["answers"] = [(PUBLIC,), (METADATA,)]
    result = invoke(seam, "http://rebind.invalid/resource", monkeypatch)
    assert network["dns"] == ["rebind.invalid", "rebind.invalid"], network
    assert result["ok"] is False, result
    assert network["connect"] == [], network


@pytest.mark.parametrize("seam", SEAMS)
def test_mixed_public_and_metadata_answers_refuse_entire_set(seam, network, monkeypatch):
    network["answers"] = [(PUBLIC, METADATA)]
    result = invoke(seam, "http://rebind.invalid/resource", monkeypatch)
    assert network["dns"] == ["rebind.invalid"], network
    assert result["ok"] is False, result
    assert network["connect"] == [], network


@pytest.mark.parametrize("seam", SEAMS)
def test_public_redirect_to_metadata_never_opens_second_socket(seam, network, monkeypatch):
    network["responses"] = [http_response(f"http://{METADATA}/latest/meta-data/")]
    result = invoke(seam, "http://rebind.invalid/start", monkeypatch)
    assert result["ok"] is False, result
    assert len(network["connect"]) == 1, network
    assert network["connect"][0][2] == PUBLIC, network


@pytest.mark.parametrize("seam", SEAMS)
def test_relative_redirect_rechecks_logical_host(seam, network, monkeypatch):
    network["responses"] = [http_response("/next")]
    # The initial guard and initial pin pass; the relative redirect rebinds.
    network["answers"] = [(PUBLIC,), (PUBLIC,), (METADATA,)]
    result = invoke(seam, "http://rebind.invalid/start", monkeypatch)
    assert result["ok"] is False, result
    assert len(network["connect"]) == 1, network
    assert network["dns"] == ["rebind.invalid"] * 3, network


@pytest.mark.parametrize("scheme", ("http", "https"))
def test_template_final_url_keeps_logical_hostname(scheme, network, monkeypatch):
    url = f"{scheme}://rebind.invalid/resource"
    result = invoke("template", url, monkeypatch)
    assert result["ok"] is True, result
    assert len(network["connect"]) == 1, network
    assert result["final_url"] == url, result


def test_template_final_url_tracks_logical_redirect(network, monkeypatch):
    network["responses"] = [http_response("/next?x=1")]
    result = invoke("template", "http://rebind.invalid/start", monkeypatch)
    assert result["ok"] is True, result
    assert len(network["connect"]) == 2, network
    assert result["final_url"] == "http://rebind.invalid/next?x=1", result


@pytest.mark.parametrize("location", ("/caf\xe9", "/caf%E9"))
@pytest.mark.parametrize("seam", SEAMS)
def test_redirect_preserves_stdlib_header_encoding(seam, location, network, monkeypatch):
    # HTTP headers are ISO-8859-1; urllib percent-quotes them before opening.
    network["responses"] = [http_response(location)]
    result = invoke(seam, "http://rebind.invalid/start", monkeypatch)
    assert result["ok"] is True, result
    assert len(network["connect"]) == 2, network
    assert b"GET /caf%E9 HTTP/1.1\r\n" in b"".join(network["wire"]), network


@pytest.mark.parametrize("seam", SEAMS)
def test_ipv6_public_literal_is_kept_and_host_header_is_bracketed(seam, network, monkeypatch):
    address = "2606:4700:4700::1111"
    result = invoke(seam, f"http://[{address}]:8080/resource", monkeypatch)
    assert result["ok"] is True, result
    assert network["dns"] == [], network
    assert network["connect"] == [(address, 8080, address)], network
    assert f"Host: [{address}]:8080\r\n".encode() in b"".join(network["wire"]), network
