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


# ── the current version appears ONCE as a changelog header ────────
#
# @1009. v3.66.1008 shipped its entry TWICE. The prepend script verified ASCII
# with a read-back AFTER writing, so a failed check left the file mutated, and
# the corrected re-run prepended a second copy to an already-prepended file.
#
# Nothing caught it. Both CHANGELOG checks in .github/workflows/ci.yml resolve
# `hdr[0]` -- the FIRST '## ' header -- and assert over that entry alone, so a
# duplicate anywhere below is structurally outside their denominator. They
# reported ASCII-clean and version-coherent, truthfully, about one of the two.
#
# SCOPED TO THE CURRENT VERSION, NOT TO EVERY HEADER, and that is measured
# rather than cautious: the changelog already carries `## v3.49.0 - 2026-05-15`
# twice, from long before this cut (2 of 1001 headers at @1009). A blanket
# uniqueness gate would fail on history nobody intends to rewrite, get switched
# off, and take the useful half with it -- CLAUDE.md section 0's
# over-sensitivity failure, which is a soundness bug and not a safe default.

def _repo_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parent.parent


def _changelog_headers(text):
    return [l for l in text.splitlines() if l.startswith("## ")]


def test_the_header_scan_can_see_the_changelog():
    """Non-empty denominator, asserted before the verdict below. A parse that
    found no headers would report "the version appears once" just as
    truthfully, over nothing."""
    text = (_repo_root() / "CHANGELOG.md").read_text(encoding="utf-8")
    assert len(_changelog_headers(text)) > 100, "the header scan went blind"


def test_the_current_version_has_exactly_one_changelog_entry():
    import re
    root = _repo_root()
    v = re.search(r'__version__\s*=\s*"([^"]+)"',
                  (root / "bulk_downloader" / "__init__.py").read_text(
                      encoding="utf-8")).group(1)
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    hits = [h for h in _changelog_headers(text) if v in h]
    assert len(hits) == 1, (
        "v%s has %d changelog entries, expected exactly 1: %r\n"
        "A prepend that ran twice is the way this happens; ci.yml's two "
        "CHANGELOG checks read only the first header and cannot see it."
        % (v, len(hits), hits))


def test_the_gate_FIRES_on_a_duplicate_and_not_on_a_single_entry():
    """Both directions. A gate that only ever passes is not a gate, and one
    that fires on the correct shape would be switched off."""
    def hits(text, v):
        return [h for h in _changelog_headers(text) if v in h]
    one = "# Changelog\n\n## v9.9.9\n\nx\n\n## v9.9.8\n\ny\n"
    two = "# Changelog\n\n## v9.9.9\n\nx\n\n## v9.9.9\n\nx\n\n## v9.9.8\n\ny\n"
    assert len(hits(one, "9.9.9")) == 1
    assert len(hits(two, "9.9.9")) == 2
