"""T35 (Tier 4) — D-117 storage-tier inspector.

Read-only surface over the existing storage_tier subsystem. Calls
get_scheduler().get_status() and find_candidates() per site.
"""
import json

from bulk_downloader import dev_suite as ds


def test_storage_tier_status_no_sites(clean_workdir):
    r = ds.storage_tier_status(site_configs={})
    assert r["ok"] is True
    assert r["tool"] == "storage_tier_status"
    assert r["per_site"] == []
    assert r["total_candidates"] == 0
    assert "scheduler" in r


def test_storage_tier_status_site_without_tier_config(clean_workdir):
    cfg = {"siteA": {"download_dir": "/tmp/x"}}  # no storage_tier key
    r = ds.storage_tier_status(site_configs=cfg)
    assert r["ok"] is True
    assert len(r["per_site"]) == 1
    assert r["per_site"][0]["site_id"] == "siteA"
    assert r["per_site"][0]["configured"] is False
    assert r["per_site"][0]["candidate_count"] == 0


def test_storage_tier_status_site_with_tier_disabled(clean_workdir):
    cfg = {"siteA": {"storage_tier": {"enabled": False,
                                       "age_days": 30}}}
    r = ds.storage_tier_status(site_configs=cfg)
    assert r["per_site"][0]["configured"] is False


def test_storage_tier_status_site_with_tier_enabled(clean_workdir):
    cfg = {"siteA": {"storage_tier": {"enabled": True,
                                       "age_days": 30,
                                       "min_size_mb": 100}}}
    r = ds.storage_tier_status(site_configs=cfg)
    entry = r["per_site"][0]
    assert entry["configured"] is True
    assert entry["age_days"] == 30
    assert entry["min_size_mb"] == 100
    assert "candidate_count" in entry


def test_storage_tier_status_defensive_against_non_dict_cfg(clean_workdir):
    # If a site cfg comes in as something weird (None, list), skip
    cfg = {"siteA": None, "siteB": [1, 2, 3]}
    r = ds.storage_tier_status(site_configs=cfg)
    assert r["ok"] is True
    # Neither dict-typed; both skipped
    assert r["per_site"] == []


def test_storage_tier_status_defensive_against_bad_age_days(clean_workdir):
    # Non-numeric age_days should default rather than crash
    cfg = {"siteA": {"storage_tier": {"enabled": True,
                                       "age_days": "not-a-number"}}}
    r = ds.storage_tier_status(site_configs=cfg)
    assert r["ok"] is True
    assert r["per_site"][0]["configured"] is True
    # Defaulted to 30
    assert r["per_site"][0]["age_days"] == 30


def test_storage_tier_status_verdict_summary(clean_workdir):
    cfg = {
        "siteA": {"storage_tier": {"enabled": True, "age_days": 30}},
        "siteB": {"storage_tier": {"enabled": False}},
    }
    r = ds.storage_tier_status(site_configs=cfg)
    assert "1/2" in r["verdict"]


def test_storage_tier_status_scheduler_shape(clean_workdir):
    r = ds.storage_tier_status(site_configs={})
    # Scheduler block has at least these keys (real-running flag,
    # interval). It's read-only; we don't start anything.
    sched = r["scheduler"]
    if sched and "error" not in sched:
        assert "running" in sched
        assert "interval_seconds" in sched


def test_storage_tier_status_is_json_serializable(clean_workdir):
    cfg = {"siteA": {"storage_tier": {"enabled": True,
                                       "age_days": 30}}}
    r = ds.storage_tier_status(site_configs=cfg)
    json.dumps(r)
