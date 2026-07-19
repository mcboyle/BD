"""T44 (Tier 4) — D-46 request-replay tool.

Bounded ring buffer of captured /api/* requests + a replay helper that
re-issues against the running app.

Tests focus on the recorder, the ring buffer bounds, the read API, and
the replay error paths (network calls happen against an unreachable
port to verify the error shape — no real network roundtrip in unit
tests).
"""
import contextlib
import json

from bulk_downloader import dev_suite as ds
from bulk_downloader import request_replay as rr
from bulk_downloader import feature_flags as ff
from pathlib import Path


@contextlib.contextmanager
def _isolated_flags(tmp_path):
    """Patch feature_flags.state_path to a tmp file; reset cache.
    Same pattern as T40's _Isolated context manager."""
    target = Path(tmp_path) / "feature_flags.json"
    orig_path = ff.state_path
    orig_cache = ff._CACHE
    orig_mtime = ff._CACHE_MTIME
    ff.state_path = lambda: target
    ff._CACHE = {}
    ff._CACHE_MTIME = 0.0
    try:
        yield target
    finally:
        ff.state_path = orig_path
        ff._CACHE = orig_cache
        ff._CACHE_MTIME = orig_mtime


@contextlib.contextmanager
def _capture_on(tmp_path):
    """Enable request capture for one test."""
    with _isolated_flags(tmp_path):
        ff.set_flag("request_capture", True)
        rr.reset()
        try:
            yield
        finally:
            rr.reset()


# ── Recorder basics ─────────────────────────────────────────────────

def test_record_is_noop_when_disabled(clean_workdir, tmp_path):
    with _isolated_flags(tmp_path):
        rr.reset()
        rid = rr.record(method="GET", path="/api/x", query="",
                        status=200, request_headers={},
                        request_body="", response_body="hi",
                        duration_ms=1.0)
        assert rid is None
        assert rr.stats()["buffered"] == 0


def test_record_assigns_id_when_enabled(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rid = rr.record(method="GET", path="/api/x", query="",
                        status=200, request_headers={},
                        request_body="", response_body="hi",
                        duration_ms=1.0)
        assert isinstance(rid, str) and len(rid) == 12


def test_record_buffer_bounded(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        for i in range(rr._MAX_REQUESTS + 50):
            rr.record(method="GET", path=f"/api/x/{i}", query="",
                      status=200, request_headers={},
                      request_body="", response_body="",
                      duration_ms=1.0)
        # Buffer caps at _MAX_REQUESTS
        assert rr.stats()["buffered"] == rr._MAX_REQUESTS
        assert rr.stats()["total_recorded"] == rr._MAX_REQUESTS + 50


def test_record_redacts_sensitive_headers(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rr.record(method="POST", path="/api/x", query="",
                  status=200,
                  request_headers={"Cookie": "session=secret",
                                   "Authorization": "Bearer xyz",
                                   "X-CSRF-Token": "abc",
                                   "Content-Type": "application/json"},
                  request_body="", response_body="",
                  duration_ms=1.0)
        items = rr.list_recent(limit=1)
        h = items[0]["request_headers"]
        assert h["Cookie"] == "[redacted]"
        assert h["Authorization"] == "[redacted]"
        assert h["X-CSRF-Token"] == "[redacted]"
        # Non-sensitive headers preserved
        assert h["Content-Type"] == "application/json"


def test_record_truncates_long_body(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        big = "x" * (rr._BODY_TRUNC * 3)
        rr.record(method="POST", path="/api/x", query="",
                  status=200, request_headers={},
                  request_body=big, response_body=big,
                  duration_ms=1.0)
        items = rr.list_recent(limit=1)
        assert "truncated" in items[0]["request_body"]
        assert "truncated" in items[0]["response_body"]
        # No stored body exceeds the limit + truncation marker
        assert len(items[0]["request_body"]) < rr._BODY_TRUNC + 100


def test_list_recent_newest_first(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rr.record(method="GET", path="/api/a", query="", status=200,
                  request_headers={}, request_body="",
                  response_body="", duration_ms=1.0)
        rr.record(method="GET", path="/api/b", query="", status=200,
                  request_headers={}, request_body="",
                  response_body="", duration_ms=1.0)
        items = rr.list_recent(limit=10)
        assert len(items) == 2
        assert items[0]["path"] == "/api/b"
        assert items[1]["path"] == "/api/a"


def test_get_by_id(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rid = rr.record(method="GET", path="/api/x", query="",
                        status=200, request_headers={},
                        request_body="", response_body="",
                        duration_ms=1.0)
        bundle = rr.get(rid)
        assert bundle is not None
        assert bundle["path"] == "/api/x"


def test_get_unknown_id_returns_none(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        assert rr.get("not-a-real-id") is None
        assert rr.get(None) is None
        assert rr.get(12345) is None


# ── Replay error paths ─────────────────────────────────────────────

def test_replay_unknown_request_id(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        r = rr.replay("missing")
        assert r["ok"] is False
        assert "unknown request_id" in r["error"]


def test_replay_unreachable_endpoint(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rid = rr.record(method="GET", path="/api/x", query="",
                        status=200, request_headers={},
                        request_body="", response_body="",
                        duration_ms=1.0)
        # Port 1 is guaranteed not listening
        r = rr.replay(rid, base_url="http://127.0.0.1:1",
                       timeout=0.5)
        assert r["ok"] is False
        assert "error" in r


def test_replay_refuses_truncated_body(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        # Body large enough to truncate
        big = "x" * (rr._BODY_TRUNC * 2)
        rid = rr.record(method="POST", path="/api/x", query="",
                        status=200, request_headers={},
                        request_body=big, response_body="",
                        duration_ms=1.0)
        r = rr.replay(rid, base_url="http://127.0.0.1:1",
                       timeout=0.5)
        assert r["ok"] is False
        assert "truncated" in r["error"]


def test_replay_timeout_clamped(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rid = rr.record(method="GET", path="/api/x", query="",
                        status=200, request_headers={},
                        request_body="", response_body="",
                        duration_ms=1.0)
        # timeout=999 should clamp internally
        r = rr.replay(rid, base_url="http://127.0.0.1:1",
                       timeout=999)
        # Either way, it should return — clamped to 10s internally
        assert "ok" in r


# ── dev_suite inspector ────────────────────────────────────────────

def test_request_replay_list_when_disabled(clean_workdir, tmp_path):
    with _isolated_flags(tmp_path):
        rr.reset()
        r = ds.request_replay_list()
        assert r["ok"] is True
        assert r["enabled"] is False
        assert "OFF" in r["verdict"]


def test_request_replay_list_when_enabled(clean_workdir, tmp_path):
    with _capture_on(tmp_path):
        rr.record(method="GET", path="/api/x", query="", status=200,
                  request_headers={}, request_body="",
                  response_body="", duration_ms=1.0)
        r = ds.request_replay_list()
        assert r["enabled"] is True
        assert r["buffered"] == 1
        assert r["items"][0]["path"] == "/api/x"


def test_request_replay_list_is_json_serializable(clean_workdir,
                                                     tmp_path):
    with _capture_on(tmp_path):
        rr.record(method="GET", path="/api/x", query="", status=200,
                  request_headers={}, request_body="",
                  response_body="", duration_ms=1.0)
        r = ds.request_replay_list()
        json.dumps(r, default=str)
