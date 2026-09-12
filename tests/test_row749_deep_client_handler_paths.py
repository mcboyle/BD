"""Row 749: deep-client HTTP handlers must preserve destination policy."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

BD_GATE_SCOPE = "module"
from flask import Flask


class _Recorder(HTTPServer):
    def __init__(self):
        self.requests = []
        super().__init__(("127.0.0.1", 0), _Handler)

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server_address[1]}"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        if self.path.startswith("/graphql"):
            body = json.dumps({"data": {"version": {"version": "1"}}}).encode()
        elif self.path.startswith("/identity"):
            body = b'<MediaContainer version="1"/>'
        else:
            body = json.dumps({"Version": "1"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

    do_POST = do_GET


@pytest.fixture
def recorder():
    server = _Recorder()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


@pytest.fixture
def handler_context(monkeypatch):
    from bulk_downloader import app_sites_integrations as handlers

    config = {}
    monkeypatch.setattr(handlers, "_app_runners", lambda: {"site": object()})
    monkeypatch.setattr(handlers, "_app_s_cfg", lambda: {"site": config})
    return Flask(__name__), config, handlers


_CLIENTS = (
    ("jellyfin", "jellyfin_url", "jellyfin_api_key",
     "jellyfin_allow_private_host", "api_jellyfin_diagnose"),
    ("plex", "plex_url", "plex_token", "plex_allow_private_host",
     "api_plex_diagnose"),
    ("stash", "stash_url", "stash_api_key", "stash_allow_private_host",
     "api_stash_diagnose"),
)


def _config(url, url_key, token_key, setting, allow_private):
    return {
        url_key: url,
        token_key: "row749-test-token",
        setting: allow_private,
    }


@pytest.mark.parametrize("name,url_key,token_key,setting,handler_name", _CLIENTS)
def test_row749_handler_reaches_classification_and_refusal(
    handler_context, recorder, monkeypatch, name, url_key, token_key, setting, handler_name,
):
    """Breaking the configured opt-in must make this handler's local route fail.

    The route invokes the real client, which invokes ``deep_http`` before any
    recorder request.  A change from the helper's opt-in to ``False`` is the
    production regression this test catches.
    """
    app, config, handlers = handler_context
    from bulk_downloader import deep_http
    checked = []
    original_check = deep_http._check
    def observe_check(url, allow_private_hosts):
        checked.append((url, allow_private_hosts))
        return original_check(url, allow_private_hosts)
    monkeypatch.setattr(deep_http, "_check", observe_check)
    config.update(_config(recorder.origin, url_key, token_key, setting, False))
    handler = getattr(handlers, handler_name)

    with app.app_context():
        refused = handler("site").get_json()

    assert refused["ok"] is False
    assert "blocked:" in refused["error"]
    assert setting in refused["error"]
    assert len(checked) == 1 and checked[0][0].startswith(recorder.origin) \
        and checked[0][1] is False, f"{name} skipped destination classification"
    assert recorder.requests == [], f"{name} sent before destination classification"


class _PublicResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


@pytest.mark.parametrize("name,url_key,token_key,setting,handler_name", _CLIENTS)
def test_row749_public_destination_without_optin_reaches_transport(
    handler_context, monkeypatch, name, url_key, token_key, setting, handler_name,
):
    """Each real handler reaches fake transport after genuine public classification."""
    from bulk_downloader import deep_http
    opened = []

    class _Opener:
        def open(self, request, timeout):
            opened.append((request.full_url, timeout))
            if request.full_url.startswith("http://8.8.8.8/graphql"):
                body = json.dumps({"data": {"version": {"version": "1"}}}).encode()
            elif request.full_url.startswith("http://8.8.8.8/identity"):
                body = b'<MediaContainer version="1"/>'
            else:
                body = json.dumps({"Version": "1"}).encode()
            return _PublicResponse(body)

    app, config, handlers = handler_context
    monkeypatch.setattr(deep_http, "_OPENER", _Opener())
    config.update(_config("http://8.8.8.8", url_key, token_key, setting, False))
    with app.app_context():
        result = getattr(handlers, handler_name)("site").get_json()

    assert result["ok"] is True
    assert len(opened) == 1 and opened[0][0].startswith("http://8.8.8.8"), name


@pytest.mark.parametrize("name,url_key,token_key,setting,handler_name", _CLIENTS)
def test_row749_site_optin_reaches_legitimate_destination(
    handler_context, recorder, name, url_key, token_key, setting, handler_name,
):
    """A site's explicit LAN opt-in reaches its configured deep server."""
    app, config, handlers = handler_context
    config.update(_config(recorder.origin, url_key, token_key, setting, True))
    handler = getattr(handlers, handler_name)

    with app.app_context():
        result = handler("site").get_json()

    assert result["ok"] is True
    assert len(recorder.requests) == 1, f"{name} handler did not reach its client"
