"""CUT 4b — `tools/dependency_graph.py` must fail closed on unparseable source.

CLAUDE.md 0: a gate that cannot see the thing it is asked about reports OK, and
that is worse than no gate. `_parse()` used to swallow SyntaxError/OSError and
`build()` skipped the file, so a module the parser could not read contributed
*no edges* and every entry point (--check, --json, --selftest, the default
regen) still reported success over a silently shortened denominator.

Unknown is a third state and it fails. These tests assert both directions:

  * FIRES on a real blind spot — an unparseable file inside the walked tree
    makes `build()` raise and makes every CLI mode exit non-zero, naming the
    count and the file, and the regen writes nothing.
  * DOES NOT fire on identity — the clean fixture and the live repository tree
    both build successfully. A gate that cries wolf gets switched off.

The denominator is the builder's own walk (`_py_files`), so the check is
derived, not asserted: a file the builder never parses is never flagged.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: F401  (harmless under real pytest + the custom runner)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import tools.dependency_graph as DG  # noqa: E402

_SCRIPT = _REPO_ROOT / "tools" / "dependency_graph.py"

_GOOD = "from . import db\n\n\ndef go():\n    return db\n"
# `def f(:` is a hard SyntaxError under every supported interpreter.
_BAD = "def broken(:\n    pass\n"


def _mktree(tmp_path, bad=()):
    """A miniature repo root: bulk_downloader/ + tools/, plus optional bad files.

    `bad` holds repo-relative paths that get unparseable contents."""
    for rel in ("bulk_downloader", "tools"):
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    (tmp_path / "bulk_downloader" / "db.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "bulk_downloader" / "app.py").write_text(_GOOD, encoding="utf-8")
    (tmp_path / "tools" / "helper.py").write_text("import os\n", encoding="utf-8")
    for rel in bad:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(_BAD, encoding="utf-8")
    return tmp_path


def _run(root, *args):
    env = dict(os.environ, BD_ROOT=str(root))
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True)


# ── the gate FIRES on a blind spot ─────────────────────────────────────────
def test_build_refuses_on_unparseable_file(tmp_path):
    root = _mktree(tmp_path, bad=("bulk_downloader/broken.py",))
    try:
        DG.build(root)
    except Exception as e:                              # noqa: BLE001
        assert type(e).__name__ == "UnparseableSourceError", type(e).__name__
        assert "bulk_downloader/broken.py" in str(e), str(e)
        assert "1" in str(e), str(e)
        assert getattr(e, "files", None) == ["bulk_downloader/broken.py"], \
            getattr(e, "files", None)
    else:
        raise AssertionError(
            "build() returned a graph over a tree containing an unparseable "
            "file — the edges of that file are silently missing")


def test_build_names_every_unparseable_file(tmp_path):
    """Counting one and stopping hides the rest; the report is the whole set."""
    root = _mktree(tmp_path, bad=("bulk_downloader/broken.py",
                                  "tools/sub/also_broken.py"))
    with pytest.raises(Exception) as ei:
        DG.build(root)
    files = getattr(ei.value, "files", [])
    assert files == ["bulk_downloader/broken.py", "tools/sub/also_broken.py"], files
    assert "2" in str(ei.value), str(ei.value)


def test_unparseable_sources_denominator_is_the_builder_walk(tmp_path):
    """Derived, not asserted: the checked set IS `_py_files`.

    A broken file outside the walked tree must NOT be flagged (no cry-wolf),
    and one nested inside it must be (no blind spot)."""
    root = _mktree(tmp_path, bad=("tools/sub/also_broken.py",))
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "scripts" / "outside.py").write_text(_BAD, encoding="utf-8")
    (root / "bulk_downloader" / "__pycache__").mkdir(parents=True, exist_ok=True)
    (root / "bulk_downloader" / "__pycache__" / "cached.py").write_text(
        _BAD, encoding="utf-8")

    bad = [rel for rel, _msg in DG.unparseable_sources(root)]
    walked = {p.relative_to(root).as_posix() for p in DG._py_files(root)}
    assert bad == ["tools/sub/also_broken.py"], bad
    assert set(bad) <= walked, (bad, sorted(walked))
    assert "scripts/outside.py" not in walked


@pytest.mark.parametrize("args", [(), ("--check",), ("--json",), ("--selftest",)])
def test_every_cli_mode_exits_nonzero_naming_the_file(tmp_path, args):
    root = _mktree(tmp_path, bad=("bulk_downloader/broken.py",))
    r = _run(root, *args)
    out = r.stdout + r.stderr
    assert r.returncode != 0, f"exit={r.returncode} args={args}\n{out}"
    assert "bulk_downloader/broken.py" in out, out
    assert "Traceback" not in out, out


def test_regen_writes_nothing_when_a_file_will_not_parse(tmp_path):
    root = _mktree(tmp_path, bad=("bulk_downloader/broken.py",))
    r = _run(root)
    assert r.returncode != 0, r.stdout + r.stderr
    assert not (root / "DEPENDENCY_GRAPH.json").exists()
    assert not (root / "DEPENDENCY_GRAPH.md").exists()


def test_check_does_not_report_ok_over_a_short_denominator(tmp_path):
    """The specific S0: --check printed OK while a file contributed no edges."""
    root = _mktree(tmp_path)
    good = _run(root)
    assert good.returncode == 0, good.stdout + good.stderr
    (root / "bulk_downloader" / "broken.py").write_text(_BAD, encoding="utf-8")
    r = _run(root, "--check")
    assert r.returncode != 0, r.stdout + r.stderr
    assert "OK: dependency graph in sync" not in r.stdout, r.stdout


def test_unreadable_file_is_also_unknown(tmp_path):
    """A file that cannot be read is not a file that parses clean."""
    root = _mktree(tmp_path)
    p = root / "bulk_downloader" / "nul.py"
    p.write_bytes(b"X = 1\x00\n")                    # ValueError from ast.parse
    bad = [rel for rel, _msg in DG.unparseable_sources(root)]
    assert bad == ["bulk_downloader/nul.py"], bad


# ── the gate does NOT fire on identity ─────────────────────────────────────
def test_clean_fixture_builds_and_regens(tmp_path):
    root = _mktree(tmp_path)
    g = DG.build(root)
    assert g["package"]["edge_count"] >= 1, g["package"]
    assert DG.unparseable_sources(root) == []
    r = _run(root)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (root / "DEPENDENCY_GRAPH.json").exists()
    assert json.loads((root / "DEPENDENCY_GRAPH.json").read_text())["graph_version"]


def test_live_repository_tree_still_builds():
    """Anti-cry-wolf: the real tree must pass under the interpreter BD tests run.

    If this fails, the tree has an unparseable file — fix the tree, not the gate."""
    bad = DG.unparseable_sources(_REPO_ROOT)
    assert bad == [], bad
    g = DG.build(_REPO_ROOT)
    assert g["package"]["edge_count"] > 600, g["package"]["edge_count"]
