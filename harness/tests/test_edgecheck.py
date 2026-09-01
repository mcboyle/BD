"""bd-edgecheck.py: declare the routine import edges, refuse the hazardous ones.

MEASURED JUSTIFICATION, not a preference. Over the 4,141-edge baseline at
v3.66.1371: 2,443 edges are test->product (59%), 1,698 are product->product
(41%), and there are ZERO in either direction involving tests on the far side.
The gate's own docstring names its hazard as "accidental-coupling /
lazy-accessor-sprawl", which lives entirely in the 41%. Four cuts on
2026-08-31 were refused by that gate, and every one was a new test file
importing its own subject -- four CI round trips, zero coupling defects.

So the behaviour under test is a split: routine test->product edges are
declarable without ceremony, product->product edges REFUSE, and anything
touching tests/ on the far side refuses regardless of flags.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home()))) / "bd-edgecheck.py"
REPO = Path("/home/mboyle/BulkDownloader")


def run(work, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--work", str(work), *extra],
                          capture_output=True, text=True)


@pytest.fixture()
def tree(tmp_path):
    """A tree carrying a real gate, a real baseline, and a stub interpreter."""
    (tmp_path / "tools" / "decomp").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "bulk_downloader").mkdir()
    (tmp_path / "venv" / "bin").mkdir(parents=True)

    base = {"edge_count": 1, "edges": {"bulk_downloader/a.py": ["bulk_downloader/b.py"]}}
    (tmp_path / "tools/decomp/import_graph_baseline.json").write_text(json.dumps(base))

    # A stub gate whose --update writes whatever LIVE_EDGES says. It stands in
    # for the real gate's derivation, which is not this tool's subject: this
    # tool must never re-derive the graph itself, only diff before against after.
    (tmp_path / "tools/decomp/import_graph_gate.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "if os.environ.get('GATE_REFUSES'):\n"
        "    sys.stderr.write('gate refuses to shrink\\n'); sys.exit(3)\n"
        "live = json.loads(os.environ.get('LIVE_EDGES', '{}'))\n"
        "p = Path(__file__).resolve().parents[2] / 'tools/decomp/import_graph_baseline.json'\n"
        "if '--update' in sys.argv:\n"
        "    p.write_text(json.dumps({'edge_count': sum(len(v) for v in live.values()), 'edges': live}))\n"
        "sys.exit(0)\n")
    (tmp_path / "venv/bin/python").write_text(f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    (tmp_path / "venv/bin/python").chmod(0o755)
    return tmp_path


def live(tree, edges):
    os.environ["LIVE_EDGES"] = json.dumps(edges)


def test_no_new_edges_passes(tree):
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py"]})
    r = run(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "no new import edges" in r.stdout


def test_a_routine_test_edge_is_reported_and_declarable(tree):
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py"],
                "tests/test_x.py": ["bulk_downloader/a.py"]})
    r = run(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "test->product 1" in r.stdout and "product->product 0" in r.stdout
    assert "routine" in r.stdout


def test_a_dry_run_leaves_the_baseline_exactly_as_found(tree):
    """The precondition that makes this tool safe to run at any time."""
    p = tree / "tools/decomp/import_graph_baseline.json"
    before = p.read_bytes()
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py"],
                "tests/test_x.py": ["bulk_downloader/a.py"]})
    assert run(tree).returncode == 0
    assert p.read_bytes() == before, "a dry run must not touch the baseline"


def test_declare_actually_rewrites_the_baseline(tree):
    p = tree / "tools/decomp/import_graph_baseline.json"
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py"],
                "tests/test_x.py": ["bulk_downloader/a.py"]})
    r = run(tree, "--declare")
    assert r.returncode == 0, r.stdout + r.stderr
    after = json.loads(p.read_text())["edges"]
    assert after["tests/test_x.py"] == ["bulk_downloader/a.py"]


def test_a_product_coupling_edge_refuses(tree):
    """The hazard the gate exists for. It must not be auto-declared."""
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py", "bulk_downloader/c.py"]})
    r = run(tree)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "COUPLING" in r.stdout
    assert "product->product edge" in r.stderr


def test_coupling_declares_only_with_a_stated_reason(tree):
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py", "bulk_downloader/c.py"]})
    r = run(tree, "--declare", "--allow-coupling", "b and c genuinely share a seam")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "b and c genuinely share a seam" in r.stdout
    after = json.loads((tree / "tools/decomp/import_graph_baseline.json").read_text())["edges"]
    assert "bulk_downloader/c.py" in after["bulk_downloader/a.py"]


def test_an_edge_into_tests_refuses_regardless_of_flags(tree):
    """Zero such edges exist in the real baseline; that is the evidence it
    never happens, so it is never routine."""
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py", "tests/helper.py"]})
    r = run(tree, "--declare", "--allow-coupling", "trying to force it")
    assert r.returncode == 1, r.stdout + r.stderr
    assert "UNUSUAL" in r.stdout
    assert "crosses into tests/" in r.stderr


def test_a_refusing_gate_is_cannot_evaluate_not_ok(tree):
    """An unavailable measurement returns UNKNOWN, never OK -- CLAUDE.md A7."""
    live(tree, {"bulk_downloader/a.py": ["bulk_downloader/b.py"]})
    env = dict(os.environ, GATE_REFUSES="1")
    r = subprocess.run([sys.executable, str(SCRIPT), "--work", str(tree)],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "CANNOT-EVALUATE" in r.stderr


def test_a_refusing_gate_restores_the_baseline(tree):
    p = tree / "tools/decomp/import_graph_baseline.json"
    before = p.read_bytes()
    env = dict(os.environ, GATE_REFUSES="1")
    subprocess.run([sys.executable, str(SCRIPT), "--work", str(tree), "--declare"],
                   capture_output=True, text=True, env=env)
    assert p.read_bytes() == before


def test_a_missing_baseline_is_cannot_evaluate(tmp_path):
    r = run(tmp_path)
    assert r.returncode == 2
    assert "CANNOT-EVALUATE" in r.stderr


@pytest.mark.skipif(not REPO.is_dir(), reason="integrator repo not present")
def test_the_real_baseline_has_the_measured_shape():
    """The premise this tool rests on, asserted against the real tree.

    If product->test or test->test edges ever appear, the split this tool
    makes is no longer safe and this test says so.
    """
    b = json.loads((REPO / "tools/decomp/import_graph_baseline.json").read_text())
    edges = b.get("edges") or {k: v for k, v in b.items() if isinstance(v, list)}
    counts = {"test->product": 0, "product->product": 0, "product->test": 0, "test->test": 0}
    for src, dsts in edges.items():
        s = src.startswith("tests/")
        for dst in dsts:
            d = dst.startswith("tests/")
            counts["test->product" if (s and not d) else
                   "product->product" if (not s and not d) else
                   "product->test" if (not s and d) else "test->test"] += 1
    assert counts["product->test"] == 0, counts
    assert counts["test->test"] == 0, counts
    assert counts["test->product"] > 0 and counts["product->product"] > 0, counts
