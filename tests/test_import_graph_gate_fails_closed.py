"""DECOMP-R0 — the import-graph gate must FAIL CLOSED (CLAUDE.md 0).

`tools/decomp/import_graph_gate.py` measures its edge set through
`tools/dependency_graph.py`, whose `_parse()` returns None on SyntaxError and
whose `build()` then does `if tree is None: continue`. A file the parser cannot
read therefore contributes *no edges at all* — and the gate reported
`PASS: no new import edges` and exited 0 over that silently-reduced denominator.
Worse, `--update` would bake the reduced set into the frozen baseline, making
the blindness permanent.

Unknown is a third state and it fails. These tests assert:

  1. `--check` exits non-zero and names the count + the file(s) it could not
     parse, instead of reporting PASS over a denominator that excludes them.
  2. `--update` refuses the same way, leaving the baseline byte-identical —
     a gate must never freeze a graph it could not fully see.
  3. `--update` that would SHRINK the baseline refuses unless `--shrink` is
     passed explicitly, and `--shrink` lets the intended shrink through.
  4. The library entry point (`check()`), used by
     `tests/test_import_graph_no_new_edges.py`, raises rather than returning a
     clean-looking answer — the CLI is not the only door.
  5. Anti-cry-wolf: an entirely parseable fixture tree still passes, and so
     does the real repository tree. A gate that fires on identity gets switched
     off, which is also a soundness bug.

Conventions match `tests/test_import_graph_no_new_edges.py`: zero-arg test
functions, plain `assert ..., msg`, no pytest builtins or fixtures (the custom
`run_tests.py` runner ships a pytest stub).
"""
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest  # noqa: F401  (harmless under real pytest + the custom runner)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE = _REPO_ROOT / "tools" / "decomp" / "import_graph_gate.py"
_DEPGRAPH = _REPO_ROOT / "tools" / "dependency_graph.py"

_BROKEN = "def broken(:\n    pass\n"  # SyntaxError under every Python


def _make_fixture(tmp: Path) -> Path:
    """A minimal but structurally real tree: bulk_downloader/ + tools/, the
    live dependency_graph.py, and the gate under test at its real relative
    location so `_repo_root()` (parents[2]) resolves to the fixture root."""
    root = tmp / "fixroot"
    (root / "bulk_downloader").mkdir(parents=True)
    (root / "tools" / "decomp").mkdir(parents=True)
    (root / "bulk_downloader" / "alpha.py").write_text(
        "from . import beta\n", encoding="utf-8")
    (root / "bulk_downloader" / "beta.py").write_text("VALUE = 1\n", encoding="utf-8")
    shutil.copy2(_DEPGRAPH, root / "tools" / "dependency_graph.py")
    shutil.copy2(_GATE, root / "tools" / "decomp" / "import_graph_gate.py")
    return root


def _gate_cmd(root: Path, *args):
    return [sys.executable, str(root / "tools" / "decomp" / "import_graph_gate.py"), *args]


def _run(root: Path, *args):
    return subprocess.run(_gate_cmd(root, *args), capture_output=True, text=True)


def _baseline(root: Path) -> Path:
    return root / "tools" / "decomp" / "import_graph_baseline.json"


def _seed(root: Path):
    """Freeze a baseline over the clean fixture; assert that much works."""
    r = _run(root, "--update")
    assert r.returncode == 0, (
        "fixture harness is broken: --update on a wholly parseable fixture tree "
        f"exited {r.returncode}.\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert _baseline(root).exists(), "fixture harness is broken: no baseline written."


def _assert_the_gate_refused(out: str, mode: str) -> None:
    """Distinguish "the gate refused" from "something else crashed".

    Without this, these tests were VACUOUS -- proven by mutation: reverting the
    gate to its pre-fix state left them green. The fixture copies the live
    tools/dependency_graph.py, which independently raises on an unparseable
    file. Its traceback happens to contain the word "unparseable", the count,
    the filename, AND a non-zero exit -- so every assertion above was satisfied
    by a crash originating in a DIFFERENT tool. The subject was never measured.

    Measured discriminator (both cells, same fixture):
        reverted gate -> "Traceback" present, "denominator:" absent
        fixed gate    -> "Traceback" absent,  "denominator:" present

    A refusal is a designed exit. A traceback is the absence of one.
    """
    assert "Traceback" not in out, (
        f"{mode} crashed instead of refusing. The non-zero exit came from an "
        f"exception escaping some other tool, not from this gate's fail-closed "
        f"path -- so this test would pass with the gate's fix removed."
        f"\noutput:\n{out}"
    )
    assert "denominator:" in out, (
        f"{mode} exited non-zero without emitting this gate's own refusal "
        f"message. Only the gate names its denominator; anything else reaching "
        f"here is a different failure wearing the same exit code."
        f"\noutput:\n{out}"
    )


def _load_gate():
    spec = importlib.util.spec_from_file_location("_r0_gate_failclosed", _GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_check_refuses_when_a_source_file_is_unparseable():
    with tempfile.TemporaryDirectory() as td:
        root = _make_fixture(Path(td))
        _seed(root)
        (root / "bulk_downloader" / "broken.py").write_text(_BROKEN, encoding="utf-8")

        r = _run(root, "--check")
        out = r.stdout + r.stderr
        assert r.returncode != 0, (
            "--check exited 0 with an unparseable source file present: the gate "
            "reported clean over a denominator that structurally excludes its "
            f"subject (CLAUDE.md 0).\noutput:\n{out}"
        )
        assert "unparseable" in out, (
            f"--check failed but never said why; expected 'unparseable'.\noutput:\n{out}"
        )
        assert "1 file" in out, (
            f"--check did not name the count of unparseable files.\noutput:\n{out}"
        )
        assert "bulk_downloader/broken.py" in out, (
            f"--check did not name the unparseable file.\noutput:\n{out}"
        )
        _assert_the_gate_refused(out, "--check")


def test_update_refuses_and_preserves_baseline_when_unparseable():
    with tempfile.TemporaryDirectory() as td:
        root = _make_fixture(Path(td))
        _seed(root)
        before = _baseline(root).read_bytes()
        (root / "bulk_downloader" / "broken.py").write_text(_BROKEN, encoding="utf-8")

        r = _run(root, "--update")
        out = r.stdout + r.stderr
        assert r.returncode != 0, (
            "--update exited 0 with an unparseable source file present — it would "
            "bake the reduced edge set in and blind the gate permanently."
            f"\noutput:\n{out}"
        )
        assert "unparseable" in out, (
            f"--update failed but never said why; expected 'unparseable'.\noutput:\n{out}"
        )
        assert _baseline(root).read_bytes() == before, (
            "--update rewrote the frozen baseline despite refusing — the reduced "
            "edge set was persisted anyway."
        )
        _assert_the_gate_refused(out, "--update")


def test_update_refuses_a_silent_shrink_but_allows_a_declared_one():
    with tempfile.TemporaryDirectory() as td:
        root = _make_fixture(Path(td))
        _seed(root)
        before = _baseline(root).read_bytes()
        # Remove the alpha -> beta coupling. Every file still parses; the only
        # change is that the baseline would lose an edge.
        (root / "bulk_downloader" / "alpha.py").write_text("VALUE = 2\n", encoding="utf-8")

        r = _run(root, "--update")
        out = r.stdout + r.stderr
        assert r.returncode != 0, (
            "--update silently shrank the baseline; a shrink must be declared "
            f"with --shrink.\noutput:\n{out}"
        )
        assert "shrink" in out.lower(), (
            f"--update refused but never mentioned the shrink.\noutput:\n{out}"
        )
        assert "bulk_downloader/alpha.py" in out, (
            f"--update did not name the edge(s) it would drop.\noutput:\n{out}"
        )
        assert _baseline(root).read_bytes() == before, (
            "--update rewrote the baseline despite refusing the shrink."
        )

        r2 = _run(root, "--update", "--shrink")
        out2 = r2.stdout + r2.stderr
        assert r2.returncode == 0, (
            f"--update --shrink refused a declared shrink.\noutput:\n{out2}"
        )
        assert _baseline(root).read_bytes() != before, (
            "--update --shrink exited 0 but did not rewrite the baseline."
        )


def test_library_check_raises_rather_than_returning_a_clean_answer():
    """`tests/test_import_graph_no_new_edges.py` calls `gate.check(root)`
    directly; the fail-closed behaviour must live below the CLI too."""
    with tempfile.TemporaryDirectory() as td:
        root = _make_fixture(Path(td))
        _seed(root)
        (root / "bulk_downloader" / "broken.py").write_text(_BROKEN, encoding="utf-8")
        gate = _load_gate()
        raised = None
        try:
            result = gate.check(root)
        except Exception as exc:  # noqa: BLE001 — the type is asserted below
            raised = exc
        assert raised is not None, (
            "gate.check() returned "
            f"{result!r} over a tree containing an unparseable file instead of "
            "raising — the library door is still open."
        )
        assert "unparseable" in str(raised).lower(), (
            f"gate.check() raised, but not about unparseability: {raised!r}"
        )
        assert "broken.py" in str(raised), (
            f"gate.check() raised without naming the file: {raised!r}"
        )
        # The exception must be THIS gate's, not one bubbling up from
        # dependency_graph. With the gate reverted, `UnparseableSourceError`
        # does not exist and this line raises AttributeError -- which is the
        # correct outcome: the test fails when the fix is gone. Matching on
        # message text alone did not, because the other tool's traceback
        # carries the same words.
        assert isinstance(raised, gate.UnparseableSourceError), (
            f"gate.check() raised {type(raised).__name__}, not the gate's own "
            f"UnparseableSourceError. A refusal from a different layer is not "
            f"evidence that this gate fails closed: {raised!r}"
        )


def test_clean_fixture_tree_still_passes():
    """Anti-cry-wolf half 1: nothing unparseable, nothing removed -> exit 0."""
    with tempfile.TemporaryDirectory() as td:
        root = _make_fixture(Path(td))
        _seed(root)
        r = _run(root, "--check")
        out = r.stdout + r.stderr
        assert r.returncode == 0, (
            f"--check failed on a wholly parseable, unchanged tree.\noutput:\n{out}"
        )
        assert "PASS" in out, f"--check passed but did not say so.\noutput:\n{out}"


def test_real_repository_tree_still_passes():
    """Anti-cry-wolf half 2: the gate must still be usable on the real tree.
    A gate that fires on identity gets switched off."""
    r = subprocess.run(
        [sys.executable, str(_GATE), "--check"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    out = r.stdout + r.stderr
    assert r.returncode == 0, (
        f"--check now fails on the real repository tree.\noutput:\n{out}"
    )
