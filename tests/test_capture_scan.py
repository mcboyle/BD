"""Item 3 -- capture SCAN + browser (the "hundreds of captures" ask).

dom_analyzer.list_captures scanned only the 5 fixed dirs NON-recursively
(dd.glob), so onboarding captures nested under
``captures/template_onboarding/<host>_<siteid>_<ts>/`` were INVISIBLE. This adds
a recursive, zero-zip-open scanner (scan_captures) with cheap per-capture metadata
(host parsed from the naming, captured_at, size, kind, redacted twin) and a
safe relative-subpath token resolver (resolve_capture_token) constrained under
the project root (rejects absolute / .. / symlink escape).

Seeded fake tree -- the sandbox has no real captures.
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

import bulk_downloader.dom_analyzer as da


def _seed_tree() -> Path:
    """Build a fake capture tree under a temp root:

      captures/flat_top.wacz                                     (flat, no host)
      captures/template_onboarding/app.reptyle.com_0b60f1ec_20250621_aa/x.wacz
      captures/template_onboarding/auth.site.tv_dead_20250101_bb/x.wacz   (collide x.wacz)
      captures/template_onboarding/app.reptyle.com_0b60f1ec_20250621_aa/x.redacted.wacz
      offline_out/capture_legacy.json                            (json kind)
    """
    root = Path(tempfile.mkdtemp())
    cap = root / "captures"
    ob = cap / "template_onboarding"
    d1 = ob / "app.reptyle.com_0b60f1ec_20250621_aa"
    d2 = ob / "auth.site.tv_dead_20250101_bb"
    off = root / "offline_out"
    for d in (cap, d1, d2, off):
        d.mkdir(parents=True, exist_ok=True)
    # a real (tiny) wacz so any accidental open would at least be a valid zip
    def _wacz(p: Path):
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("archive/capture.json", "{}")
    _wacz(cap / "flat_top.wacz")
    _wacz(d1 / "x.wacz")
    _wacz(d1 / "x.redacted.wacz")
    _wacz(d2 / "x.wacz")
    (off / "capture_legacy.json").write_text("{}", encoding="utf-8")
    return root


# (a) a nested onboarding capture IS discovered (the RED -- flat glob misses it)
def test_scan_discovers_nested_onboarding_capture():
    root = _seed_tree()
    rows = da.scan_captures(root=root)
    rels = {r["rel_path"] for r in rows}
    assert "captures/template_onboarding/app.reptyle.com_0b60f1ec_20250621_aa/x.wacz" in rels
    # the flat top-level capture is still found
    assert "captures/flat_top.wacz" in rels
    # the legacy json capture is found
    assert any(r["kind"] == "json" for r in rows)


# (b) host parsed from the <host>_<siteid>_<ts> naming
def test_scan_parses_host_from_onboarding_naming():
    root = _seed_tree()
    rows = da.scan_captures(root=root)
    by_rel = {r["rel_path"]: r for r in rows}
    r = by_rel["captures/template_onboarding/app.reptyle.com_0b60f1ec_20250621_aa/x.wacz"]
    assert r["host"] == "app.reptyle.com"
    # the flat capture with no host naming -> None
    assert by_rel["captures/flat_top.wacz"]["host"] is None


# (c) a .redacted.wacz twin marks the raw capture redacted=True
def test_scan_marks_redacted_twin():
    root = _seed_tree()
    rows = da.scan_captures(root=root)
    by_rel = {r["rel_path"]: r for r in rows}
    raw = by_rel["captures/template_onboarding/app.reptyle.com_0b60f1ec_20250621_aa/x.wacz"]
    assert raw["redacted"] is True
    # and the flat one has no twin
    assert by_rel["captures/flat_top.wacz"]["redacted"] is False


# (d) the scan/list path opens ZERO wacz zips (perf structural: host from path)
def test_scan_opens_no_zip(monkeypatch):
    root = _seed_tree()
    opened = []
    real = zipfile.ZipFile

    def _spy(*a, **k):
        opened.append(a[0] if a else None)
        return real(*a, **k)

    monkeypatch.setattr(zipfile, "ZipFile", _spy)
    da.scan_captures(root=root)
    assert opened == [], f"scan opened zip(s): {opened}"


# (e) resolve_capture_token: a valid relative subpath resolves to the real file
def test_resolve_token_valid_subpath():
    root = _seed_tree()
    tok = "captures/template_onboarding/auth.site.tv_dead_20250101_bb/x.wacz"
    p = da.resolve_capture_token(tok, root=root)
    assert p is not None and p.is_file()
    assert p == root / tok


# (f) basename collision across two subdirs resolves to the CORRECT file by token
def test_resolve_token_disambiguates_collision():
    root = _seed_tree()
    t1 = "captures/template_onboarding/app.reptyle.com_0b60f1ec_20250621_aa/x.wacz"
    t2 = "captures/template_onboarding/auth.site.tv_dead_20250101_bb/x.wacz"
    p1 = da.resolve_capture_token(t1, root=root)
    p2 = da.resolve_capture_token(t2, root=root)
    assert p1 != p2
    assert p1 == root / t1 and p2 == root / t2


# (g) resolve rejects absolute / .. / non-capture paths
def test_resolve_token_rejects_traversal():
    root = _seed_tree()
    assert da.resolve_capture_token("/etc/passwd", root=root) is None
    assert da.resolve_capture_token("../../etc/passwd", root=root) is None
    assert da.resolve_capture_token("captures/../../../etc/passwd", root=root) is None
    # a path that is not an enumerated capture is rejected
    assert da.resolve_capture_token("captures/nope.wacz", root=root) is None
    assert da.resolve_capture_token("", root=root) is None


# (h) resolve rejects a symlink that escapes the root (no escape via symlink)
def test_resolve_token_rejects_symlink_escape():
    root = _seed_tree()
    secret = Path(tempfile.mkdtemp()) / "outside.wacz"
    with zipfile.ZipFile(secret, "w") as z:
        z.writestr("archive/capture.json", "{}")
    link = root / "captures" / "linked.wacz"
    try:
        os.symlink(secret, link)
    except (OSError, NotImplementedError):
        return  # platform without symlinks -> nothing to assert
    # the symlinked capture must NOT be discoverable nor resolvable
    rows = da.scan_captures(root=root)
    assert "captures/linked.wacz" not in {r["rel_path"] for r in rows}
    assert da.resolve_capture_token("captures/linked.wacz", root=root) is None


# (i) summary builder: totals + by_host
def test_scan_summary_counts():
    root = _seed_tree()
    summ = da.scan_captures_summary(root=root)
    assert summ["total"] >= 4
    assert summ["by_host"].get("app.reptyle.com", 0) >= 1
    assert isinstance(summ["took_ms"], (int, float))
