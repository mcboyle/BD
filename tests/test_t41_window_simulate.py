"""T41 (Tier 4) — D-16 download-window simulator.

Stateless wrapper around download_window.site_in_window(). Takes a
spec + sample timestamps; reports which are in-window and where the
transitions occur.
"""
import json

from bulk_downloader import dev_suite as ds


def test_window_simulate_full_day_business_hours(clean_workdir):
    samples = [
        "2026-05-20T08:00:00",
        "2026-05-20T09:00:00",
        "2026-05-20T12:00:00",
        "2026-05-20T17:00:00",
        "2026-05-20T18:00:00",
    ]
    r = ds.window_simulate(window_spec="09:00-17:00", samples=samples)
    assert r["ok"] is True
    assert r["tool"] == "window_simulate"
    by_ts = {s["ts"]: s["open"] for s in r["samples"]}
    assert by_ts["2026-05-20T08:00"] is False
    assert by_ts["2026-05-20T09:00"] is True
    assert by_ts["2026-05-20T12:00"] is True
    # 17:00 is the closing edge → in_window is strict less-than → False
    assert by_ts["2026-05-20T17:00"] is False
    assert by_ts["2026-05-20T18:00"] is False


def test_window_simulate_midnight_crossing(clean_workdir):
    samples = [
        "2026-05-20T21:00:00",
        "2026-05-20T22:00:00",
        "2026-05-20T23:30:00",
        "2026-05-20T05:00:00",
        "2026-05-20T06:00:00",
        "2026-05-20T08:00:00",
    ]
    r = ds.window_simulate(window_spec="22:00-06:00", samples=samples)
    by_ts = {s["ts"]: s["open"] for s in r["samples"]}
    assert by_ts["2026-05-20T21:00"] is False
    assert by_ts["2026-05-20T22:00"] is True
    assert by_ts["2026-05-20T23:30"] is True
    assert by_ts["2026-05-20T05:00"] is True
    assert by_ts["2026-05-20T06:00"] is False
    assert by_ts["2026-05-20T08:00"] is False


def test_window_simulate_parsed_windows_reported(clean_workdir):
    r = ds.window_simulate(window_spec="09:00-17:00", samples=[])
    assert len(r["parsed_windows"]) == 1
    w = r["parsed_windows"][0]
    assert w["start_min"] == 540
    assert w["end_min"] == 1020
    assert w["crosses_midnight"] is False


def test_window_simulate_midnight_window_flagged_crosses(clean_workdir):
    r = ds.window_simulate(window_spec="22:00-06:00", samples=[])
    w = r["parsed_windows"][0]
    assert w["crosses_midnight"] is True


def test_window_simulate_multiple_windows(clean_workdir):
    r = ds.window_simulate(window_spec="08:00-12:00,14:00-18:00",
                            samples=[])
    assert len(r["parsed_windows"]) == 2


def test_window_simulate_disabled_means_always_open(clean_workdir):
    samples = ["2026-05-20T03:00:00", "2026-05-20T15:00:00"]
    r = ds.window_simulate(window_spec="09:00-17:00",
                            samples=samples,
                            window_enabled=False)
    for s in r["samples"]:
        assert s["open"] is True


def test_window_simulate_empty_spec_means_no_restriction(clean_workdir):
    samples = ["2026-05-20T03:00:00", "2026-05-20T15:00:00"]
    r = ds.window_simulate(window_spec="", samples=samples)
    # Empty spec → no parsed windows → site_in_window returns True
    for s in r["samples"]:
        assert s["open"] is True


def test_window_simulate_default_hourly_samples(clean_workdir):
    r = ds.window_simulate(window_spec="09:00-17:00")
    assert len(r["samples"]) == 24


def test_window_simulate_default_samples_open_fraction(clean_workdir):
    # 09:00-17:00 → 8 open hours out of 24 → 8/24 = 0.333...
    r = ds.window_simulate(window_spec="09:00-17:00")
    assert 0.30 <= r["open_fraction"] <= 0.36


def test_window_simulate_transitions_detected(clean_workdir):
    samples = [
        "2026-05-20T08:00:00",  # closed
        "2026-05-20T09:00:00",  # open → transition to open
        "2026-05-20T16:00:00",  # open
        "2026-05-20T17:00:00",  # closed → transition to closed
    ]
    r = ds.window_simulate(window_spec="09:00-17:00", samples=samples)
    assert len(r["transitions"]) == 2
    assert r["transitions"][0]["to"] == "open"
    assert r["transitions"][1]["to"] == "closed"


def test_window_simulate_malformed_sample_skipped(clean_workdir):
    samples = ["not-a-timestamp", "2026-05-20T10:00:00"]
    r = ds.window_simulate(window_spec="09:00-17:00", samples=samples)
    # Only the valid sample makes it through
    assert len(r["samples"]) == 1


def test_window_simulate_is_json_serializable(clean_workdir):
    r = ds.window_simulate(window_spec="09:00-17:00")
    json.dumps(r)
