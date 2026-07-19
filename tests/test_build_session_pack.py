"""Pin test for tools/build_session_pack.py — the one-command close ritual.

Zero-arg functions; repo root via __file__. Builds a tiny fake release zip +
draft STATE + pack dir, asserts mechanical refresh + changes-pruning. Uses
openpyxl for the tracker (skips if absent).
"""
import hashlib
import importlib.util
import json
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_session_pack", REPO / "tools" / "build_session_pack.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_zip(path, version, guard_body):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bulk_downloader/__init__.py", f'__version__ = "{version}"\n')
        zf.writestr("tools/build_release.py", guard_body)
        zf.writestr("a.txt", "a\n")


def test_refresh_sets_sha_count_version_and_guard():
    m = _load()
    d = Path(tempfile.mkdtemp(prefix="bd_bsp_"))
    zp = d / "rel.zip"
    _fake_zip(zp, "3.66.281", "# guard body v281\n")
    want_guard = hashlib.sha256(b"# guard body v281\n").hexdigest()
    state = {
        "built_version": "0.0.0",
        "zip": {"name": "OLD.zip", "file": "OLD.zip", "sha256": "x", "file_count": 1},
        "guards": {"tools/build_release.py": "deadbeef"},
        "guards_full_sha256": {"tools/build_release.py": "dead" * 16},
    }
    out, ver, cnt, full = m.refresh_state(state, str(zp), 2)
    assert ver == "3.66.281" and out["built_version"] == "3.66.281"
    assert out["zip"]["file_count"] == cnt == 3
    assert out["zip"]["sha256"] == full == hashlib.sha256(zp.read_bytes()).hexdigest()
    assert out["guards"]["tools/build_release.py"] == want_guard[:8]
    assert out["guards_full_sha256"]["tools/build_release.py"] == want_guard


def test_prunes_changes_to_keep_newest():
    m = _load()
    d = Path(tempfile.mkdtemp(prefix="bd_bsp2_"))
    zp = d / "rel.zip"
    _fake_zip(zp, "3.66.281", "# g\n")
    state = {
        "built_version": "0.0.0", "zip": {},
        "changes_278": "a", "changes_279": "b", "changes_280": "c", "changes_281": "d",
    }
    out, *_ = m.refresh_state(state, str(zp), 2)
    keys = sorted(k for k in out if k.startswith("changes_"))
    assert keys == ["changes_280", "changes_281"], keys
