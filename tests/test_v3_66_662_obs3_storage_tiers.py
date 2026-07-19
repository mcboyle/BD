"""v3.66.662 -- OBS-3: read-only storage-tier dashboard.

storage_rebalance.inventory() already returns per-path {total_gb, free_gb, used_gb,
free_pct, mount_id, file_count}, but the only surfaces were POST /api/storage_rebalance/*
(operator supplies paths) and GET /api/storage/validate (one path). There was no
read-only occupancy/headroom fleet view. This adds collect_storage_tiers() -- gathers the
configured download dirs from the live s_cfg, runs inventory over them, and returns
per-tier occupancy + fleet totals + low-headroom flags -- exposed via GET
/api/data/storage_tiers (the data-layer collector pattern). No writes, no rebalance;
surfacing only.
"""
from bulk_downloader import app_data_layer as adl


def _seed(monkeypatch, inv, sites):
    from bulk_downloader import app_state, storage_rebalance as sr
    monkeypatch.setattr(app_state, "s_cfg", sites, raising=False)
    monkeypatch.setattr(sr, "inventory", lambda paths: inv)


def test_collect_storage_tiers_summarizes(monkeypatch):
    inv = [
        {"path": "/hot", "total_gb": 100.0, "free_gb": 40.0, "used_gb": 60.0, "free_pct": 40.0},
        {"path": "/cold", "total_gb": 200.0, "free_gb": 5.0, "used_gb": 195.0, "free_pct": 2.5},
    ]
    _seed(monkeypatch, inv, {"a": {"download_dir": "/hot"}, "b": {"download_dir": "/cold"}})
    out = adl.collect_storage_tiers()
    assert out["tier_count"] == 2
    assert out["total_gb"] == 300.0
    assert out["free_gb"] == 45.0
    assert out["used_gb"] == 255.0
    assert len(out["tiers"]) == 2


def test_low_headroom_flagged(monkeypatch):
    inv = [
        {"path": "/ok", "total_gb": 100.0, "free_gb": 40.0, "used_gb": 60.0, "free_pct": 40.0},
        {"path": "/tight", "total_gb": 200.0, "free_gb": 5.0, "used_gb": 195.0, "free_pct": 2.5},
    ]
    _seed(monkeypatch, inv, {"a": {"download_dir": "/ok"}, "b": {"download_dir": "/tight"}})
    out = adl.collect_storage_tiers()
    low_paths = {x["path"] for x in out["low_headroom"]}
    assert "/tight" in low_paths   # under 10% free
    assert "/ok" not in low_paths


def test_empty_config_is_safe(monkeypatch):
    _seed(monkeypatch, [], {})
    out = adl.collect_storage_tiers()
    assert out["tier_count"] == 0
    assert out["low_headroom"] == []
    assert out["pct_free"] is None


def test_dedupes_and_skips_blank_dirs(monkeypatch):
    seen = {}
    from bulk_downloader import app_state, storage_rebalance as sr
    monkeypatch.setattr(app_state, "s_cfg",
                        {"a": {"download_dir": "/x"}, "b": {"download_dir": "/x"},
                         "c": {"download_dir": ""}, "d": {}}, raising=False)
    def _inv(paths):
        seen["paths"] = paths
        return []
    monkeypatch.setattr(sr, "inventory", _inv)
    adl.collect_storage_tiers()
    assert seen["paths"] == ["/x"], "paths must be deduped and blanks dropped"


def test_route_registered():
    from bulk_downloader.app import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    assert "/api/data/storage_tiers" in rules
