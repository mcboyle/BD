"""Pin test for tools/make_overlay.py — overlay derived from the release diff.

Zero-arg functions; repo root via __file__; stdlib only.
"""
import importlib.util
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "make_overlay", REPO / "tools" / "make_overlay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _zip(members):
    p = Path(tempfile.mkdtemp(prefix="bd_ovl_")) / "z.zip"
    with zipfile.ZipFile(p, "w") as zf:
        for n, b in members.items():
            zf.writestr(n, b)
    return p


def test_overlay_is_added_plus_changed_only():
    m = _load()
    base = _zip({"a.py": "1\n", "b.py": "1\n", "docs/x.md": "old\n"})
    new = _zip({"a.py": "1\n",            # unchanged -> excluded
                "b.py": "2\n",            # changed   -> included
                "docs/x.md": "new\n",     # changed   -> included
                "tools/new.py": "n\n"})   # added     -> included
    out = Path(tempfile.mkdtemp(prefix="bd_ovl_o_")) / "ov.zip"
    payload, forbidden = m.build_overlay(str(base), str(new), str(out))
    names = set(payload)
    assert "b.py" in names and "docs/x.md" in names and "tools/new.py" in names
    assert "a.py" not in names                      # unchanged excluded
    with zipfile.ZipFile(out) as zf:
        assert set(n for n in zf.namelist() if not n.endswith("/")) == names


def test_overlay_excludes_forbidden_artifacts():
    m = _load()
    base = _zip({"a.py": "1\n"})
    new = _zip({"a.py": "1\n", "x.pyc": "junk\n",
                "tools/__pycache__/y.pyc": "junk\n", "real.py": "r\n"})
    out = Path(tempfile.mkdtemp(prefix="bd_ovl_o2_")) / "ov.zip"
    payload, forbidden = m.build_overlay(str(base), str(new), str(out))
    assert "real.py" in payload
    assert not any(p.endswith(".pyc") for p in payload), payload
