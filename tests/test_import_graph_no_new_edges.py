"""DECOMP-R0 — import-graph regression gate.

The surface invariants (`route_map` snapshot, the `*_surface_lock` tests,
`runner_api_snapshot`) prove that a decomposition cut removed *nothing* — no
route, method, or public name vanished. They are blind to the opposite failure:
a cut that quietly *adds* an inter-module import edge it was not supposed to —
the accidental-coupling / lazy-accessor-sprawl class (hazard H-14).

This test is that complement. It freezes the intended internal import-edge set
(`tools/decomp/import_graph_baseline.json`) and asserts the live graph adds no
edge outside it. When a cut *intends* a new edge, re-freeze the baseline in the
same cut (`python3 tools/decomp/import_graph_gate.py --update`) — exactly the way
a guard-SHA change is declared, never silent.

Conventions: the custom `run_tests.py` runner chdirs to a temp dir, so the gate
is loaded by absolute path off this file's location (not via `tools.decomp.*`,
which also keeps the gate out of the product import graph it measures). Uses
`assert ..., msg` (the runner's pytest stub has no `pytest.fail`); zero-arg test
functions; no pytest builtins.
"""
import importlib.util
import sys
from pathlib import Path

import pytest  # noqa: F401  (harmless under real pytest + the custom runner)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GATE = _REPO_ROOT / "tools" / "decomp" / "import_graph_gate.py"


def _load_gate():
    assert _GATE.exists(), (
        f"{_GATE.relative_to(_REPO_ROOT)} is missing — the DECOMP-R0 import-graph "
        f"gate was not landed."
    )
    spec = importlib.util.spec_from_file_location("_r0_import_graph_gate", _GATE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_baseline_present_and_well_formed():
    gate = _load_gate()
    base = gate.load_baseline(_REPO_ROOT)
    assert base["edges"], "frozen baseline has no edges — it was never populated."
    assert base["edge_count"] == sum(len(v) for v in base["edges"].values()), (
        "baseline edge_count disagrees with its own edge map — regenerate with "
        "`python3 tools/decomp/import_graph_gate.py --update`."
    )


def test_no_new_edges():
    """The gate proper: the live graph introduces no edge outside the frozen set."""
    gate = _load_gate()
    new, removed = gate.check(_REPO_ROOT)
    assert not new, (
        "NEW import edge(s) not in the frozen baseline — a cut coupled modules it "
        "should not have (or an intended edge was not declared). Review, then if "
        "intended re-freeze with `python3 tools/decomp/import_graph_gate.py "
        "--update` in the SAME cut:\n  " + "\n  ".join(f"{s} -> {d}" for s, d in new)
    )


def test_gate_detects_an_injected_edge():
    """Teeth: prove the comparison is not vacuous — an edge absent from the
    baseline must be reported as NEW. Pure in-memory; mutates no tree."""
    gate = _load_gate()
    base = gate.load_baseline(_REPO_ROOT)
    fake = ("bulk_downloader/__r0_probe_src.py", "bulk_downloader/__r0_probe_dst.py")
    injected = {(s, d) for s, lst in base["edges"].items() for d in lst}
    injected.add(fake)
    new, _ = gate.compare_edges(_baseline_edge_set(base), injected)
    assert fake in set(new), (
        "the gate failed to flag a synthetic edge absent from the baseline — the "
        "regression check would pass vacuously."
    )


def _baseline_edge_set(base):
    return {(s, d) for s, lst in base["edges"].items() for d in lst}
