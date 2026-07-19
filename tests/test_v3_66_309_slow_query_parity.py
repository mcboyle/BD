"""v3.66.309 — CLI->GUI parity Phase 4.2: slow-query group promoted full +
HLS/Live import-time constants reclassified display-only.

Honest classification cut: BD_SLOW_QUERY_LOG / BD_SLOW_QUERY_MS are read at
call time (db.py getters) -> genuinely runtime-tunable -> promoted to global_config
(store > env > default). The 7 HLS/Live env vars are MODULE-LEVEL constants bound
once at import (hls_downloader.py / live_recorder.py) -> not hot-swappable -> marked
import_time so the inventory targets them display-only and drops them from the
runtime-tunable open count (their read-only SPA panel batches into 4.6).

RED-first: every assertion fails on pristine v3.66.308. Custom runner; zero-arg
tests; restore globals in try/finally.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

# v3.66.503 (Bucket 1): these 7 HLS/Live tunables were promoted from import-time
# display-only to full live controls (call-time getters). Kept here to assert the
# new full classification (was: assert display-only / not runtime_tunable).
_PROMOTED_503 = (
    "BD_HLS_INPUT_TIMEOUT_US", "BD_HLS_MAX_RUNTIME_S", "BD_HLS_PROGRESS_POLL_S",
    "BD_LIVE_DISCONNECT_TOLERANCE_S", "BD_LIVE_LAUNCH_TIMEOUT_S",
    "BD_LIVE_MAX_ACTIVE_RECORDINGS", "BD_LIVE_POLL_INTERVAL_S",
)


def _fresh_store(d: dict) -> None:
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


# ── schema ─────────────────────────────────────────────────────────────────
def test_schema_has_slow_query_keys():
    from bulk_downloader import global_config as GC
    s = GC.GLOBAL_CONFIG_SCHEMA
    assert s["slow_query_log"]["type"] is bool
    assert s["slow_query_ms"]["type"] is int


# ── db.py getters honor the store over the env seed ──────────────────────────
def test_slow_query_log_store_over_env():
    from bulk_downloader import db as DB
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_SLOW_QUERY_LOG")
    try:
        os.environ.pop("BD_SLOW_QUERY_LOG", None)
        _fresh_store({"slow_query_log": False})
        assert DB._slow_query_log_enabled() is False        # store off, env unset
        os.environ["BD_SLOW_QUERY_LOG"] = "0"
        _fresh_store({"slow_query_log": True})
        assert DB._slow_query_log_enabled() is True          # store wins over env=0
    finally:
        if saved is None:
            os.environ.pop("BD_SLOW_QUERY_LOG", None)
        else:
            os.environ["BD_SLOW_QUERY_LOG"] = saved
        os.chdir(cwd)


def test_slow_query_ms_store_over_env():
    from bulk_downloader import db as DB
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    saved = os.environ.get("BD_SLOW_QUERY_MS")
    try:
        os.environ.pop("BD_SLOW_QUERY_MS", None)
        _fresh_store({"slow_query_ms": 250})
        assert DB._slow_query_threshold_ms() == 250
        _fresh_store({"slow_query_ms": "bogus"})             # bad value -> default
        assert DB._slow_query_threshold_ms() == 100
    finally:
        if saved is None:
            os.environ.pop("BD_SLOW_QUERY_MS", None)
        else:
            os.environ["BD_SLOW_QUERY_MS"] = saved
        os.chdir(cwd)


# ── generic write path persists slow-query (free via 308's path; assert it) ──
def test_post_persists_slow_query():
    cwd = os.getcwd(); tmp = tempfile.mkdtemp(); os.chdir(tmp)
    try:
        from bulk_downloader import app as A
        from bulk_downloader import global_config as GC
        GC._cached = None; GC._cached_mtime = 0.0
        c = A.app.test_client()
        r = c.post("/api/global_config", json={"slow_query_log": False, "slow_query_ms": 250})
        assert r.status_code == 200, r.status_code
        assert GC.get("slow_query_log", "<unset>") is False
        assert GC.get("slow_query_ms", "<unset>") == 250
    finally:
        os.chdir(cwd)


# ── inventory: slow-query full; HLS/Live display-only + out of open ──────────
def test_inventory_slow_query_full():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in ("BD_SLOW_QUERY_LOG", "BD_SLOW_QUERY_MS"):
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])


def test_inventory_hls_live_now_full():
    # v3.66.503: promoted import-time -> full (call-time getters via runtime_flags.num).
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"] if it["kind"] == "env_var"}
    for k in _PROMOTED_503:
        assert items[k]["runtime_tunable"] is True, k
        assert items[k]["parity_target"] == "full", k
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])


def test_ratchet_baseline_dropped_to_141():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    # Ceiling, not equality (ratchet-pin discipline): later parity cuts lower it.
    assert base["open_count"] <= 141, base["open_count"]
    for k in ("BD_SLOW_QUERY_LOG", "BD_SLOW_QUERY_MS") + _PROMOTED_503:
        assert k not in base["open"], k


def test_manifest_ledgers_slow_query():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text())
    exposed = m.get("exposed", m)
    assert exposed.get("BD_SLOW_QUERY_LOG") == "full"
    assert exposed.get("BD_SLOW_QUERY_MS") == "full"
