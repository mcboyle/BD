"""extraction_core — characterization (golden) tests. PHASE 1, STEP 1.

Pins the CURRENT byte-level behavior of the derivation logic that a future
`extraction_core` will consolidate (segment roles/regex/addressability,
value-shape classification, URL segmentation, network-pattern derivation,
manifest-resolution parsing, identifier-pattern drafting). The golden outputs
are frozen in tests/fixtures/extraction_core/golden_derivation.json.

Today this passes trivially (live functions == fixture). Its VALUE is later:
when extraction_core lands and `build_template_from_wacz` / `capture_workbench`
/ `capture_template` are routed through it, this test must still pass against
the FROZEN fixture — that is the zero-behavior-change contract. If any output
shifts, the refactor changed derivation and the diff is caught here.

This is characterization, not specification: it freezes behavior as-is (including
quirks like a bare "480" segment classifying as identity), it does not assert the
behavior is correct. Zero-arg functions for the custom runner; stdlib + offline.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import bulk_downloader.capture_workbench as wb
import bulk_downloader.capture_template as ct
import importlib.util as _u

_spec = _u.spec_from_file_location("btw_char", str(_ROOT / "tools/build_template_from_wacz.py"))
btw = _u.module_from_spec(_spec)
sys.modules["btw_char"] = btw
_spec.loader.exec_module(btw)

_FIX = json.loads((_ROOT / "tests/fixtures/extraction_core/golden_derivation.json").read_text())
_IN = _FIX["inputs"]
_GOLD = _FIX["golden"]


def _derive_fields(param, value, in_path):
    p = wb._derive_pattern(param, value, in_path)
    if hasattr(p, "__dict__"):
        return {k: v for k, v in sorted(vars(p).items())}
    if isinstance(p, (tuple, list)):
        return list(p)
    return str(p)


def test_segment_role_pinned():
    for seg in _IN["segments"]:
        assert wb._segment_role(seg) == _GOLD["segment_role"][seg], seg


def test_segment_regex_pinned():
    for seg in _IN["segments"]:
        assert wb._segment_regex(seg) == _GOLD["segment_regex"][seg], seg


def test_segment_is_addressable_pinned():
    for seg in _IN["segments"]:
        assert wb._segment_is_addressable(seg) == _GOLD["segment_is_addressable"][seg], seg


def test_classify_value_pinned():
    for v in _IN["values"]:
        assert wb.classify_value(v) == _GOLD["classify_value"][v], v


def test_segments_split_pinned():
    for url in _IN["urls"]:
        assert list(ct._segments(url)) == _GOLD["segments"][url], url


def test_network_patterns_pinned():
    got = btw._network_patterns(_IN["network_log"])
    # normalize to JSON-comparable (Counters/sets → sorted) the same way the fixture was frozen
    norm = json.loads(json.dumps(got, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else dict(o)))
    gold = json.loads(json.dumps(_GOLD["network_patterns"], default=str))
    assert norm == gold, f"network_patterns drift:\n got={norm}\n gold={gold}"


def test_manifest_resolutions_pinned():
    assert sorted(btw._manifest_resolutions(_IN["hls_body"], "/x/master.m3u8")) == _GOLD["manifest_resolutions_hls"]
    assert sorted(btw._manifest_resolutions(_IN["mpd_body"], "/x/manifest.mpd")) == _GOLD["manifest_resolutions_mpd"]


def test_derive_pattern_pinned():
    for key, gold in _GOLD["derive_pattern"].items():
        param, value, in_path = key.split("|")
        got = _derive_fields(param, value, in_path == "True")
        got = json.loads(json.dumps(got, default=str))
        assert got == gold, f"derive_pattern drift for {key}:\n got={got}\n gold={gold}"


def test_identity_rendition_constants_pinned():
    assert wb.IDENTITY == _GOLD["IDENTITY"]
    assert wb.RENDITION == _GOLD["RENDITION"]
