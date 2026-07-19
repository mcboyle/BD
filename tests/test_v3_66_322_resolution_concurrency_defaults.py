"""A0 / BEH-1 + BEH-2: canonical creation-time defaults for min_resolution and
max_concurrent.

Pristine bug (v3.66.321): runner.py reads `min_resolution` with two different
fallbacks -- `get("min_resolution", 0)` on the yt-dlp quality path and
`get("min_resolution", 1080)` on the runtime min-resolution gate. A site with no
explicit `min_resolution` therefore gets low-res needs-review flagging ON or OFF
depending on which path runs (the 0 path silently disables it). BEH-2 is the same
shape for `max_concurrent` (one read defaults 1, others 2).

Fix: a single module constant per knob (1080 / 2, the legacy-stored defaults) that
all read sites reference. RED on pristine (constants absent + the `, 0` / `, 1`
literals present); GREEN after unification.
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_RUNNER = _REPO / "bulk_downloader" / "runner.py"


def _src():
    return _RUNNER.read_text(encoding="utf-8")


def test_default_constants_exist_and_values():
    from bulk_downloader.runner import (
        DEFAULT_MIN_RESOLUTION,
        DEFAULT_MAX_CONCURRENT,
    )
    assert DEFAULT_MIN_RESOLUTION == 1080, DEFAULT_MIN_RESOLUTION
    assert DEFAULT_MAX_CONCURRENT == 2, DEFAULT_MAX_CONCURRENT


def test_min_resolution_default_unified_no_zero_fallback():
    src = _src()
    # the divergent "default 0" read must be gone (it silently disables flagging)
    assert 'get("min_resolution", 0)' not in src, \
        "min_resolution still defaults to 0 on one read path"
    assert 'get("min_resolution",0)' not in src
    # both read sites now reference the canonical constant
    assert src.count("DEFAULT_MIN_RESOLUTION") >= 2, \
        "both min_resolution reads must use DEFAULT_MIN_RESOLUTION"


def test_max_concurrent_default_unified_no_one_fallback():
    src = _src()
    assert 'get("max_concurrent", 1)' not in src, \
        "max_concurrent still defaults to 1 on one read path"
    assert 'get("max_concurrent",1)' not in src
    assert "DEFAULT_MAX_CONCURRENT" in src


def test_min_resolution_resolution_semantics():
    # Behavioral contract of the canonical expression int(cfg.get(k, DEFAULT) or 0):
    # absent -> 1080 (flag sub-1080); explicit values honored; explicit 0 stays
    # "no minimum" (operator opt-out preserved).
    from bulk_downloader.runner import DEFAULT_MIN_RESOLUTION as D

    def resolve(cfg):
        return int(cfg.get("min_resolution", D) or 0)

    assert resolve({}) == 1080
    assert resolve({"min_resolution": 720}) == 720
    assert resolve({"min_resolution": 0}) == 0
    assert resolve({"min_resolution": None}) == 0


def test_max_concurrent_resolution_semantics():
    from bulk_downloader.runner import DEFAULT_MAX_CONCURRENT as D

    def resolve(cfg):
        return max(1, int(cfg.get("max_concurrent", D)))

    assert resolve({}) == 2
    assert resolve({"max_concurrent": 4}) == 4
    assert resolve({"max_concurrent": 1}) == 1
