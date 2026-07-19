"""B1.3 (post-365) — build identity in /api/health  [ISOLATED GUARD CUT].

build_release.py (a release guard) writes build_info.json {sha, built_at} into
the release and injects the same VITE_BUILD_STAMP for the frontend; /api/health
exposes build:{sha,built_at}. That lets the Dashboard compare the FE-loaded
stamp against the backend sha (meaningful) instead of package.json 0.1.0 vs
backend 3.66.x (permanently red).

Resolution: /api/health reads build_info.json from BD_INSTALL_DIR (same anchor
as the DB path), and omits the `build` key gracefully when the file is absent.

This is the isolated guard cut: tools/build_release.py changes SHA (declared
before/after). Everything else stays byte-identical.

RED-first: on pristine source /api/health has no `build` key and build_release
has no build_info writer.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def test_health_exposes_build_when_info_present():
    import bulk_downloader.app as a
    d = tempfile.mkdtemp()
    (Path(d) / "build_info.json").write_text(
        json.dumps({"sha": "deadbeef1234", "built_at": "2026-06-23T18:00:00"}))
    prev = os.environ.get("BD_INSTALL_DIR")
    os.environ["BD_INSTALL_DIR"] = d
    try:
        body = a.app.test_client().get("/api/health").get_json()
        assert "build" in body and body["build"], "health must expose build identity"
        assert body["build"]["sha"] == "deadbeef1234"
        assert body["build"]["built_at"] == "2026-06-23T18:00:00"
    finally:
        if prev is None:
            os.environ.pop("BD_INSTALL_DIR", None)
        else:
            os.environ["BD_INSTALL_DIR"] = prev


def test_health_omits_build_when_info_absent():
    import bulk_downloader.app as a
    d = tempfile.mkdtemp()  # empty: no build_info.json
    prev = os.environ.get("BD_INSTALL_DIR")
    os.environ["BD_INSTALL_DIR"] = d
    try:
        body = a.app.test_client().get("/api/health").get_json()
        # Graceful: either the key is absent or it's explicitly null — never a
        # fabricated sha, and never a 500.
        assert body.get("build") in (None, {}), body.get("build")
    finally:
        if prev is None:
            os.environ.pop("BD_INSTALL_DIR", None)
        else:
            os.environ["BD_INSTALL_DIR"] = prev


def _load_build_release():
    import importlib.util
    p = _REPO_ROOT / "tools" / "build_release.py"
    spec = importlib.util.spec_from_file_location("_bd_build_release", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_build_release_writes_build_info():
    mod = _load_build_release()
    d = tempfile.mkdtemp()
    # GREEN provides write_build_info(dest_dir, sha=...) -> dict, writing
    # build_info.json with {sha, built_at}.
    info = mod.write_build_info(Path(d), sha="abc123")
    f = Path(d) / "build_info.json"
    assert f.exists(), "build_release must write build_info.json"
    on_disk = json.loads(f.read_text())
    assert on_disk["sha"] == "abc123"
    assert on_disk.get("built_at"), "built_at must be stamped"
    assert info["sha"] == "abc123"
