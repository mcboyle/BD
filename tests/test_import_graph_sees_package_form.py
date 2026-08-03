"""The import graph must see `from bulk_downloader import X`.

`tools/dependency_graph.py:_internal_imports` handles three import idioms and
its docstring says so. It misses a fourth, and the miss is silent: for
`from bulk_downloader import db`, `n.module` is the PACKAGE, so the `cand`
computation resolves the string "bulk_downloader" -- which is not a node --
`_node()` returns None, and the edge is dropped. The P2 arm two lines below
reads `n.names` correctly for the relative form; the absolute form never
reaches it.

Silent is the operative word. The gate built on this predicate
(`tools/decomp/import_graph_gate.py`) then reports PASS over a graph missing
246 real edges: a denominator that structurally excludes part of its subject,
reporting clean -- CLAUDE.md section 0, in the instrument the section's own
band rule tells you to trust.

WHAT THIS FILE PINS, and why each part exists:

  (a) the three blind forms, each failing independently -- submodule alias,
      package-symbol alias, and the `tools` package;
  (b) that the two forms already handled keep working (CONTROLS -- without
      them a harness defect reads as a subject failure);
  (c) that the resolution happens INSIDE THE NAMED PACKAGE and never through
      `_node()`. This one is not decoration. `_node()` tests `if stem in
      bd_mods` FIRST (tools/dependency_graph.py:80-86), and four stems exist
      in BOTH packages -- llm_readiness, multi_site_benchmark,
      temporal_benchmark, validation_corpus. A `_node`-routed implementation
      passes every other assertion in this file and on today's tree produces a
      byte-identical 246-edge result, because no live site happens to trip the
      collision. The first `from tools import validation_corpus` written after
      that would fabricate an edge to bulk_downloader/validation_corpus.py and
      the gate, the band and the baseline would all stay green. So the fixture
      below deliberately puts `tool_b` in BOTH packages: it is the only
      assertion here that can tell the intended fix from the obvious
      simplification back to `_node`, and it exists because an adversarial
      pass measured that gap rather than because anyone predicted it.
  (d) a self-deriving completeness check over the live tree, with no pinned
      number, so it cannot go stale the way a hardcoded count would.

Conventions follow tests/test_import_graph_no_new_edges.py: the builder is
loaded BY PATH (never `from tools.dependency_graph import ...` -- the gate
deliberately stays out of the graph it measures, and so does its test), plain
`assert ..., msg` because the custom run_tests.py stub has no `pytest.fail`,
and zero-arg test functions.
"""
from __future__ import annotations

import ast
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

import pytest  # noqa: F401  (harmless under real pytest + the custom runner)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUILDER = _REPO_ROOT / "tools" / "dependency_graph.py"


def _load_builder():
    assert _BUILDER.exists(), (
        f"{_BUILDER.relative_to(_REPO_ROOT)} is missing -- it is the module that "
        f"owns the import predicate this file is about."
    )
    spec = importlib.util.spec_from_file_location("_p4_dependency_graph", _BUILDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fixture(tmp: Path) -> None:
    """A miniature tree exercising every arm of the predicate.

    `tool_b` exists in BOTH packages on purpose -- see (c) in the module
    docstring. Nothing else here collides.
    """
    bd = tmp / "bulk_downloader"
    tl = tmp / "tools"
    bd.mkdir(parents=True)
    tl.mkdir(parents=True)

    (bd / "__init__.py").write_text('__version__ = "0.0.0"\n', encoding="utf-8")
    (bd / "beta.py").write_text("VALUE = 1\n", encoding="utf-8")

    # SUBJECT 1: absolute package form, alias IS a submodule.
    (bd / "alpha.py").write_text(
        "from bulk_downloader import beta\n", encoding="utf-8")
    # SUBJECT 2: absolute package form, alias is a SYMBOL re-exported by
    # __init__ -- the edge is to the __init__ that defines it.
    (bd / "gamma.py").write_text(
        "from bulk_downloader import __version__\n", encoding="utf-8")
    # CONTROL 1: dotted module form, already handled today.
    (bd / "delta.py").write_text(
        "from bulk_downloader.beta import VALUE\n", encoding="utf-8")
    # CONTROL 2: relative form (P2), already handled today.
    (bd / "epsilon.py").write_text(
        "from . import beta\n", encoding="utf-8")

    # SUBJECT 3 + the _node discriminator: `tool_b` exists in tools/ AND in
    # bulk_downloader/. A predicate that resolves the alias through _node()
    # returns the bulk_downloader one, because _node checks bd_mods first.
    (tl / "tool_b.py").write_text("VALUE = 2\n", encoding="utf-8")
    (bd / "tool_b.py").write_text("VALUE = 3\n", encoding="utf-8")
    (tl / "tool_a.py").write_text(
        "from tools import tool_b\n", encoding="utf-8")


def _edges_from_fixture():
    """Build the fixture with the REAL builder, copied into a temp tree."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _fixture(tmp)
        (tmp / "tools").mkdir(exist_ok=True)
        shutil.copy2(_BUILDER, tmp / "tools" / "dependency_graph.py")
        mod = _load_builder()
        out = mod.build(tmp)["package"]["out"]
        return {(src, dst) for src, dsts in out.items() for dst in dsts}


def test_controls_still_resolve():
    """Without these, a harness defect is indistinguishable from the subject."""
    edges = _edges_from_fixture()
    assert ("bulk_downloader/delta.py", "bulk_downloader/beta.py") in edges, (
        "CONTROL FAILED: the dotted form `from bulk_downloader.beta import VALUE` "
        "no longer produces an edge. The harness or the builder is broken -- do "
        "not read the subject assertions below as evidence about the P4 form."
    )
    assert ("bulk_downloader/epsilon.py", "bulk_downloader/beta.py") in edges, (
        "CONTROL FAILED: the relative form `from . import beta` no longer "
        "produces an edge. Same warning as above."
    )


def test_absolute_package_form_resolves_a_submodule():
    edges = _edges_from_fixture()
    assert ("bulk_downloader/alpha.py", "bulk_downloader/beta.py") in edges, (
        "`from bulk_downloader import beta` produced no edge. n.module is the "
        "PACKAGE here, so the alias names are the targets; the `cand` path "
        "resolves \"bulk_downloader\", which is not a node, and the edge is "
        "dropped in silence."
    )


def test_absolute_package_form_resolves_a_reexported_symbol():
    edges = _edges_from_fixture()
    assert ("bulk_downloader/gamma.py", "bulk_downloader/__init__.py") in edges, (
        "`from bulk_downloader import __version__` produced no edge. The alias "
        "is not a submodule, so it is a name the package __init__ defines or "
        "re-exports -- that file is the edge target."
    )


def test_tools_package_form_resolves_inside_tools_not_bulk_downloader():
    """The one assertion that separates the fix from routing through _node().

    `tool_b` exists in both packages. `_node()` checks bd_mods first, so an
    implementation that resolves the alias through it targets the WRONG file --
    and every other assertion in this file still passes.
    """
    edges = _edges_from_fixture()
    assert ("tools/tool_a.py", "tools/tool_b.py") in edges, (
        "`from tools import tool_b` produced no edge to tools/tool_b.py."
    )
    assert ("tools/tool_a.py", "bulk_downloader/tool_b.py") not in edges, (
        "`from tools import tool_b` resolved into bulk_downloader/. The alias "
        "of an absolute package import must resolve INSIDE THE NAMED PACKAGE. "
        "This is what routing through _node() does -- it tests `stem in "
        "bd_mods` first (tools/dependency_graph.py:80-86), and four stems "
        "exist in both packages (llm_readiness, multi_site_benchmark, "
        "temporal_benchmark, validation_corpus). On today's tree that mistake "
        "is invisible: no live site trips the collision, so the edge COUNT is "
        "identical either way. This fixture is the only thing that can see it."
    )


def test_every_package_form_site_in_the_live_tree_has_an_edge():
    """Completeness over the real tree, self-derived -- no pinned number.

    A count would go stale on the next cut that adds an import. This asks the
    question directly instead: for every `from <pkg> import X` statement that
    names a real node, does the built graph contain that edge?
    """
    mod = _load_builder()
    graph = mod.build(_REPO_ROOT)["package"]["out"]
    bd_mods = mod._bd_mods(_REPO_ROOT)
    tool_stems = mod._tool_stems(_REPO_ROOT)

    misses = []
    for path in mod._py_files(_REPO_ROOT):
        rel = path.relative_to(_REPO_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.ImportFrom):
                continue
            if n.level != 0 or not n.module:
                continue
            parts = n.module.split(".")
            if len(parts) != 1 or parts[0] not in ("bulk_downloader", "tools"):
                continue
            stems = bd_mods if parts[0] == "bulk_downloader" else tool_stems
            for alias in n.names:
                head = alias.name.split(".")[0]
                if head in stems:
                    want = f"{parts[0]}/{head}.py"
                elif "__init__" in stems:
                    want = f"{parts[0]}/__init__.py"
                else:
                    continue
                if want == rel:
                    continue  # a module importing from its own package head
                if want not in graph.get(rel, ()):
                    misses.append(f"{rel} -> {want}  (line {n.lineno})")

    assert not misses, (
        f"{len(misses)} `from <package> import X` site(s) produce no edge in the "
        f"built graph. The predicate is blind to the absolute package form, so "
        f"the import-graph gate is passing over a graph that is missing them:\n  "
        + "\n  ".join(misses[:40])
        + ("\n  ..." if len(misses) > 40 else "")
    )
