"""v3.66.320 — synthetic_tests json_path assertion support + sandbox canary smoke.

RED-first contract (proven failing on pristine synthetic_tests.py before impl):

  1. ``_json_path(obj, path)`` evaluator exists: dotted keys, ``[N]`` index,
     ``[*]`` wildcard -> list; an unresolved path raises (drift signal).
  2. ``run_fixture`` evaluates an ``assertions.json`` ``json_path`` map against
     the parsed JSON body, exposes ``extracted``, and feeds it to ``expects`` so
     the JSON fixtures (api/spa) assert into ``items[].media_url/title/id``
     instead of passing vacuously.
  3. ``run_all`` accepts an optional ``root=`` kwarg (default INSTALL_DIR/fixtures)
     so a sandbox test can target a fixtures dir with no install.
  4. Sandbox canary smoke: ``run_all`` over ``fixtures/fixturesite2_*`` passes
     (4/4) AND a deliberately-unresolvable json_path is flagged ``ok=False``.

This is the sandbox half of OPV-F3.3 — pure offline replay, no network. The
test runner chdirs to a temp dir, so the repo root is derived from __file__.
"""
import json
import tempfile
from pathlib import Path

import pytest

# repo root = <root>/tests/this_file.py -> parents[1]
_ROOT = Path(__file__).resolve().parent.parent

import sys
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bulk_downloader import synthetic_tests as st  # noqa: E402
from capture_test_fixtures import capture_fixture_lane  # noqa: E402

# s_cfg the HTML fixtures need (title comes from the run cfg, not the .har)
_S_CFG = {
    "fixturesite2_scene": {"title_selector": "title"},
    "fixturesite2_infinite": {"title_selector": "title"},
}
_CAPTURE_FIXTURES = capture_fixture_lane()


def _fixture_root():
    if not _CAPTURE_FIXTURES.enabled:
        pytest.skip("synthetic HAR capture artifacts not enabled")
    root = _CAPTURE_FIXTURES.root / "fixtures"
    required = [
        root / site / filename
        for site in _S_CFG | {"fixturesite2_api": {}, "fixturesite2_spa": {}}
        for filename in ("canary.har", "canary.assertions.json")
    ]
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        pytest.skip(
            "synthetic HAR capture artifacts not present under "
            f"{_CAPTURE_FIXTURES.env_name}: {', '.join(missing)}"
        )
    return root


def _have_lxml():
    try:
        import lxml  # noqa: F401
        import cssselect  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- evaluator ---
def test_json_path_evaluator_dotted_index_wildcard():
    obj = {"items": [{"id": 0, "title": "A", "media_url": "/a.mp4"},
                     {"id": 1, "title": "B", "media_url": "/b.mp4"}]}
    assert st._json_path(obj, "items[0].title") == "A"
    assert st._json_path(obj, "items[1].id") == 1
    assert st._json_path(obj, "items[*].media_url") == ["/a.mp4", "/b.mp4"]
    assert st._json_path(obj, "items[*].id") == [0, 1]


def test_json_path_evaluator_unresolved_raises():
    obj = {"items": [{"id": 0}]}
    # a key that doesn't exist must RAISE (that's the drift signal the
    # caller turns into ok=False), not silently return None.
    raised = False
    try:
        st._json_path(obj, "items[0].nope")
    except Exception:
        raised = True
    assert raised, "unresolved json_path must raise"
    raised = False
    try:
        st._json_path(obj, "items[9].id")  # index past end
    except Exception:
        raised = True
    assert raised, "out-of-range index must raise"


# --------------------------------------------------- run_fixture json_path ---
def test_run_fixture_json_path_populates_extracted_and_passes():
    if not _have_lxml():
        return  # selector dep is for HTML; JSON path is dep-free, but keep parity
    root = _fixture_root()
    har = root / "fixturesite2_api" / "canary.har"
    ass = root / "fixturesite2_api" / "canary.assertions.json"
    r = st.run_fixture(har, ass, site_cfg=None)
    ex = r.get("extracted") or {}
    # json_path resolved the three keys off the JSON body
    assert ex.get("first_title") == "Mock Item 000", ex
    assert isinstance(ex.get("media"), list) and len(ex["media"]) == 10, ex
    assert isinstance(ex.get("ids"), list) and len(ex["ids"]) == 10, ex
    # and the expects (regex / _min / _count) all hold -> ok
    assert r.get("ok") is True, r


def test_run_fixture_json_path_drift_flags_not_ok():
    # an assertions file whose json_path no longer resolves must yield ok=False
    root = _fixture_root()
    har = root / "fixturesite2_api" / "canary.har"
    broken = {
        "json_path": {"gone": "items[*].does_not_exist"},
        "expects": {"gone_count": 10},
    }
    with tempfile.TemporaryDirectory() as td:
        ap = Path(td) / "broken.assertions.json"
        ap.write_text(json.dumps(broken), "utf-8")
        r = st.run_fixture(har, ap, site_cfg=None)
    assert r.get("ok") is False, r


# -------------------------------------------------------- run_all root kwarg ---
def test_run_all_accepts_root_kwarg_and_passes_all():
    if not _have_lxml():
        return
    res = st.run_all(root=_fixture_root(), s_cfg=_S_CFG)
    assert res["total"] == 4, res
    assert res["failed"] == 0, res
    assert res["passed"] == res["total"], res


def test_run_all_root_drift_detected():
    # break one fixture's CSS selector via s_cfg -> that fixture fails,
    # proving run_all(root=) actually surfaces drift end-to-end.
    if not _have_lxml():
        return
    bad_cfg = dict(_S_CFG)
    bad_cfg["fixturesite2_scene"] = {"title_selector": ".gone-xyz-not-in-dom"}
    res = st.run_all(root=_fixture_root(), s_cfg=bad_cfg)
    assert res["failed"] >= 1, res
