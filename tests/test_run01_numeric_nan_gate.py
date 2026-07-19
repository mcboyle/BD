"""F-RUN01-03 -- config numeric disk/bandwidth gates must reject non-finite
(NaN / inf) values so a NaN cannot silently disable a safety gate.

runner.start() and SiteRunner._effective_concurrency() coerce config-sourced
numeric gates with a bare float(): disk_threshold_gb (the low_disk hard stop and
the disk-pressure soft throttle) and bandwidth_target_mbps. float('nan') parses
without error and evades every downstream comparison -- ``free < NaN`` is always
False -- so a NaN disk_threshold_gb DISABLES the disk gate entirely (workers are
never throttled and the queue keeps starting past a full disk).

RED on pinned source: with a NaN disk_threshold_gb and low free space, the
disk-pressure throttle in _effective_concurrency does NOT fire (returns the full
max_concurrent), and the shared coercion helper does not exist. GREEN once a
math.isfinite backstop (the _finite_config_float helper) rejects the non-finite
value and falls back to the safe default.
"""
import types

import pytest

import bulk_downloader.runner as runner


def _eff_conc(config, free_gb, monkeypatch):
    """Drive SiteRunner._effective_concurrency on a minimal fake self, with
    disk_free_gb mocked to a fixed value so the throttle math is deterministic."""
    monkeypatch.setattr(runner, "disk_free_gb", lambda *a, **k: free_gb)
    fake = types.SimpleNamespace(config=config, log_event=lambda *a, **k: None)
    return runner.SiteRunner._effective_concurrency(fake)


def test_nan_disk_threshold_does_not_disable_throttle(monkeypatch):
    cap = 8
    config = {"max_concurrent": cap, "download_dir": "/tmp",
              "bandwidth_target_mbps": 0, "disk_threshold_gb": float("nan")}
    # 1 GB free is well under any sane threshold -> the disk-pressure throttle
    # MUST engage. A NaN threshold must not silently disable it.
    workers = _eff_conc(config, 1.0, monkeypatch)
    assert workers < cap, (
        f"NaN disk_threshold_gb disabled the disk-pressure throttle "
        f"(workers={workers}, cap={cap}) -- the safety gate was evaded")


def test_finite_low_disk_threshold_throttles(monkeypatch):
    # control: a finite threshold with low free space throttles (unchanged).
    cap = 8
    config = {"max_concurrent": cap, "download_dir": "/tmp",
              "bandwidth_target_mbps": 0, "disk_threshold_gb": 2.0}
    workers = _eff_conc(config, 1.0, monkeypatch)
    assert workers < cap


def test_ample_disk_no_throttle(monkeypatch):
    # control: ample free space -> no throttle (full max_concurrent).
    cap = 8
    config = {"max_concurrent": cap, "download_dir": "/tmp",
              "bandwidth_target_mbps": 0, "disk_threshold_gb": 2.0}
    workers = _eff_conc(config, 1000.0, monkeypatch)
    assert workers == cap


@pytest.mark.parametrize("raw,default,expected", [
    (float("nan"), 2.0, 2.0),
    (float("inf"), 2.0, 2.0),
    (float("-inf"), 2.0, 2.0),
    ("nan", 2.0, 2.0),
    ("inf", 2.0, 2.0),
    ("not-a-number", 2.0, 2.0),
    (None, 2.0, 2.0),
    (3.5, 2.0, 3.5),
    (0, 5.0, 0.0),
    ("7.5", 2.0, 7.5),
])
def test_finite_config_float_helper(raw, default, expected):
    # the shared coercion helper: non-finite / non-numeric -> default; a finite
    # numeric value passes through. (RED on pinned source: helper does not exist.)
    assert runner._finite_config_float(raw, default) == expected
