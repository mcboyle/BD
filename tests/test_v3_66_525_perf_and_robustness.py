"""v3.66.525 -- VERIFY guard cut: VR-P13 + VR-P14.

VR-P13 (GUARD: bulk_downloader/extraction_core.py): ``segment_role`` was
latently O(n^2) on a long pure-digit path segment -- the ``_RENDITION_SIGNAL``
regex carried an UNBOUNDED ``\\d+`` in its ``(?:\\d+\\s*fps)`` branch, so on a
long digit run ``re.search`` retried every start position with linear ``\\d+``
backtracking. Pre-fix: ~9s at n=20000 (4x per 2x n). The fix bounds the digit
run (fps is 1-4 digits) -> linear. Detection of real fps/resolution descriptors
is unchanged.

VR-P14 (NON-guard: tools/build_template_from_wacz.py): ``build_template`` did
``cap.get(...)`` / ``e.get(...)`` on the raw ``json.loads`` of capture.json with
no type guard, so a TYPE-MALFORMED capture (a non-dict root, or a dom_log /
network_log that is not a list of dicts) raised AttributeError instead of
degrading to an (empty) template. Fixtures here are SYNTHETIC junk, not real
captures (no F2 data). The fix normalizes the cap-derived collections at the
function top; a well-formed capture is unaffected.

RED-first: the timing case and the malformed-capture case both FAIL on pristine
v3.66.524; the rendition-preserved + well-formed cases are regression guards
(green on both).
"""
import json
import sys
import time
import zipfile
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from bulk_downloader import extraction_core as E  # noqa: E402
import build_template_from_wacz as B  # noqa: E402


# ---------------------------------------------------------------- VR-P13
def test_segment_role_long_digit_is_not_quadratic():
    """A long pure-digit segment must classify in linear time. Pre-fix this
    backtracked quadratically (~9s at n=20000); a 1.0s ceiling cleanly
    separates linear (sub-ms) from the O(n^2) regex without machine flakiness."""
    seg = "1" * 20000
    t = time.perf_counter()
    E.segment_role(seg)
    elapsed = time.perf_counter() - t
    assert elapsed < 1.0, (
        f"segment_role on a {len(seg)}-digit segment took {elapsed:.2f}s -- "
        "O(n^2) backtracking in _RENDITION_SIGNAL (unbounded \\d+ before fps)")


def test_segment_role_rendition_signals_preserved():
    """The bound must not neuter detection: real fps / resolution descriptors
    still classify as RENDITION (these all hold on pristine too -- regression
    guard so the fix cannot just disable the branch)."""
    assert E.segment_role("clip_60fps.mp4") == E.RENDITION   # \d{1,4}\s*fps branch
    assert E.segment_role("teaser_120fps") == E.RENDITION    # 3-digit fps (the fixed branch)
    assert E.segment_role("1920x1080") == E.RENDITION         # WxH branch
    assert E.segment_role("1080p") == E.RENDITION             # \d{3,4}[pi] branch
    # and an opaque long-digit id must NOT be spuriously promoted to rendition
    assert E.segment_role("9" * 4000) == E.IDENTITY


# ---------------------------------------------------------------- VR-P14
def _wacz(cap_obj, tmp_path) -> Path:
    """Synthetic WACZ carrying a (possibly malformed) capture.json. Junk only."""
    p = tmp_path / "c.wacz"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("archive/capture.json", json.dumps(cap_obj))
    return p


@pytest.mark.parametrize("malformed", [
    [1, 2, 3],                                  # root is a list        -> L1698
    "a string",                                 # root is a str
    42,                                         # root is an int
    None,                                       # root is null
    {"dom_log": "truthy-string"},               # dom_log non-list
    {"dom_log": ["str-entry"]},                 # dom_log entry non-dict -> L1703
    {"dom_log": [999]},                         # dom_log entry int
    {"network_log": {"k": "v"}},                # network_log non-list  -> L1716
    {"action_timeline": "oops"},                # action_timeline non-list
    {"storage_snapshot": [1, 2]},               # storage_snapshot non-dict
])
def test_build_template_type_malformed_returns_dict(malformed, tmp_path):
    """A type-malformed capture must degrade to a dict template, never raise
    AttributeError. (RED on pristine: each case raises.)"""
    out = B.build_template(_wacz(malformed, tmp_path))
    assert isinstance(out, dict), f"expected dict, got {type(out).__name__} for {malformed!r}"


def test_build_template_wellformed_still_builds(tmp_path):
    """A well-formed minimal capture still produces a dict template unchanged."""
    out = B.build_template(_wacz(
        {"dom_log": [{"type": "full_snapshot", "html": "<div class='x'></div>"}],
         "network_log": []}, tmp_path))
    assert isinstance(out, dict)
