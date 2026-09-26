"""v3.66.1267 -- expose MOD3 telemetry in /api/health and /api/health/v2 (Row 127).

Guards the machine-readable surface for the 14-day PostgreSQL soak:
1. /api/health and /api/health/v2 expose `mod3` with dual_write, shadow_read,
   cutover_requested, cutover_engaged, stats, and shadow.
   cutover_engaged is always None (not evaluated: it needs a PG round-trip).
2. Read of in-process counters is pure and fail-safe: never raises, never connects
   (asserted with MOD3_CUTOVER armed on a blackhole DSN),
   never degrades `ok` or `db_ok`.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from bulk_downloader import app_health, pg_backend
from flask import Flask

BD_GATE_SCOPE = "module"

_SHADOW_KEYS = frozenset({"compared", "matched", "diverged", "skipped",
                          "errors", "last_divergence"})


@pytest.fixture(autouse=True)
def _isolated_mod3_counters(monkeypatch):
    """pg_backend counters are process-global; the postgres-integration shard
    runs 800/801/803/804/1266/1678 in the same process and they leave nonzero
    counts behind. Zero every key per test, restored on teardown, so this
    module's exact-value assertions hold in any order."""
    for k in ("mirrored", "skipped", "failed"):
        monkeypatch.setitem(pg_backend._stats, k, 0)
    monkeypatch.setitem(pg_backend._stats, "degraded_reason", None)
    for k in _SHADOW_KEYS - {"last_divergence"}:
        monkeypatch.setitem(pg_backend._shadow, k, 0)
    monkeypatch.setitem(pg_backend._shadow, "last_divergence", None)


@contextmanager
def _mem_db() -> Iterator[sqlite3.Connection]:
    cx = sqlite3.connect(":memory:")
    try:
        yield cx
    finally:
        cx.close()


def _client(monkeypatch) -> Flask.test_client:
    monkeypatch.setattr(app_health, "db_conn", _mem_db)
    monkeypatch.setattr(app_health, "_app_runners", dict)
    monkeypatch.setattr(app_health, "_app_s_cfg", dict)
    monkeypatch.setattr(app_health, "_app__app_boot_time", lambda: 0.0)
    monkeypatch.setattr(app_health, "app_test_mode", lambda: False)
    monkeypatch.setattr(
        app_health, "_attach_credential_health",
        lambda p, s: p.update({"vault_ready": True}),
    )
    monkeypatch.setattr(
        app_health, "build_identity",
        lambda _install_dir: {"sha": None, "built_at": None, "source": "unknown"},
    )
    app = Flask("row127-health")
    app.register_blueprint(app_health.health_bp)
    return app.test_client()


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_health_exposes_mod3_telemetry(monkeypatch, path):
    client = _client(monkeypatch)
    res = client.get(path)
    assert res.status_code == 200, (res.status_code, res.get_data(as_text=True))
    payload = res.get_json()
    assert "mod3" in payload, f"'mod3' key missing from {path} payload"


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_mod3_payload_exact_keys_and_types(monkeypatch, path):
    client = _client(monkeypatch)
    res = client.get(path)
    assert res.status_code == 200
    payload = res.get_json()
    mod3 = payload["mod3"]
    assert isinstance(mod3, dict)

    # Exact-count 6 keys on mod3 object
    expected_keys = {
        "dual_write",
        "shadow_read",
        "cutover_requested",
        "cutover_engaged",
        "stats",
        "shadow",
    }
    assert set(mod3.keys()) == expected_keys
    assert len(mod3) == 6

    assert isinstance(mod3["dual_write"], bool)
    assert isinstance(mod3["shadow_read"], bool)
    assert isinstance(mod3["cutover_requested"], bool)
    # None = not evaluated: engagement needs a PG round-trip (see
    # _attach_mod3_health); health never connects.
    assert mod3["cutover_engaged"] is None

    # stats object
    stats = mod3["stats"]
    assert isinstance(stats, dict)
    expected_stats_keys = {"mirrored", "skipped", "failed", "degraded_reason"}
    assert set(stats.keys()) == expected_stats_keys
    assert len(stats) == 4

    # shadow object verbatim from shadow_stats()
    shadow = mod3["shadow"]
    assert isinstance(shadow, dict)
    assert set(shadow.keys()) == _SHADOW_KEYS
    assert len(shadow) == 6


def test_negative_control_dsn_unset(monkeypatch):
    monkeypatch.delenv("MOD3_PG_DSN", raising=False)
    monkeypatch.delenv("MOD3_SHADOW_READ", raising=False)
    monkeypatch.delenv("MOD3_CUTOVER", raising=False)
    client = _client(monkeypatch)
    res = client.get("/api/health")
    assert res.status_code == 200
    payload = res.get_json()
    mod3 = payload["mod3"]
    assert mod3["dual_write"] is False
    assert mod3["shadow_read"] is False
    assert mod3["cutover_requested"] is False
    assert mod3["cutover_engaged"] is None
    assert mod3["stats"]["mirrored"] == 0
    assert mod3["stats"]["skipped"] == 0
    assert mod3["stats"]["failed"] == 0
    assert mod3["stats"]["degraded_reason"] is None


def test_pg_backend_exception_failsafe(monkeypatch):
    def _exploding_stats():
        raise RuntimeError("simulated pg_backend failure")

    monkeypatch.setattr(pg_backend, "stats", _exploding_stats)
    client = _client(monkeypatch)
    res = client.get("/api/health")
    # Health endpoint still returns 200 and ok=True
    assert res.status_code == 200
    payload = res.get_json()
    assert payload["ok"] is True
    mod3 = payload["mod3"]
    assert "error" in mod3
    assert len(mod3) == 1
    assert "simulated pg_backend failure" in mod3["error"]


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_cutover_armed_health_never_connects(monkeypatch, path):
    """REFUTE fix (VERDICT-correctness-bd-review-correctness-A1-A): with
    cutover requested, the health probe must not reach preflight_cutover()'s
    PG round-trip. Blackhole DSN: a real connect blocks connect_timeout (5 s)."""
    import time

    psycopg = pytest.importorskip("psycopg")
    monkeypatch.setenv("MOD3_PG_DSN", "postgresql://u:p@10.255.255.1:5432/x")
    monkeypatch.setenv("MOD3_SHADOW_READ", "1")
    monkeypatch.setenv("MOD3_CUTOVER", "1")
    connects = []
    real_connect = psycopg.connect

    def counting_connect(*a, **kw):
        connects.append(a)
        return real_connect(*a, **kw)

    monkeypatch.setattr(psycopg, "connect", counting_connect)
    client = _client(monkeypatch)
    t0 = time.monotonic()
    res = client.get(path)
    elapsed = time.monotonic() - t0
    assert res.status_code == 200
    mod3 = res.get_json()["mod3"]
    assert len(connects) == 0, f"{path} opened {len(connects)} PG connection(s)"
    assert elapsed < 1.0, f"{path} took {elapsed:.2f}s"
    assert mod3["cutover_requested"] is True
    assert mod3["dual_write"] is True and mod3["shadow_read"] is True
    assert mod3["cutover_engaged"] is None
    assert mod3["stats"]["degraded_reason"] is None


@pytest.mark.parametrize("path", ["/api/health", "/api/health/v2"])
def test_counter_values_come_from_pg_backend(monkeypatch, path):
    """Shape REFUTE (VERDICT-shape-bd-review-shape-A2-A): every counter the
    payload carries must be pg_backend's live value, not a constant. Distinct
    nonzero sentinels per key; a hard-coded-zeros payload fails here."""
    stats = {"mirrored": 11, "skipped": 12, "failed": 13,
             "degraded_reason": "sentinel-degraded"}
    shadow = {"compared": 21, "matched": 22, "diverged": 23, "skipped": 24,
              "errors": 25, "last_divergence": {"sql": "sentinel", "pg_rows": 26}}
    assert set(shadow) == _SHADOW_KEYS
    for k, v in stats.items():
        monkeypatch.setitem(pg_backend._stats, k, v)
    for k, v in shadow.items():
        monkeypatch.setitem(pg_backend._shadow, k, v)
    res = _client(monkeypatch).get(path)
    assert res.status_code == 200
    mod3 = res.get_json()["mod3"]
    assert mod3["stats"] == stats
    assert mod3["shadow"] == shadow
