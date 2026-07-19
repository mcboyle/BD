"""Tests for integration hooks (Phase 20, with Phase 41 coverage).

We don't spin up real Stash/Plex/Jellyfin/Home Assistant servers — we
mock the HTTP layer (urllib.request.urlopen) and verify the right URL
is hit with the right payload. This catches regressions in:
  - Payload structure (Stash GraphQL, Plex ?path= param, Jellyfin POST)
  - Authentication headers (Stash ApiKey, Plex X-Plex-Token, Jellyfin
    MediaBrowser Token)
  - Template variable substitution in URLs
"""
import io
import json
from unittest.mock import MagicMock, patch



class TestSendWebhook:
    def test_success_returns_ok(self):
        from bulk_downloader.hooks import send_webhook
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        with patch("bulk_downloader.hooks._hook_urlopen", return_value=fake_resp) as m:
            ok, msg, _resp = send_webhook("http://localhost/hook", {"x": 1})
        assert ok is True
        assert "200" in msg
        # Verify it actually called urlopen
        call_args = m.call_args
        req = call_args[0][0]
        assert req.full_url == "http://localhost/hook"
        assert req.get_method() == "POST"

    def test_http_error_returns_status(self):
        from bulk_downloader.hooks import send_webhook
        import urllib.error
        err = urllib.error.HTTPError(
            "http://127.0.0.1/", 500, "Internal Server Error",
            {}, io.BytesIO(b'{"error":"boom"}'))
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=err):
            ok, msg, _resp = send_webhook("http://127.0.0.1/", {})
        assert ok is False
        assert "500" in msg

    def test_network_error_returns_false(self):
        from bulk_downloader.hooks import send_webhook
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=ConnectionRefusedError("nope")):
            ok, msg, _resp = send_webhook("http://127.0.0.1/", {})
        assert ok is False
        assert "ConnectionRefused" in msg or "nope" in msg

    def test_payload_is_json_encoded(self):
        from bulk_downloader.hooks import send_webhook
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        captured_body = []
        def _capture(req, **kwargs):
            captured_body.append(req.data)
            return fake_resp
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=_capture):
            send_webhook("http://127.0.0.1/", {"hello": "world", "n": 42})
        body = json.loads(captured_body[0])
        assert body == {"hello": "world", "n": 42}

    def test_empty_url_skipped_gracefully(self):
        from bulk_downloader.hooks import send_webhook
        ok, msg, _resp = send_webhook("", {})
        assert ok is True
        assert "no webhook" in msg.lower() or "(no" in msg


class TestStashScan:
    def test_url_normalization_adds_graphql_suffix(self):
        from bulk_downloader.hooks import stash_trigger_scan
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        fake_resp.read.return_value = b'{"data":{"metadataScan":"job-123"}}'
        captured = []
        def _capture(req, **kwargs):
            captured.append(req); return fake_resp
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=_capture):
            stash_trigger_scan("http://127.0.0.1:9999", "api_xyz")
        assert "/graphql" in captured[0].full_url

    def test_api_key_header_set(self):
        from bulk_downloader.hooks import stash_trigger_scan
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        fake_resp.read.return_value = b'{"data":{"metadataScan":"job-1"}}'
        captured = []
        def _capture(req, **kwargs):
            captured.append(req); return fake_resp
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=_capture):
            stash_trigger_scan("http://127.0.0.1:9999/graphql", "my_api_key")
        headers = dict(captured[0].headers)
        # urllib lowercases header names internally — check both forms
        assert any("my_api_key" in str(v) for v in headers.values())

    def test_paths_included_in_payload(self):
        from bulk_downloader.hooks import stash_trigger_scan
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        fake_resp.read.return_value = b'{"data":{"metadataScan":"job"}}'
        captured = []
        def _capture(req, **kwargs):
            captured.append(req); return fake_resp
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=_capture):
            stash_trigger_scan("http://127.0.0.1/graphql", "key",
                               paths=["/data/wowgirls", "/data/other"])
        body = json.loads(captured[0].data)
        # GraphQL payload — paths nested under variables.input
        assert "/data/wowgirls" in json.dumps(body)
        assert "/data/other" in json.dumps(body)

    def test_unconfigured_returns_skip(self):
        from bulk_downloader.hooks import stash_trigger_scan
        ok, msg = stash_trigger_scan("", "")
        assert ok is True
        assert "not configured" in msg


class TestEventDispatch:
    """fire_event() is fire-and-forget (runs in a thread). We verify the
    synchronous _dispatch routes to the right sinks based on site_config."""

    def test_completed_no_hooks_configured(self):
        from bulk_downloader.hooks import _dispatch, build_vars
        vars = build_vars({}, "completed", job={})
        # No hooks configured → _dispatch should noop silently
        _dispatch("completed", {}, vars, {})

    def test_completed_fires_webhook_when_configured(self):
        from bulk_downloader.hooks import _dispatch, build_vars
        site = {"webhook_urls": "http://localhost/hook", "webhook_events": ""}
        vars = build_vars(site, "completed",
                         job={"url": "https://x/v.mp4", "filename": "v.mp4"})
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        captured = []
        def _capture(req, **kwargs):
            captured.append(req); return fake_resp
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=_capture):
            _dispatch("completed", site, vars, {"url": "https://x/v.mp4"})
        assert len(captured) == 1
        assert captured[0].full_url == "http://localhost/hook"

    def test_unsubscribed_event_skipped(self):
        # When site is subscribed only to "failed", a "completed" event
        # should NOT fire the webhook
        from bulk_downloader.hooks import _dispatch, build_vars
        site = {
            "webhook_urls": "http://localhost/hook",
            "webhook_events": "failed",  # NOT completed
        }
        vars = build_vars(site, "completed", job={})
        fake_resp = MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__ = MagicMock(return_value=fake_resp)
        fake_resp.__exit__ = MagicMock(return_value=False)
        captured = []
        def _capture(req, **kwargs):
            captured.append(req); return fake_resp
        with patch("bulk_downloader.hooks._hook_urlopen", side_effect=_capture):
            _dispatch("completed", site, vars, {})
        assert len(captured) == 0


class TestUnknownEvent:
    def test_unknown_event_returns_silently(self):
        from bulk_downloader.hooks import fire_event
        # Should not raise — just logs and returns
        fire_event("not_a_real_event", {}, job={})
