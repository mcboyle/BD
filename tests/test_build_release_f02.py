"""Pin tests for F0.2 one-pass build_release STATE stamping.

Zero-arg test functions per run_tests.py conventions; repo root via __file__.
build_release.py is stdlib-only at import, so it loads via spec without Flask.
These prove the FIRST build produces an in-zip STATE.json whose file_count ==
the zip's own member count and whose built_version == the package version —
i.e. verify_release's count + built_version gates are active without a
two-pass STATE edit.
"""
import importlib.util
import json
import os
import stat
import tempfile
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "build_release", REPO / "tools" / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _stale_state():
    # Deliberately wrong file_count + built_version + zip name; the build must
    # overwrite all three. live_version must survive untouched.
    return json.dumps({
        "live_version": "3.66.999",
        "built_version": "0.0.0",
        "deploy_status": "BUILT",
        "zip": {"name": "WRONG.zip", "file": "WRONG.zip",
                "sha256": "x", "file_count": 1},
        "guards": {},
        "parity": {"legacy_only_count": 1},
    }, indent=2)


def _make_tree(d):
    root = Path(tempfile.mkdtemp(prefix="bd_f02_"))
    (root / "bulk_downloader").mkdir()
    (root / "bulk_downloader" / "__init__.py").write_text('__version__ = "9.9.9"\n')
    (root / "a.txt").write_text("a\n")
    (root / "b.txt").write_text("b\n")
    (root / "STATE.json").write_text(_stale_state())
    return root


def _files(mod, root):
    # Mirror build_release's own enumeration: every file, sorted, relative.
    return sorted([p for p in root.rglob("*") if p.is_file()],
                  key=lambda q: str(q.relative_to(root)).replace("\\", "/"))


# ── Fix B (BP-SB): F0.2 must null the self-referential zip.sha256 ──

def test_stamp_nulls_self_referential_sha():
    # A full-zip sha can't be computed over a zip that contains this STATE,
    # so the stamp must NULL it (not leave the stale work-tree value) — the
    # in-zip copy then makes no false claim and bd-state cleanly SKIPS its
    # sha gate on the work-tree fallback.
    m = _load()
    out = m._stamp_state_json(_stale_state().encode(), version="3.66.215",
                              file_count=1313, zip_name="BulkDownloader_v3_66_215.zip")
    d = json.loads(out)
    assert d["zip"]["sha256"] is None, d["zip"].get("sha256")


def test_stamp_refreshes_declared_guards_from_root():
    # A guard-changing cut must not embed a stale guard pin: the stamp refreshes
    # whatever guard keys STATE declares from the ACTUAL built tree, so the in-zip
    # guards match the zip and verify_release's guard-vs-zip check passes.
    import hashlib
    root = Path(tempfile.mkdtemp(prefix="bd_f02g_"))
    (root / "bulk_downloader").mkdir()
    gp = root / "bulk_downloader" / "extraction_core.py"
    gp.write_text("# guard body\n")
    want = hashlib.sha256(gp.read_bytes()).hexdigest()
    state = json.dumps({
        "built_version": "0.0.0",
        "zip": {"name": "W.zip", "file": "W.zip", "sha256": "x", "file_count": 1},
        "guards": {"bulk_downloader/extraction_core.py": "deadbeef"},
        "guards_full_sha256": {"bulk_downloader/extraction_core.py": "dead" * 16},
    })
    m = _load()
    out = m._stamp_state_json(state.encode(), version="3.66.215", file_count=2,
                              zip_name="z.zip", root=root)
    d = json.loads(out)
    assert d["guards"]["bulk_downloader/extraction_core.py"] == want[:8]
    assert d["guards_full_sha256"]["bulk_downloader/extraction_core.py"] == want


# ── _stamp_state_json unit ────────────────────────────────────────

def test_stamp_sets_count_version_name_keeps_live():
    m = _load()
    out = m._stamp_state_json(_stale_state().encode(), version="3.66.215",
                              file_count=1313, zip_name="BulkDownloader_v3_66_215.zip")
    d = json.loads(out)
    assert d["built_version"] == "3.66.215"
    assert d["zip"]["file_count"] == 1313
    assert d["zip"]["name"] == "BulkDownloader_v3_66_215.zip"
    assert d["zip"]["file"] == "BulkDownloader_v3_66_215.zip"
    assert d["live_version"] == "3.66.999"        # untouched


def test_stamp_returns_raw_on_malformed():
    m = _load()
    bad = b"{not json"
    assert m._stamp_state_json(bad, version="1", file_count=1, zip_name="z") == bad


# ── full _build_zip integration ───────────────────────────────────

def test_first_build_state_count_matches_members():
    m = _load()
    root = _make_tree(None)
    files = _files(m, root)
    dest = root / "out" / "BulkDownloader_v3_66_215.zip"
    m._build_zip(root, files, dest, "3.66.215")

    with zipfile.ZipFile(dest) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        state = json.loads(zf.read("STATE.json"))

    # the load-bearing assertion: in-zip count == real member count
    assert state["zip"]["file_count"] == len(names)
    assert state["built_version"] == "3.66.215"
    assert state["zip"]["name"] == "BulkDownloader_v3_66_215.zip"
    assert state["live_version"] == "3.66.999"     # still untouched


def test_build_is_deterministic_with_stamp():
    m = _load()
    root = _make_tree(None)
    files = _files(m, root)
    d1 = root / "o1.zip"; d2 = root / "o2.zip"
    # same dest name so the stamped zip.name is identical → byte-identical
    m._build_zip(root, files, root / "out1" / "rel.zip", "3.66.215")
    m._build_zip(root, files, root / "out2" / "rel.zip", "3.66.215")
    b1 = (root / "out1" / "rel.zip").read_bytes()
    b2 = (root / "out2" / "rel.zip").read_bytes()
    assert b1 == b2, "F0.2 stamp broke deterministic build"


def test_build_marks_shell_scripts_as_unix_executables():
    """Linux unzip must recognize the executable mode stored in the ZIP."""
    m = _load()
    root = _make_tree(None)
    script = root / "install_service.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    files = _files(m, root)
    dest = root / "out" / "release.zip"

    m._build_zip(root, files, dest, "3.66.215")

    with zipfile.ZipFile(dest) as zf:
        info = zf.getinfo("install_service.sh")

    assert info.create_system == 3, "ZIP member is not marked as Unix"
    assert stat.S_IMODE(info.external_attr >> 16) == 0o755
