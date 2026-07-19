"""extraction_core — unit tests for PHASE 1, STEP 2 (the additive core).

Three guarantees, all stdlib + offline (zero-arg functions for the custom runner):

  1. EQUIVALENCE — every core function returns exactly what its current producer
     counterpart returns on the frozen golden corpus. This is the faithful-copy proof:
     it must hold *now*, before any producer is routed through the core.
  2. GOLDEN PIN — every core function also matches the frozen golden outputs directly
     (tests/fixtures/extraction_core/golden_derivation.json), so the pin still bites
     after step 5 makes the producers thin aliases of the core.
  3. LIFT FIDELITY + IMPORT-FROM-NOWHERE — recorded_rendition_ok (a closure lifted to a
     module function) matches an inline replica of the original closure across its
     branches; and no production module imports extraction_core yet (the STEP-2 invariant).

Mirrors the import shim of test_extraction_core_characterization.py.
"""
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "tools"))

import bulk_downloader.extraction_core as ec
import bulk_downloader.capture_workbench as wb
import bulk_downloader.capture_template as ct
import bulk_downloader.capture_synth as cs
import importlib.util as _u

_spec = _u.spec_from_file_location("btw_ectest", str(_ROOT / "tools/build_template_from_wacz.py"))
btw = _u.module_from_spec(_spec)
sys.modules["btw_ectest"] = btw
_spec.loader.exec_module(btw)

_FIX = json.loads((_ROOT / "tests/fixtures/extraction_core/golden_derivation.json").read_text())
_IN = _FIX["inputs"]
_GOLD = _FIX["golden"]


def _vars_sorted(p):
    """Structural view of a DraftPattern (matches the characterization fixture's freeze)."""
    return {k: v for k, v in sorted(vars(p).items())}


def _norm(obj):
    """JSON-normalize the way the network_patterns golden was frozen."""
    return json.loads(json.dumps(
        obj, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else dict(o)))


# ── 1. EQUIVALENCE: core == current producer (the faithful-copy proof) ──────────────
def test_equiv_split_segments():
    for url in _IN["urls"]:
        assert ec.split_segments(url) == ct._segments(url), url


def test_equiv_is_addressable():
    for seg in _IN["segments"]:
        assert ec.is_addressable(seg) == wb._segment_is_addressable(seg), seg


def test_equiv_segment_role():
    for seg in _IN["segments"]:
        assert ec.segment_role(seg) == wb._segment_role(seg), seg


def test_equiv_segment_regex():
    for seg in _IN["segments"]:
        assert ec.segment_regex(seg) == wb._segment_regex(seg), seg


def test_equiv_value_shape_is_classify_value():
    # re-exported by identity, and equal across the corpus
    assert ec.value_shape is cs.classify_value
    assert ec.classify_value is cs.classify_value
    for v in _IN["values"]:
        assert ec.value_shape(v) == wb.classify_value(v), v


def test_equiv_derive_pattern():
    for case in _IN["derive"]:
        param, value, in_path = case[0], case[1], bool(case[2])
        assert _vars_sorted(ec.derive_pattern(param, value, in_path)) == \
            _vars_sorted(wb._derive_pattern(param, value, in_path)), case


def test_equiv_network_patterns():
    assert _norm(ec.network_patterns(_IN["network_log"])) == \
        _norm(btw._network_patterns(_IN["network_log"]))


def test_equiv_manifest_resolutions():
    assert ec.manifest_resolutions(_IN["hls_body"], "/x/master.m3u8") == \
        btw._manifest_resolutions(_IN["hls_body"], "/x/master.m3u8")
    assert ec.manifest_resolutions(_IN["mpd_body"], "/x/manifest.mpd") == \
        btw._manifest_resolutions(_IN["mpd_body"], "/x/manifest.mpd")


def test_equiv_constants():
    assert ec.IDENTITY == wb.IDENTITY
    assert ec.RENDITION == wb.RENDITION
    # capture_template's parallel rendition role must agree (it's the value the lift uses)
    assert ec.RENDITION == ct.RENDITION_ROLE


# ── 2. GOLDEN PIN: core matches the frozen fixture directly ─────────────────────────
def test_golden_segment_role():
    for seg in _IN["segments"]:
        assert ec.segment_role(seg) == _GOLD["segment_role"][seg], seg


def test_golden_segment_regex():
    for seg in _IN["segments"]:
        assert ec.segment_regex(seg) == _GOLD["segment_regex"][seg], seg


def test_golden_is_addressable():
    for seg in _IN["segments"]:
        assert ec.is_addressable(seg) == _GOLD["segment_is_addressable"][seg], seg


def test_golden_value_shape():
    for v in _IN["values"]:
        assert ec.value_shape(v) == _GOLD["classify_value"][v], v


def test_golden_split_segments():
    for url in _IN["urls"]:
        assert list(ec.split_segments(url)) == _GOLD["segments"][url], url


def test_golden_network_patterns():
    got = _norm(ec.network_patterns(_IN["network_log"]))
    gold = json.loads(json.dumps(_GOLD["network_patterns"], default=str))
    assert got == gold, f"network_patterns drift:\n got={got}\n gold={gold}"


def test_golden_manifest_resolutions():
    assert sorted(ec.manifest_resolutions(_IN["hls_body"], "/x/master.m3u8")) == \
        _GOLD["manifest_resolutions_hls"]
    assert sorted(ec.manifest_resolutions(_IN["mpd_body"], "/x/manifest.mpd")) == \
        _GOLD["manifest_resolutions_mpd"]


def test_golden_derive_pattern():
    for key, gold in _GOLD["derive_pattern"].items():
        param, value, in_path = key.split("|")
        got = json.loads(json.dumps(
            _vars_sorted(ec.derive_pattern(param, value, in_path == "True")), default=str))
        assert got == gold, f"derive_pattern drift for {key}:\n got={got}\n gold={gold}"


def test_golden_constants():
    assert ec.IDENTITY == _GOLD["IDENTITY"]
    assert ec.RENDITION == _GOLD["RENDITION"]


# ── 3a. LIFT FIDELITY: recorded_rendition_ok == the original closure ────────────────
def _closure_reference(slot_values, slots_meta):
    """Byte-for-byte replica of capture_template._is_recorded_rendition's body,
    with the free var threaded explicitly — the thing the lift must equal."""
    for name, meta in slots_meta.items():
        if (meta.get("role") == ct.RENDITION_ROLE
                and meta.get("recorded") is not None
                and slot_values.get(name) != meta.get("recorded")):
            return False
    return True


def test_recorded_rendition_matches_closure_reference():
    cases = [
        ({}, {}),
        ({"res": "720"}, {"res": {"role": ct.RENDITION_ROLE, "recorded": "720"}}),
        ({"res": "480"}, {"res": {"role": ct.RENDITION_ROLE, "recorded": "720"}}),
        ({"res": "480"}, {"res": {"role": ct.RENDITION_ROLE, "recorded": None}}),
        ({"id": "abc"}, {"id": {"role": ct.IDENTITY_ROLE, "recorded": "xyz"}}),
        ({"res": "720", "id": "abc"},
         {"res": {"role": ct.RENDITION_ROLE, "recorded": "720"},
          "id": {"role": ct.IDENTITY_ROLE, "recorded": "abc"}}),
        ({"a": "1", "b": "9"},
         {"a": {"role": ct.RENDITION_ROLE, "recorded": "1"},
          "b": {"role": ct.RENDITION_ROLE, "recorded": "2"}}),
    ]
    for sv, meta in cases:
        assert ec.recorded_rendition_ok(sv, meta) == _closure_reference(sv, meta), (sv, meta)


def test_recorded_rendition_branches():
    R, I = ct.RENDITION_ROLE, ct.IDENTITY_ROLE
    # rendition recorded == observed -> ok
    assert ec.recorded_rendition_ok({"res": "720"}, {"res": {"role": R, "recorded": "720"}})
    # rendition recorded != observed -> not ok
    assert not ec.recorded_rendition_ok({"res": "480"}, {"res": {"role": R, "recorded": "720"}})
    # rendition with no recorded value -> ignored (ok)
    assert ec.recorded_rendition_ok({"res": "480"}, {"res": {"role": R, "recorded": None}})
    # identity-role mismatch -> ignored (only RENDITION is checked)
    assert ec.recorded_rendition_ok({"id": "abc"}, {"id": {"role": I, "recorded": "xyz"}})
    # empty meta -> ok
    assert ec.recorded_rendition_ok({}, {})
    # one of several renditions mismatches -> not ok
    assert not ec.recorded_rendition_ok(
        {"a": "1", "b": "9"},
        {"a": {"role": R, "recorded": "1"}, "b": {"role": R, "recorded": "2"}})


# Real-import detector for the routed-producers guard below: an import
# statement referencing extraction_core (relative or absolute), NOT a mere
# textual mention (a docstring naming the core does not make a module a
# producer). Anchored at line start (allowing indentation).
_IMPORTS_CORE = re.compile(
    r'^[ \t]*(?:from[ \t]+[\w.]*extraction_core[ \t]+import\b'
    r'|import[ \t]+[\w.]*extraction_core\b)', re.M)


# ── 3b. ROUTED-PRODUCERS GUARD: only the routed producers import the core ────────────
def test_extraction_core_importers_are_the_routed_producers():
    """As producers are routed through the core (steps 3-5), EXACTLY the routed ones
    import it and nothing else wires it accidentally. Update `expected` when a step
    routes another producer:
        step 3: tools/build_template_from_wacz.py  (network_patterns, manifest_resolutions)
        step 4: + bulk_downloader/capture_template.py   (pending)
        step 5: + bulk_downloader/capture_workbench.py  (pending)
    """
    expected = {
        "tools/build_template_from_wacz.py",
        "bulk_downloader/capture_template.py",
        # DECOMP-LEAF cut 4: capture_workbench.py -> capture_workbench_impl/ package;
        # all extraction_core imports consolidated into the package's _common.py.
        "bulk_downloader/capture_workbench_impl/_common.py",
    }
    importers = set()
    for base in ("bulk_downloader", "tools"):
        root = _ROOT / base
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            if "__pycache__" in dirpath:
                continue
            for fn in filenames:
                if not fn.endswith(".py"):
                    continue
                fp = Path(dirpath) / fn
                if fp.name == "extraction_core.py":
                    continue  # the module itself
                # Match a REAL import of extraction_core, not a substring: a
                # module that merely names "extraction_core" in a docstring
                # (e.g. player_recognition/player_families saying the core
                # "stays byte-identical") is not an importer. Forms covered:
                #   from .extraction_core import ...
                #   from bulk_downloader.extraction_core import ...
                #   import extraction_core / import bulk_downloader.extraction_core
                if _IMPORTS_CORE.search(fp.read_text(errors="replace")):
                    importers.add(str(fp.relative_to(_ROOT)))
    assert importers == expected, (
        "extraction_core importer set drifted from the routed-producer allowlist.\n"
        f"  got      = {sorted(importers)}\n  expected = {sorted(expected)}\n"
        "If you just routed a producer, add it to `expected`; if not, something wired "
        "the core unexpectedly.")


def test_routed_producer_detector_ignores_docstring_mentions():
    """Regression: the guard must key on a real import, not a textual mention.
    player_recognition/player_families name extraction_core in docstrings while
    explicitly NOT importing it; a substring check wrongly flagged them (the
    full-suite failure that surfaced post-168)."""
    mention = '"""... extraction_core.py stays byte-identical ..."""\nimport re\n'
    rel_import = "from .extraction_core import split_segments\n"
    abs_import = "from bulk_downloader.extraction_core import IDENTITY\n"
    plain_import = "import extraction_core\n"
    assert not _IMPORTS_CORE.search(mention), "docstring mention must NOT count"
    assert _IMPORTS_CORE.search(rel_import)
    assert _IMPORTS_CORE.search(abs_import)
    assert _IMPORTS_CORE.search(plain_import)
