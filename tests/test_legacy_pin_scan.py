"""Pin test for tools/legacy_pin_scan.py — the deletion-set test-pin scanner.

Zero-arg functions; repo root via __file__; stdlib only.
"""
import importlib.util
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "legacy_pin_scan", REPO / "tools" / "legacy_pin_scan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tree():
    root = Path(tempfile.mkdtemp(prefix="bd_pinscan_"))
    (root / "bulk_downloader" / "templates").mkdir(parents=True)
    (root / "bulk_downloader" / "static").mkdir(parents=True)
    (root / "bulk_downloader" / "templates" / "index.html").write_text("<html></html>\n")
    (root / "bulk_downloader" / "static" / "widgets.js").write_text("// widgets\n")
    (root / "bulk_downloader" / "static" / "sw.js").write_text("// sw (survivor)\n")
    (root / "bulk_downloader" / "static" / "manifest.json").write_text("{}\n")
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_reads_widget.py").write_text(
        'from pathlib import Path\n'
        'def test_x():\n'
        '    Path("bulk_downloader/static/widgets.js").read_text()\n')
    (tests / "test_reads_survivor.py").write_text(
        'def test_y():\n'
        '    c.get("/static/sw.js")\n'
        '    c.get("/static/manifest.json")\n')
    (tests / "test_comment_only.py").write_text(
        'def test_z():\n'
        '    # historical: bulk_downloader/static/widgets.js used to do X\n'
        '    pass\n')
    return root, tests


def test_widgets_js_is_a_live_pin():
    m = _load()
    root, tests = _tree()
    res = m.scan(str(root), str(tests))
    assert "test_reads_widget.py" in res["pins"], res["pins"]


def test_survivors_never_reported():
    m = _load()
    root, tests = _tree()
    res = m.scan(str(root), str(tests))
    # sw.js + manifest.json are survivors -> not in the deletion set -> no pin
    assert "test_reads_survivor.py" not in res["pins"]
    assert "test_reads_survivor.py" not in res["non_pins"]
    assert all("sw.js" not in a and "manifest.json" not in a
               for a in res["deletion_set"])


def test_comment_only_is_non_pin():
    m = _load()
    root, tests = _tree()
    res = m.scan(str(root), str(tests))
    assert "test_comment_only.py" in res["non_pins"], res["non_pins"]
    assert "test_comment_only.py" not in res["pins"]


def test_composed_pathlib_path_is_a_live_pin():
    """A reader that builds the asset path by pathlib '/' chaining
    (REPO / "bulk_downloader" / "templates" / "index.html") must still be a
    pin. The full contiguous path never appears on one line, so the bare-path
    matcher misses it — this is exactly the idiom the v3.43.* index.html
    readers use, so the cut-time pin list would be incomplete without it."""
    m = _load()
    root, tests = _tree()
    (tests / "test_composed.py").write_text(
        'from pathlib import Path\n'
        'def test_x():\n'
        '    p = Path(__file__).parent.parent / "bulk_downloader" '
        '/ "templates" / "index.html"\n'
        '    p.read_text()\n')
    res = m.scan(str(root), str(tests))
    assert "test_composed.py" in res["pins"], res["pins"]


def test_composed_os_path_join_is_a_live_pin():
    """The os.path.join idiom (..., "static", "widgets.js") is also composed —
    same miss class, same fix."""
    m = _load()
    root, tests = _tree()
    (tests / "test_join.py").write_text(
        'import os\n'
        'def test_x():\n'
        '    p = os.path.join(REPO, "bulk_downloader", "static", "widgets.js")\n'
        '    open(p).read()\n')
    res = m.scan(str(root), str(tests))
    assert "test_join.py" in res["pins"], res["pins"]


def test_composed_survivor_still_never_reported():
    """Composed-path matching must NOT start reporting survivors: a pathlib
    read of sw.js stays out of pins AND non_pins (sw.js isn't in the set)."""
    m = _load()
    root, tests = _tree()
    (tests / "test_comp_sw.py").write_text(
        'from pathlib import Path\n'
        'def test_x():\n'
        '    (REPO / "bulk_downloader" / "static" / "sw.js").read_text()\n')
    res = m.scan(str(root), str(tests))
    assert "test_comp_sw.py" not in res["pins"]
    assert "test_comp_sw.py" not in res["non_pins"]
