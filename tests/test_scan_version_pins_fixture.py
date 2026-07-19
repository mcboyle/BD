"""Pin test for tools/scan_version_pins.py fixture-literal handling.

A `__version__ == "X"` that is itself inside a string (test fixture data) must
NOT be reported as a stale pin; a real assertion pin at the wrong version must.
Zero-arg functions; repo root via __file__; stdlib only.
"""
import importlib.util
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "scan_version_pins", REPO / "tools" / "scan_version_pins.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _root_with(test_body):
    root = Path(tempfile.mkdtemp(prefix="bd_svp_"))
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text(test_body)
    return str(root)


def test_real_stale_pin_is_flagged():
    m = _load()
    # a genuine assertion pin (real code) at the wrong version -> HARD
    body = 'from bulk_downloader import __version__\ndef test_v():\n    assert __version__ == "3.66.100"\n'
    hard, _ = m.scan_test_pins(_root_with(body), "3.66.280")
    assert hard, "real stale pin should be flagged"


def test_fixture_literal_is_ignored():
    m = _load()
    # the pin lives inside a fixture string passed to a helper -> NOT a real pin
    inner = "3.66.100"
    body = ('def test_v():\n'
            '    tree = {"tests/test_y.py": ' + repr('assert __version__ == "' + inner + '"\n') + '}\n'
            '    assert tree\n')
    hard, _ = m.scan_test_pins(_root_with(body), "3.66.280")
    assert not hard, f"fixture literal must be ignored, got {hard}"


def test_correct_real_pin_passes():
    m = _load()
    body = 'def test_v():\n    assert __version__ == "3.66.280"\n'
    hard, _ = m.scan_test_pins(_root_with(body), "3.66.280")
    assert not hard
