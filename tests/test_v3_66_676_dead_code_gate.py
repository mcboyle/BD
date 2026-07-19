"""v3.66.676 -- MNT-1: continuous (advisory) dead-code gate.

Proves the function-level scanner: a referenced function is NOT flagged, an
unreferenced one IS; __all__ exports and decorated (dynamically-reached)
functions are never flagged; and the CLI is advisory (exit 0) unless --strict.
Uses a tiny fixture tree so it is deterministic and fast. Zero-arg tests.
"""
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

_GATE = Path(__file__).resolve().parent.parent / "tools" / "decomp" / "dead_code_gate.py"


def _load_gate():
    spec = importlib.util.spec_from_file_location("dead_code_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture():
    d = tempfile.mkdtemp(prefix="mnt1_")
    (Path(d) / "mod_a.py").write_text(
        "def used():\n    return 1\n\n"
        "def orphan():\n    return 2\n\n"
        "def _also_orphan():\n    return 3\n",
        encoding="utf-8")
    # mod_b references used() but never orphan()/_also_orphan()
    (Path(d) / "mod_b.py").write_text(
        "import mod_a\n\n"
        "def caller():\n    return mod_a.used()\n",
        encoding="utf-8")
    return d


def test_flags_only_unreferenced_functions():
    gate = _load_gate()
    d = _fixture()
    res = gate.scan(d, [d])
    dead = {c["name"] for c in res["candidates"]}
    assert "used" not in dead, "a referenced function must not be flagged"
    # caller() is itself referenced by nothing in the fixture -> a valid candidate
    assert "caller" in dead
    assert "orphan" in dead
    assert "_also_orphan" in dead


def test_all_export_and_decorated_are_skipped():
    gate = _load_gate()
    d = tempfile.mkdtemp(prefix="mnt1_")
    (Path(d) / "m.py").write_text(
        "__all__ = ['exported']\n\n"
        "def exported():\n    return 1\n\n"
        "import functools\n"
        "@functools.lru_cache\n"
        "def decorated():\n    return 2\n\n"
        "def truly_dead():\n    return 3\n",
        encoding="utf-8")
    res = gate.scan(d, [d])
    dead = {c["name"] for c in res["candidates"]}
    assert "exported" not in dead, "__all__ exports are public -> not dead"
    assert "decorated" not in dead, "decorated fns are dynamically reached -> skipped"
    assert "truly_dead" in dead


def test_cli_is_advisory_by_default():
    gate = _load_gate()
    d = _fixture()
    # advisory: exit 0 even with candidates present
    rc = gate.main(["--root", d, "--json"])
    assert rc == 0
    # strict flips to exit 1 when candidates remain -- but --root points at the
    # repo layout (root/bulk_downloader); the fixture has no such subdir, so scan
    # sees no defs -> no candidates -> strict still 0. Exercise strict on a repo-
    # shaped fixture instead:
    repo = tempfile.mkdtemp(prefix="mnt1_repo_")
    pkg = Path(repo) / "bulk_downloader"
    pkg.mkdir()
    (pkg / "x.py").write_text("def lonely():\n    return 1\n", encoding="utf-8")
    (Path(repo) / "tools").mkdir()
    (Path(repo) / "tests").mkdir()
    assert gate.main(["--root", repo]) == 0            # advisory
    assert gate.main(["--root", repo, "--strict"]) == 1  # strict catches 'lonely'
