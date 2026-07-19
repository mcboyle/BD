"""Tests for the release-hygiene tools wired into build_release.py (Wave 5):
scan_version_pins, check_frontend_present, diff_release_zips.

Synthetic zips/trees only — no real artifacts. Each tool's pass AND fail
(teeth) path is exercised.

Zero-arg test functions; repo root from __file__ (run_tests.py convention).
"""
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

import scan_version_pins as SVP  # noqa: E402
import check_frontend_present as CFP  # noqa: E402
import diff_release_zips as DRZ  # noqa: E402


def _mkzip(files: dict) -> str:
    d = Path(tempfile.mkdtemp(prefix="hyg_"))
    z = d / "rel.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return str(z)


def _mktree(rel_files: dict) -> str:
    root = tempfile.mkdtemp(prefix="hygtree_")
    for rel, data in rel_files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(data, encoding="utf-8")
    return root


# ── scan_version_pins ──────────────────────────────────────────────
def test_version_pin_matches_expected():
    root = _mktree({"tests/test_x.py": 'assert __version__ == "3.66.169"\n'})
    hard, _soft = SVP.scan_test_pins(root, "3.66.169")
    assert hard == [], hard


def test_version_pin_mismatch_flagged():
    root = _mktree({"tests/test_x.py": 'assert __version__ == "3.66.168"\n'})
    hard, _soft = SVP.scan_test_pins(root, "3.66.169")
    assert len(hard) == 1 and hard[0][2] == "3.66.168", hard


# ── check_frontend_present ─────────────────────────────────────────
def _critical_files():
    return {p: "x" for p in CFP.CRITICAL_FRONTEND}


def test_frontend_required_ok():
    z = _mkzip({**_critical_files(), "bulk_downloader/__init__.py": "v"})
    assert CFP.required_present(z) == []


def test_frontend_required_detects_absent_and_empty():
    files = _critical_files()
    files["frontend/src/App.tsx"] = ""          # empty
    del files["frontend/dist/index.html"]         # absent
    z = _mkzip(files)
    bad = CFP.required_present(z)
    assert any("App.tsx" in b and "empty" in b for b in bad), bad
    assert any("index.html" in b and "absent" in b for b in bad), bad


def test_frontend_compare_detects_drop_and_change():
    base = _mkzip({"frontend/src/App.tsx": "v1", "frontend/dist/index.html": "h"})
    cand = _mkzip({"frontend/src/App.tsx": "v2"})  # index.html dropped, App changed
    res = CFP.compare(base, cand)
    assert "frontend/dist/index.html" in res["missing"], res
    assert "frontend/src/App.tsx" in res["changed"], res


# ── diff_release_zips ──────────────────────────────────────────────
def test_diff_added_changed_removed():
    old = _mkzip({"a.py": "1", "b.py": "1"})
    new = _mkzip({"a.py": "2", "c.py": "1"})  # a changed, b removed, c added
    d = DRZ.diff(old, new)
    assert d["changed"] == ["a.py"], d["changed"]
    assert d["removed"] == ["b.py"], d["removed"]
    assert d["added"] == ["c.py"], d["added"]


def test_diff_flags_forbidden_artifacts():
    new = _mkzip({"x.py": "1", "bulk_downloader/__pycache__/x.pyc": "z",
                  "data/site.wacz": "w"})
    bad = DRZ.forbidden_artifacts(list(__import__("zipfile").ZipFile(new).namelist()))
    assert any("__pycache__" in b for b in bad), bad
    assert any(b.endswith(".wacz") for b in bad), bad


def test_diff_flags_frontend_drop():
    old = _mkzip({"frontend/dist/index.html": "h", "x.py": "1"})
    new = _mkzip({"x.py": "1"})
    d = DRZ.diff(old, new)
    assert d["frontend_dropped"] == ["frontend/dist/index.html"], d


def test_diff_version_and_changelog_extraction():
    z = _mkzip({"bulk_downloader/__init__.py": '__version__ = "3.66.169"\n',
                "CHANGELOG.md": "# Changelog\n\n## v3.66.169 — x\n"})
    assert DRZ.version_of(z) == "3.66.169"
    assert DRZ.changelog_top(z) == "3.66.169"
