"""Tests for Row 836: Media Server Targeted Library Refresh Dispatcher.

Verifies:
1. Path mapping logic correctly converts local directory/file to server mount path.
2. Payload and auth compliance for Plex, Jellyfin, and Stash APIs.
3. Non-blocking execution (server timeout or latency does not block download completion).
4. Error isolation and SSRF URL validation compliance.
"""

from __future__ import annotations

BD_GATE_SCOPE = "module"

import http.server
import json
import threading
import time
import urllib.error
import urllib.parse
from typing import Any

from bulk_downloader.media_server_sync import (
    JellyfinClient,
    MediaServerConfig,
    MediaServerDispatcher,
    PathMapper,
    PlexClient,
    StashClient,
    dispatch_media_server_refresh,
)


class MockServerHandler(http.server.BaseHTTPRequestHandler):
    """Mock HTTP handler capturing requests for verification."""

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress stderr logging in tests

    def do_GET(self) -> None:
        self.server.requests.append(
            {  # type: ignore[attr-defined]
                "method": "GET",
                "path": self.path,
                "headers": self.headers,
            }
        )
        if getattr(self.server, "delay", 0) > 0:  # type: ignore[attr-defined]
            time.sleep(self.server.delay)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(b"<Response status='ok'/>")

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else ""
        self.server.requests.append(
            {  # type: ignore[attr-defined]
                "method": "POST",
                "path": self.path,
                "headers": self.headers,
                "body": body,
            }
        )
        if getattr(self.server, "delay", 0) > 0:  # type: ignore[attr-defined]
            time.sleep(self.server.delay)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps({"data": {"metadataScan": "job-123"}}).encode("utf-8")
        )


def start_mock_server(delay: float = 0.0):
    server = http.server.HTTPServer(("127.0.0.1", 0), MockServerHandler)
    server.requests = []  # type: ignore[attr-defined]
    server.delay = delay  # type: ignore[attr-defined]
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    return server, url


class TestPathMapper:
    """Test suite for path translation from local filesystem to server mounts."""

    def test_path_mapping_exact_and_prefix(self):
        mapper = PathMapper(
            [
                {"local": "/local/downloads/movies", "server": "/media/movies"},
                {"local": "/local/downloads/tv", "server": "/mnt/tv"},
            ]
        )
        # File within movies directory
        res = mapper.translate("/local/downloads/movies/Action/movie.mp4")
        assert res == "/media/movies/Action/movie.mp4"

        # Directory within tv directory
        res_dir = mapper.translate("/local/downloads/tv/Season 01")
        assert res_dir == "/mnt/tv/Season 01"

    def test_longest_prefix_match(self):
        mapper = PathMapper(
            [
                {"local": "/data/media", "server": "/media/general"},
                {"local": "/data/media/special", "server": "/media/special_mount"},
            ]
        )
        res = mapper.translate("/data/media/special/clip.mkv")
        assert res == "/media/special_mount/clip.mkv"

        res2 = mapper.translate("/data/media/other/clip.mkv")
        assert res2 == "/media/general/other/clip.mkv"

    def test_unmatched_path_passthrough(self):
        mapper = PathMapper(
            [
                {"local": "/local/downloads", "server": "/server/media"},
            ]
        )
        res = mapper.translate("/var/other/path/file.mp4")
        assert res == "/var/other/path/file.mp4"

    def test_dict_initialization(self):
        mapper = PathMapper({"/home/user/downloads": "/storage/media"})
        res = mapper.translate("/home/user/downloads/sub/item.avi")
        assert res == "/storage/media/sub/item.avi"


class TestMediaServerAuthAndPayload:
    """Verifies API protocol, auth headers/query params, and payloads for Plex, Jellyfin, and Stash."""

    def test_plex_targeted_refresh_compliance(self):
        server, url = start_mock_server()
        try:
            client = PlexClient(
                base_url=url,
                token="plex-secret-token-123",
                section_id="2",
            )
            res = client.refresh(path="/media/movies/Action/movie.mp4")
            assert res.ok is True
            assert res.server_type == "plex"
            assert len(server.requests) == 1

            req = server.requests[0]
            assert req["method"] == "GET"
            parsed = urllib.parse.urlparse(req["path"])
            assert parsed.path == "/library/sections/2/refresh"
            query = urllib.parse.parse_qs(parsed.query)
            assert query.get("path") == ["/media/movies/Action/movie.mp4"]
            assert query.get("X-Plex-Token") == ["plex-secret-token-123"]
            assert req["headers"].get("X-Plex-Token") == "plex-secret-token-123"
        finally:
            server.shutdown()

    def test_jellyfin_targeted_media_updated_compliance(self):
        server, url = start_mock_server()
        try:
            client = JellyfinClient(
                base_url=url,
                api_key="jelly-secret-key-456",
            )
            res = client.refresh(path="/mnt/tv/Show/episode.mkv")
            assert res.ok is True
            assert res.server_type == "jellyfin"
            assert len(server.requests) == 1

            req = server.requests[0]
            assert req["method"] == "POST"
            assert req["path"] == "/Library/Media/Updated"
            assert req["headers"].get("X-Emby-Token") == "jelly-secret-key-456"
            payload = json.loads(req["body"])
            assert "Updates" in payload
            assert len(payload["Updates"]) == 1
            assert payload["Updates"][0]["Path"] == "/mnt/tv/Show/episode.mkv"
            assert payload["Updates"][0]["UpdateType"] == "Created"
        finally:
            server.shutdown()

    def test_stash_targeted_metadata_scan_compliance(self):
        server, url = start_mock_server()
        try:
            client = StashClient(
                base_url=url,
                api_key="stash-api-token-789",
            )
            res = client.refresh(path="/storage/scenes/scene.mp4")
            assert res.ok is True
            assert res.server_type == "stash"
            assert len(server.requests) == 1

            req = server.requests[0]
            assert req["method"] == "POST"
            assert req["path"] == "/graphql"
            assert req["headers"].get("ApiKey") == "stash-api-token-789"
            payload = json.loads(req["body"])
            assert "mutation MetadataScan" in payload.get("query", "")
            assert "metadataScan" in payload.get("query", "")
            variables = payload.get("variables", {})
            assert variables.get("input", {}).get("paths") == [
                "/storage/scenes/scene.mp4"
            ]
        finally:
            server.shutdown()


class TestNonBlockingExecution:
    """Verifies that slow or timing-out servers do not block the caller or download process."""

    def test_async_dispatch_under_500ms(self):
        # Start a server that hangs with a 2.0s delay
        server, url = start_mock_server(delay=2.0)
        try:
            cfg = MediaServerConfig(
                server_type="jellyfin",
                url=url,
                token="test-key",
                timeout=3.0,
            )
            dispatcher = MediaServerDispatcher(servers=[cfg])

            start_t = time.time()
            # Non-blocking async dispatch
            res = dispatcher.dispatch(
                local_path="/local/downloads/movie.mp4",
                sync=False,
            )
            elapsed = time.time() - start_t

            # Must return immediately (< 500ms) without waiting for server response
            assert elapsed < 0.5, f"Expected dispatch in <500ms, took {elapsed:.3f}s"
            assert res == []  # Async returns immediately without blocking
        finally:
            server.shutdown()

    def test_server_timeout_does_not_raise(self):
        # Start server with delay exceeding client timeout
        server, url = start_mock_server(delay=1.5)
        try:
            cfg = MediaServerConfig(
                server_type="plex",
                url=url,
                token="test-token",
                timeout=0.2,  # 200ms client timeout
            )
            dispatcher = MediaServerDispatcher(servers=[cfg])

            # In sync mode, verify timeout is caught and reported in result
            results = dispatcher.dispatch(
                local_path="/local/downloads/movie.mp4",
                sync=True,
            )
            assert len(results) == 1
            assert results[0].ok is False
            assert (
                "timeout" in results[0].error.lower()
                or "timed out" in results[0].error.lower()
            )
        finally:
            server.shutdown()


class TestDispatcherIntegration:
    """End-to-end integration and path mapping tests."""

    def test_dispatcher_translates_and_fans_out(self):
        s_plex, u_plex = start_mock_server()
        s_jelly, u_jelly = start_mock_server()
        s_stash, u_stash = start_mock_server()

        try:
            configs = [
                MediaServerConfig(
                    server_type="plex",
                    url=u_plex,
                    token="plex-token",
                    section_id="4",
                    path_mappings={"/downloads/library": "/plex/media"},
                ),
                MediaServerConfig(
                    server_type="jellyfin",
                    url=u_jelly,
                    token="jelly-token",
                    path_mappings={"/downloads/library": "/jellyfin/storage"},
                ),
                MediaServerConfig(
                    server_type="stash",
                    url=u_stash,
                    token="stash-token",
                    path_mappings={"/downloads/library": "/stash/vault"},
                ),
            ]
            dispatcher = MediaServerDispatcher(servers=configs)
            results = dispatcher.dispatch(
                "/downloads/library/comedy/show.mp4", sync=True
            )

            assert len(results) == 3
            assert all(r.ok for r in results)

            # Check Plex received translated /plex/media path
            assert len(s_plex.requests) == 1
            assert "path=%2Fplex%2Fmedia%2Fcomedy%2Fshow.mp4" in s_plex.requests[0][
                "path"
            ] or "/plex/media/comedy/show.mp4" in urllib.parse.unquote(
                s_plex.requests[0]["path"]
            )

            # Check Jellyfin received translated /jellyfin/storage path
            assert len(s_jelly.requests) == 1
            j_body = json.loads(s_jelly.requests[0]["body"])
            assert j_body["Updates"][0]["Path"] == "/jellyfin/storage/comedy/show.mp4"

            # Check Stash received translated /stash/vault path
            assert len(s_stash.requests) == 1
            s_body = json.loads(s_stash.requests[0]["body"])
            assert s_body["variables"]["input"]["paths"] == [
                "/stash/vault/comedy/show.mp4"
            ]
        finally:
            s_plex.shutdown()
            s_jelly.shutdown()
            s_stash.shutdown()

    def test_convenience_function_dispatch_media_server_refresh(self):
        server, url = start_mock_server()
        try:
            cfg = {
                "server_type": "plex",
                "url": url,
                "token": "tok",
                "section_id": "1",
            }
            res = dispatch_media_server_refresh(
                file_path="/media/video.mp4",
                servers=[cfg],
                sync=True,
            )
            assert len(res) == 1
            assert res[0].ok is True
        finally:
            server.shutdown()


# ── FIXER (row836 REFUTE E1 HIGH / E2 / E3) ──────────────────────────────

import pytest

from bulk_downloader import media_server_sync


class TestFixerEscapes:
    def test_server_root_prefix_is_preserved(self):
        """E2: a server mount at '/' must translate to '/...' -- never to ''
        (which made PlexClient drop the path query and full-scan the section)."""
        mapper = PathMapper([{"local": "/local/downloads", "server": "/"}])
        assert mapper.translate("/local/downloads") == "/"
        assert mapper.translate("/local/downloads/Movies/a.mp4") == "/Movies/a.mp4"
        mapper2 = PathMapper([{"local": "/local/downloads/", "server": "/media/"}])
        assert mapper2.translate("/local/downloads/x.mp4") == "/media/x.mp4"

    @pytest.mark.parametrize("location", [
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata (link-local): the SSRF target
        "http://[fe80::1]/admin",                     # IPv6 link-local
        "file:///etc/passwd",                         # non-http scheme (urllib refuses this one itself)
    ])
    def test_redirect_to_a_refused_target_is_blocked(self, location):
        """E1 HIGH (round 2 E4): a media server answering 302 to a target the
        hook policy refuses must not be followed. The first two Locations are
        plain http redirects urllib's own handler WOULD follow -- only the
        guarded opener says no; the socket is never opened (the server sees
        exactly one hit and the failure is immediate, not a 2s timeout)."""
        class Redirecting(http.server.BaseHTTPRequestHandler):
            hits: list = []

            def do_GET(self):  # noqa: N802
                Redirecting.hits.append(self.path)
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()

            def log_message(self, *a):
                pass

        Redirecting.hits = []
        srv = http.server.HTTPServer(("127.0.0.1", 0), Redirecting)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}"
            client = PlexClient(url, "t", section_id="1", timeout=3.0)
            started = time.perf_counter()
            res = client.refresh("/media/x")
            elapsed = time.perf_counter() - started
            assert res.ok is False, res
            assert "redirect" in (res.error or "").lower() or "blocked" in (res.error or "").lower(), res
            assert len(Redirecting.hits) == 1
            assert elapsed < 1.5, elapsed  # refused before any connection attempt, not a connect timeout
        finally:
            srv.shutdown()

    def test_refused_base_url_is_never_opened(self, monkeypatch):
        """Round 2 M6: a base URL the policy refuses does not reach the opener."""
        opened = []
        monkeypatch.setattr(media_server_sync, "_open", lambda req, timeout: opened.append(req.full_url))
        client = PlexClient("http://169.254.169.254", "t", section_id="1", timeout=1.0)
        res = client.refresh("/media/x")
        assert res.ok is False and opened == [], (res, opened)

    def test_bare_opener_mutant_is_caught(self, monkeypatch):
        """R-NEG: replacing _open() with a bare urllib opener (follows any http
        redirect) makes the metadata-redirect case FAIL -- the guard is the
        only thing saying no."""
        import urllib.request

        class Redirecting(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
                self.end_headers()

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Redirecting)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        followed = []

        class Recording(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                followed.append(newurl)
                raise urllib.error.HTTPError(newurl, code, "fixture stop", headers, fp)

        monkeypatch.setattr(media_server_sync, "_open",
                            lambda req, timeout: urllib.request.build_opener(Recording()).open(req, timeout=timeout))
        try:
            PlexClient(f"http://127.0.0.1:{srv.server_port}", "t", section_id="1", timeout=3.0).refresh("/media/x")
        finally:
            srv.shutdown()
        assert followed == ["http://169.254.169.254/latest/meta-data/"]  # a bare opener DOES follow it

    def test_every_request_goes_through_the_guarded_opener(self, monkeypatch):
        """No client reaches urllib.request.urlopen directly."""
        src = open(media_server_sync.__file__, encoding="utf-8").read()
        assert "urllib.request.urlopen(" not in src
        assert src.count("with _open(req, self.config.timeout) as resp:") == 3
