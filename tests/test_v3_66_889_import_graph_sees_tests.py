"""The import-graph gate was blind to tests/ edges, and the band tool hid it.

TWO HALVES, ONE CUT, because shipping the first without the second converts a
per-cut chore into a per-cut TRAP.

  THE BLIND SPOT. The gate compares an edge set against a frozen baseline, but
  the edge set came from `dependency_graph.build()`, whose `_py_files` walks a
  hardcoded ("bulk_downloader", "tools"). MEASURED at v3.66.888: the baseline
  holds 1618 edges over 506 source keys, and including tests/ adds 2132 more
  from 1234 test files (0 parse failures). So 57% of the real internal import
  surface was outside the gate's denominator.

  THE TRAP. `bd-band-derive` fires its "import edges" regen flag only for
  changes under `bulk_downloader/`. Widen the gate alone and a tests/-only cut
  gets NO flag and does NOT band the gate's own suite -- so the author is never
  told they owe a re-freeze, and the failure surfaces on the box instead of in
  the sandbox. CLAUDE.md section 4 records that exact shape costing five
  releases with `test_source_windows_do_not_shift` red on main.

WHY THE ENUMERATOR AND NOT THE WALKER. `dependency_graph._py_files` also feeds
`DEPENDENCY_GRAPH.json` AND the config sub-graph, so widening it there would
make test files count as config readers/writers -- a semantic change to a
DIFFERENT gate's denominator, riding along invisibly. The gate gets its own
file list instead, and still reuses `dependency_graph`'s `_parse`,
`_internal_imports`, `_bd_mods` and `_tool_stems`. The thing that must not
drift -- the predicate deciding what counts as an edge -- stays single-sourced;
only the list of files widens.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[1]
_GATE = _REPO / "tools" / "decomp" / "import_graph_gate.py"


def _gate():
    spec = importlib.util.spec_from_file_location("_igg_tests", _GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# RED: the gate cannot see tests/                                              #
# --------------------------------------------------------------------------- #

def test_the_edge_set_contains_tests_edges():
    """A tests/ file importing bulk_downloader is an internal import edge."""
    g = _gate()
    edges = g.current_edge_set(_REPO)
    from_tests = {(s, d) for s, d in edges if s.startswith("tests/")}
    assert from_tests, (
        "the gate's edge set contains ZERO edges out of tests/, so every "
        "import a test file makes is outside the denominator it gates on.")
    assert any(d.startswith("bulk_downloader/") for _s, d in from_tests), (
        "tests/ edges exist but none reach bulk_downloader/ -- the predicate "
        "is not seeing the dominant test import idiom.")


def test_the_parse_check_covers_tests():
    """`assert_fully_parseable` must fail closed over the SAME files the edge
    set is derived from, or the gate can be blind and clean at once."""
    g = _gate()
    dep = g._load_dependency_graph(_REPO)
    files, label = g._source_files(_REPO, dep)
    rel = [p.relative_to(_REPO).as_posix() for p in files]
    assert any(r.startswith("tests/") for r in rel), (
        "the parse-check denominator (%s) excludes tests/ while the edge set "
        "is meant to include it -- an unparseable test file would be invisible."
        % label)


def test_band_derive_flags_import_edges_for_a_tests_change():
    """The trap half. Without this, widening the gate is a silent obligation.

    Asserted through the tool's real CLI rather than by importing its
    internals, because what must be true is what an author actually sees.
    """
    r = subprocess.run(
        [sys.executable, str(_REPO / "toolchain" / "bin" / "bd-band-derive"),
         "--files", "tests/test_contracts.py", "--json"],
        capture_output=True, text=True, timeout=300, cwd=str(_REPO))
    assert r.returncode == 0, r.stderr[-600:]
    payload = json.loads(r.stdout)
    flags = " ".join(str(f) for f in (payload.get("regen_flags") or []))
    band = list(payload.get("band") or [])

    # NOT `"import_graph" in " ".join(band)`. The first draft asserted that and
    # PASSED on pristine source -- because this very file's NAME contains
    # "import_graph", so bd-band-derive's filename-stem signal pulled it in and
    # the test certified itself. Name the gate's own suite exactly.
    gate_suite_banded = "tests/test_import_graph_no_new_edges.py" in band
    assert ("import" in flags.lower()) or gate_suite_banded, (
        "a tests/-only change produced no import-edge regen flag (%r) and did "
        "not band tests/test_import_graph_no_new_edges.py. After the gate "
        "widens, that author owes a baseline re-freeze nobody told them about, "
        "and it fails on the box, not in the sandbox." % flags)


# --------------------------------------------------------------------------- #
# GREEN before AND after: widening must not lose or invent product edges       #
# --------------------------------------------------------------------------- #

def test_product_edges_are_not_lost():
    """The pre-existing bulk_downloader/tools surface must survive intact."""
    g = _gate()
    edges = g.current_edge_set(_REPO)
    product = {(s, d) for s, d in edges if not s.startswith("tests/")}
    base = json.loads((_REPO / "tools" / "decomp" / "import_graph_baseline.json")
                      .read_text(encoding="utf-8"))
    declared = {(s, d) for s, lst in base["edges"].items() for d in lst
                if not s.startswith("tests/")}
    missing = declared - product
    assert not missing, (
        "widening the enumerator DROPPED %d product edge(s) the baseline "
        "declares, e.g. %r" % (len(missing), sorted(missing)[:5]))


def test_the_gate_still_passes_against_its_baseline():
    """--check must be green on a tree whose baseline is current. This is the
    over-sensitivity direction: a widened gate with a stale baseline would red
    every cut until re-frozen, which is why the re-freeze ships here."""
    r = subprocess.run([sys.executable, str(_GATE), "--check"],
                       capture_output=True, text=True, timeout=600,
                       cwd=str(_REPO))
    assert r.returncode == 0, (
        "import_graph_gate --check failed on a tree that should be clean; the "
        "baseline was not re-frozen in the same cut.\n%s"
        % (r.stdout + r.stderr)[-1500:])


def test_the_predicate_is_still_single_sourced():
    """Only the FILE LIST widens. The edge predicate must remain the one in
    dependency_graph, or two denominators start drifting -- the exact defect
    CLAUDE.md section 8 records for the two 'tools' populations."""
    src = _GATE.read_text(encoding="utf-8")
    assert "_internal_imports" in src, (
        "the gate no longer references dependency_graph._internal_imports; if "
        "it has grown its own edge predicate, the two will drift.")
    assert "def _internal_imports" not in src, (
        "the gate DEFINES its own _internal_imports -- the predicate has been "
        "forked, which is what this cut was written to avoid.")
