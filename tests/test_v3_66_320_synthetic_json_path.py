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
_FIXTURE_SITES = tuple(_S_CFG) + ("fixturesite2_api", "fixturesite2_spa")
_CAPTURE_FIXTURES = capture_fixture_lane()


def _fixture_sources(capture_root, site):
    site_root = capture_root / site
    return (
        site_root / "canary.har",
        _ROOT / "fixtures" / site / "canary.assertions.json",
    )


def _merge_fixture_tree(capture_root, merged_root):
    for site in _FIXTURE_SITES:
        site_root = merged_root / site
        site_root.mkdir(parents=True)
        har, assertions = _fixture_sources(capture_root, site)
        (site_root / "canary.har").symlink_to(har)
        (site_root / "canary.assertions.json").symlink_to(assertions)
    return merged_root


def _capture_root(lane=None):
    lane = _CAPTURE_FIXTURES if lane is None else lane
    if not lane.enabled:
        pytest.skip("synthetic HAR capture artifacts not enabled")
    root = lane.root / "fixtures"
    required_hars = [
        root / site / "canary.har"
        for site in _FIXTURE_SITES
    ]
    missing = [
        str(path.relative_to(root)) for path in required_hars if not path.is_file()
    ]
    if missing:
        pytest.skip(
            "synthetic HAR capture artifacts not present under "
            f"{lane.env_name}: {', '.join(missing)}"
        )
    required_assertions = [
        _fixture_sources(root, site)[1]
        for site in _FIXTURE_SITES
    ]
    missing_tracked = [
        str(path.relative_to(_ROOT))
        for path in required_assertions
        if not path.is_file()
    ]
    if missing_tracked:
        raise AssertionError(
            "tracked synthetic assertions missing: " + ", ".join(missing_tracked)
        )
    return root


@pytest.fixture
def fixture_root(tmp_path):
    return _merge_fixture_tree(_capture_root(), tmp_path / "synthetic-fixtures")


def _have_lxml():
    try:
        import lxml  # noqa: F401
        import cssselect  # noqa: F401
        return True
    except Exception:
        return False


def test_capture_fixture_sources_use_external_har_and_tracked_assertions(tmp_path):
    capture_root = tmp_path / "external" / "fixtures"

    har, assertions = _fixture_sources(capture_root, "fixturesite2_api")

    assert har == capture_root / "fixturesite2_api" / "canary.har"
    assert assertions == (
        _ROOT / "fixtures" / "fixturesite2_api" / "canary.assertions.json"
    )
    assert assertions.is_file()


def test_capture_root_requires_only_external_hars(tmp_path):
    lane_root = tmp_path / "capture"
    capture_root = lane_root / "fixtures"
    for site in _FIXTURE_SITES:
        har = capture_root / site / "canary.har"
        har.parent.mkdir(parents=True)
        har.write_text("{}", encoding="utf-8")
    lane = type(_CAPTURE_FIXTURES)(
        env_name="BD_TEST_CAPTURE_ROOT",
        root=lane_root,
    )

    assert _capture_root(lane) == capture_root


def test_merged_fixture_tree_symlinks_hars_and_tracked_assertions(tmp_path):
    capture_root = tmp_path / "external" / "fixtures"
    external_har = capture_root / "fixturesite2_api" / "canary.har"
    external_har.parent.mkdir(parents=True)
    external_har.write_bytes(b"private capture stand-in")

    merged = _merge_fixture_tree(capture_root, tmp_path / "merged")

    merged_har = merged / "fixturesite2_api" / "canary.har"
    merged_assertions = (
        merged / "fixturesite2_api" / "canary.assertions.json"
    )
    assert merged_har.is_symlink()
    assert merged_har.resolve() == external_har
    assert merged_assertions.is_symlink()
    assert merged_assertions.resolve() == (
        _ROOT / "fixtures" / "fixturesite2_api" / "canary.assertions.json"
    )


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
    har, ass = _fixture_sources(_capture_root(), "fixturesite2_api")
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
    har, _ = _fixture_sources(_capture_root(), "fixturesite2_api")
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
def test_run_all_accepts_root_kwarg_and_passes_all(fixture_root):
    if not _have_lxml():
        return
    res = st.run_all(root=fixture_root, s_cfg=_S_CFG)
    assert res["total"] == 4, res
    assert res["failed"] == 0, res
    assert res["passed"] == res["total"], res


def test_run_all_root_drift_detected(fixture_root):
    # break one fixture's CSS selector via s_cfg -> that fixture fails,
    # proving run_all(root=) actually surfaces drift end-to-end.
    if not _have_lxml():
        return
    bad_cfg = dict(_S_CFG)
    bad_cfg["fixturesite2_scene"] = {"title_selector": ".gone-xyz-not-in-dom"}
    res = st.run_all(root=fixture_root, s_cfg=bad_cfg)
    assert res["failed"] >= 1, res
