"""O1826 C46: small correctness fixes (M165, M164, M190, M192, M071, M158).

One RED assertion per finding at base behaviour, plus a negative control
showing valid inputs keep their old answer.
"""

from __future__ import annotations

import json
import time

from bulk_downloader import (
    import_profiler,
    session_cache_service,
    shortcuts,
    site_editor,
    workload_bottleneck,
    ytdlp_updater,
)

BD_GATE_SCOPE = "module"


# -- M165: validate_config shares validate_numeric_updates ------------------


def _numeric_errors(result: dict) -> list:
    return [e for e in result["errors"] if e.startswith("'max_concurrent'")]


def test_m165_validate_config_rejects_fractional_int_field():
    errs = _numeric_errors(site_editor.validate_config({"max_concurrent": 2.5}))
    assert errs == ["'max_concurrent' must be a whole number (got 2.5)."]


def test_m165_validate_config_numeric_errors_match_updates_validator():
    cfg = {
        "max_concurrent": "3.5",
        "max_retries": float("nan"),
        "wait": 1.5,
        "parallel_chunks": 0,
        "chunk_size_mb": "abc",
    }
    expected = list(site_editor.validate_numeric_updates(cfg).values())
    got = [e for e in site_editor.validate_config(cfg)["errors"] if e in expected]
    assert expected and got == expected


def test_m165_negative_control_whole_numbers_accepted():
    for v in (2, 2.0, "2"):
        assert _numeric_errors(site_editor.validate_config({"max_concurrent": v})) == []


# -- M164: every shortcut binding is unique ----------------------------------


def test_m164_catalog_keys_are_unique():
    keys = [c["keys"] for c in shortcuts.CATALOG]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    assert dupes == []


def test_m164_negative_control_history_bindings_unchanged():
    by_action = {c["action"]: c["keys"] for c in shortcuts.CATALOG}
    assert by_action["retry_selected"] == "Shift+R"
    assert by_action["delete_selected"] == "Shift+D"
    assert {"reload_data", "diagnostics_download"} <= set(by_action)


# -- M190: record_metric(site_id=None) keeps the site filter -----------------


def _spiked_detector():
    det = workload_bottleneck.WorkloadBottleneckDetector()
    events = []
    det.register_callback(events.append)
    for v in (1.0, 1.1, 0.9, 1.0, 1.1, 0.9):
        det.record_metric("req_latency", v, site_id="site_a")
        det.record_metric("req_latency", v, site_id=None)
    det.record_metric("req_latency", 50.0, site_id="site_a")
    return det, events


def test_m190_default_site_ingest_does_not_reemit_other_sites():
    det, events = _spiked_detector()
    assert [e.site_id for e in events] == ["site_a"]
    det.record_metric("req_latency", 1.0, site_id=None)
    det.record_queue_latency(1.0, site_id=None)
    assert [e.site_id for e in events] == ["site_a"]


def test_m190_negative_control_default_site_spike_still_detected():
    det = workload_bottleneck.WorkloadBottleneckDetector()
    events = []
    det.register_callback(events.append)
    for v in (1.0, 1.1, 0.9, 1.0, 1.1, 0.9, 60.0):
        det.record_metric("req_latency", v, site_id=None)
    assert [e.site_id for e in events] == [None]
    # A sweep with no site argument still covers every site.
    det2, _ = _spiked_detector()
    assert [e.site_id for e in det2.detect_anomalies("req_latency")] == ["site_a"]


# -- M192: the on-disk 'latest' cache honours its stored ts ------------------


def _latest_env(monkeypatch, tmp_path, disk_age_s):
    monkeypatch.setenv("BD_HOME", str(tmp_path))
    monkeypatch.setattr(ytdlp_updater, "_LATEST_CACHE", {"ts": 0.0, "version": None})
    monkeypatch.setattr(ytdlp_updater, "_test_mode", lambda: False)
    (tmp_path / ytdlp_updater._LATEST_STATE).write_text(
        json.dumps({"version": "2020.1.1", "ts": time.time() - disk_age_s}),
        encoding="utf-8",
    )
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        return json.dumps({"info": {"version": "2026.9.9"}})

    return calls, fetch


def test_m192_stale_disk_cache_refreshes_when_fetch_allowed(monkeypatch, tmp_path):
    calls, fetch = _latest_env(monkeypatch, tmp_path, ytdlp_updater._LATEST_TTL + 60)
    assert ytdlp_updater.latest_version(allow_fetch=True, _fetch=fetch) == "2026.9.9"
    assert len(calls) == 1
    stored = json.loads(
        (tmp_path / ytdlp_updater._LATEST_STATE).read_text(encoding="utf-8")
    )
    assert stored["version"] == "2026.9.9"


def test_m192_negative_control_fresh_disk_cache_served_without_fetch(
    monkeypatch, tmp_path
):
    calls, fetch = _latest_env(monkeypatch, tmp_path, 60)
    assert ytdlp_updater.latest_version(allow_fetch=True, _fetch=fetch) == "2020.1.1"
    assert calls == []


def test_m192_negative_control_stale_disk_without_fetch_unchanged(
    monkeypatch, tmp_path
):
    calls, fetch = _latest_env(monkeypatch, tmp_path, ytdlp_updater._LATEST_TTL + 60)
    assert ytdlp_updater.latest_version(allow_fetch=False, _fetch=fetch) == "2020.1.1"
    assert calls == []


# -- M071: a missing budget root is not a zero ------------------------------

_ROWS = [
    {"module": "site", "self_us": 10, "cumulative_us": 40, "depth": 0},
    {"module": "bulk_downloader", "self_us": 100, "cumulative_us": 900, "depth": 0},
]


def test_m071_missing_root_is_not_ok():
    rep = import_profiler.check_budget(_ROWS, 10_000, root="bulk_downloader.absent")
    assert rep["ok"] is False
    assert "bulk_downloader.absent" in rep["error"]


def test_m071_negative_control_present_root_unchanged():
    rep = import_profiler.check_budget(_ROWS, 10_000, root="bulk_downloader")
    assert (rep["ok"], rep["total_us"], rep["over_us"]) == (True, 900, 0)
    assert "error" not in rep
    over = import_profiler.check_budget(_ROWS, 500, root="bulk_downloader")
    assert (over["ok"], over["over_us"]) == (False, 400)


# -- M158: the docstring no longer claims cross-node replication -------------


def test_m158_docstring_does_not_claim_replication():
    doc = (session_cache_service.__doc__ or "").lower()
    assert "replicat" not in doc
    assert "in-process" in doc


def test_m158_negative_control_store_is_per_instance():
    a = session_cache_service.SessionCacheService()
    b = session_cache_service.SessionCacheService()
    a.write("n1", "site", {"cookies": {}})
    assert a.read("n2", "site") == {"cookies": {}}
    assert b.read("n1", "site") is None
