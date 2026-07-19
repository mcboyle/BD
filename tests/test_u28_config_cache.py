"""U28 — config hot-reload (D-119) + cache-clear (D-123). A
maintenance (A) pair — both POST + CSRF + dev-gate.
"""
import json

from bulk_downloader import dev_suite as ds
from bulk_downloader import app as bd_app
from bulk_downloader import hls_downloader


def _api_status_handler():
    """Resolve the api_status view wherever it currently lives. P4
    (v3.66.436): api_status was extracted from app.py onto the
    app_status blueprint, so it is no longer an attribute of the app
    module — prefer the app module for older trees, fall back to the
    blueprint module."""
    h = getattr(bd_app, "api_status", None)
    if h is None:
        from bulk_downloader import app_status as _as
        h = getattr(_as, "api_status", None)
    return h


# ── config_hot_reload (D-119) ──────────────────────────────────────

def test_hot_reload_runs_cleanly(clean_workdir):
    r = ds.config_hot_reload()
    assert r["ok"] is True
    assert r["reloaded"] is True
    # the merge-reload caveat must be surfaced honestly
    assert r["merge_reload"] is True
    assert "restart" in r["note"]


def test_hot_reload_picks_up_an_on_disk_change(clean_workdir):
    # write a changed app_config.json, then reload and confirm the
    # watched keys reflect it
    bd_app.APP_CFG_FILE.write_text(
        json.dumps({"log_level": "DEBUG",
                    "global_max_concurrent": 9}),
        encoding="utf-8")
    try:
        r = ds.config_hot_reload()
        changed = r["changed_keys"]
        assert "global_max_concurrent" in changed
        assert changed["global_max_concurrent"]["to"] == 9
        assert "watched key(s) changed" in r["verdict"]
    finally:
        # restore a clean state for other tests in the process
        bd_app._app_cfg["global_max_concurrent"] = 0


# ── cache_clear (D-123) ────────────────────────────────────────────

def test_cache_clear_all_targets(clean_workdir):
    # populate every cache, then clear and confirm each is reset
    # v3.66.6: save/restore globals — see test_cache_clear_single_target
    # for the diagnosis behind this. Even though cache_clear() resets
    # _INDEX_HTML_CACHE to None at the end of this test (which would
    # be fine — None is the initial state), it's safer to restore the
    # exact value we saw on entry so a future change to cache_clear()
    # can't reintroduce the leak.
    _had_index = hasattr(bd_app, "_INDEX_HTML_CACHE")
    saved_index = getattr(bd_app, "_INDEX_HTML_CACHE", None)
    saved_ffmpeg = hls_downloader._FFMPEG_CACHE
    saved_disk = dict(getattr(_api_status_handler(), "_disk_cache", {}) or {})
    try:
        bd_app._INDEX_HTML_CACHE = "<cached>"
        hls_downloader._FFMPEG_CACHE = "/usr/bin/ffmpeg"
        _api_status_handler()._disk_cache = {"d": 1}
        r = ds.cache_clear()
        assert r["ok"] is True
        assert set(r["cleared"]) == {"index_html", "disk_cache", "ffmpeg"}
        assert r["failed"] == []
        assert bd_app._INDEX_HTML_CACHE is None
        assert hls_downloader._FFMPEG_CACHE is None
        assert _api_status_handler()._disk_cache == {}
    finally:
        if _had_index:
            bd_app._INDEX_HTML_CACHE = saved_index
        elif hasattr(bd_app, "_INDEX_HTML_CACHE"):
            delattr(bd_app, "_INDEX_HTML_CACHE")  # legacy cache removed; don't leave it behind
        hls_downloader._FFMPEG_CACHE = saved_ffmpeg
        _api_status_handler()._disk_cache = saved_disk


def test_cache_clear_single_target(clean_workdir):
    # v3.66.6: this test pokes a module-level global
    # (bd_app._INDEX_HTML_CACHE). Without restoration the poisoned
    # "<cached>" value leaks into any later test that calls / on the
    # Flask app — and the index() handler's html.replace("<!--CSRF_META-->",
    # csrf_meta, 1) becomes a no-op, returning the literal "<cached>"
    # string with no meta tag. This was harmless when the harness ran
    # each test file in its own subprocess, but on nproc=1 systems the
    # harness runs serially in-process and the poison propagates to
    # tests/test_v3_43_55_csrf_bootstrap.py downstream. Diagnosed via
    # heavy instrumentation in a session-end full-suite run.
    _had_index = hasattr(bd_app, "_INDEX_HTML_CACHE")
    saved_index = getattr(bd_app, "_INDEX_HTML_CACHE", None)
    saved_ffmpeg = hls_downloader._FFMPEG_CACHE
    try:
        bd_app._INDEX_HTML_CACHE = "<cached>"
        hls_downloader._FFMPEG_CACHE = "/usr/bin/ffmpeg"
        r = ds.cache_clear(targets="ffmpeg")
        assert r["cleared"] == ["ffmpeg"]
        # only the requested cache is touched
        assert hls_downloader._FFMPEG_CACHE is None
        assert bd_app._INDEX_HTML_CACHE == "<cached>"
    finally:
        if _had_index:
            bd_app._INDEX_HTML_CACHE = saved_index
        elif hasattr(bd_app, "_INDEX_HTML_CACHE"):
            delattr(bd_app, "_INDEX_HTML_CACHE")  # legacy cache removed; don't leave it behind
        hls_downloader._FFMPEG_CACHE = saved_ffmpeg


def test_cache_clear_rejects_unknown_target(clean_workdir):
    r = ds.cache_clear(targets="not_a_cache")
    assert r["ok"] is False
    assert "unknown cache target" in r["error"]


def test_cache_clear_accepts_comma_list(clean_workdir):
    bd_app._INDEX_HTML_CACHE = "<cached>"
    hls_downloader._FFMPEG_CACHE = "/usr/bin/ffmpeg"
    r = ds.cache_clear(targets="index_html,ffmpeg")
    assert set(r["cleared"]) == {"index_html", "ffmpeg"}


# ── routes ─────────────────────────────────────────────────────────

def test_config_reload_route(fresh_app):
    resp = fresh_app.post("/api/dev/config_reload", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["reloaded"] is True


def test_cache_clear_route_defaults_to_all(fresh_app):
    resp = fresh_app.post("/api/dev/cache_clear", json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert len(body["cleared"]) >= 1


def test_cache_clear_route_with_targets(fresh_app):
    resp = fresh_app.post("/api/dev/cache_clear",
                          json={"targets": "ffmpeg"})
    assert resp.status_code == 200
    assert resp.get_json()["cleared"] == ["ffmpeg"]
