"""dah-hls-runtime (O1789 deep-adapters-http F5): a non-positive hls_max_runtime_s
must not fail every HLS transfer at once.

The Settings key is a free string (global_config type str). "0" or "-5" cast
cleanly, so hls_downloader.download set deadline = started + 0 (or -5) and
every ffmpeg run ended as error "timeout", "exceeded max_runtime_s=0", while a
per-call max_runtime_s=0 already means "use the default" (falsy `or`). The
getter the deadline reads must give the same answer for the store value.
"""

# An ordinary module test: its subject is the module under test.
BD_GATE_SCOPE = "module"

import json
from pathlib import Path

import pytest

_DEFAULT = 3600


def _fresh_store(d):
    from bulk_downloader import global_config as GC
    Path("app_config.json").write_text(json.dumps(d), encoding="utf-8")
    GC._cached = None
    GC._cached_mtime = 0.0


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("BD_HLS_MAX_RUNTIME_S", raising=False)
    try:
        yield
    finally:
        from bulk_downloader import global_config as GC
        GC._cached = None
        GC._cached_mtime = 0.0


@pytest.mark.parametrize("stored", ["0", "-5", " 0 "])
def test_nonpositive_store_value_does_not_zero_the_deadline(scratch, stored):
    from bulk_downloader import hls_downloader as H
    _fresh_store({"hls_max_runtime_s": stored})
    got = H._max_runtime_s()
    assert got > 0, (
        f"DAH_F5_NONPOSITIVE_RUNTIME: hls_max_runtime_s={stored!r} gives a {got} s "
        f"deadline, so every HLS transfer times out at once")
    assert got == _DEFAULT


@pytest.mark.parametrize("seed", ["0", "-5"])
def test_nonpositive_env_seed_does_not_zero_the_deadline(scratch, monkeypatch, seed):
    from bulk_downloader import hls_downloader as H
    monkeypatch.setenv("BD_HLS_MAX_RUNTIME_S", seed)
    _fresh_store({})
    got = H._max_runtime_s()
    assert got == _DEFAULT, (
        f"DAH_F5_NONPOSITIVE_RUNTIME: BD_HLS_MAX_RUNTIME_S={seed!r} gives a {got} s deadline")


def test_control_positive_values_are_honoured(scratch, monkeypatch):
    from bulk_downloader import hls_downloader as H
    _fresh_store({"hls_max_runtime_s": "120"})
    assert H._max_runtime_s() == 120
    _fresh_store({"hls_max_runtime_s": "1"})
    assert H._max_runtime_s() == 1
    monkeypatch.setenv("BD_HLS_MAX_RUNTIME_S", "45")
    _fresh_store({})
    assert H._max_runtime_s() == 45
