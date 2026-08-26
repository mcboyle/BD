"""The canonical regeneration selftest must know every required generator.

Before v3.66.1256 the selftest built its expected labels from ``CHAIN`` itself.
Deleting eight of the ten entries therefore left ``--selftest`` green, and the
main loop then skipped the same generator while still printing REGEN COMPLETE.
The expected ten labels below are deliberately independent of the production
chain so the deletion experiment cannot shrink its own denominator.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import sys

import pytest


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
REGEN = REPO / "toolchain" / "bin" / "bd-regen-order"
EXPECTED_LABELS = (
    "FRONTEND_SECRET_KEYS",
    "gui_parity",
    "ROUTE_INDEX",
    "ENDPOINT_CATALOG",
    "DEPENDENCY_GRAPH",
    "FUNCTION_INDEX",
    "INV_TAGS",
    "SOURCE_WINDOW_HASHES",
    "PIN_INDEX",
    "STATIC_KB",
)


def _load_regen():
    loader = importlib.machinery.SourceFileLoader("regen_order_1256", str(REGEN))
    spec = importlib.util.spec_from_loader("regen_order_1256", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run_selftest(module, monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setattr(sys, "argv", [str(REGEN), "--selftest"])
    rc = module.main()
    return rc, capsys.readouterr().out


def test_unmodified_chain_is_the_exact_nonzero_ten_and_selftest_passes(
        monkeypatch, capsys):
    module = _load_regen()
    labels = tuple(entry[0] for entry in module.CHAIN)
    assert len(EXPECTED_LABELS) == 10
    assert labels == EXPECTED_LABELS

    rc, output = _run_selftest(module, monkeypatch, capsys)
    assert rc == 0, output
    assert "SELFTEST PASS" in output
    assert "FROZEN BASELINES are not auto-re-frozen" in output
    assert "check_route_counts is a gate, not a generator" in output


@pytest.mark.parametrize("missing", EXPECTED_LABELS)
def test_deleting_each_chain_entry_in_memory_fails_selftest_and_names_it(
        missing, monkeypatch, capsys):
    module = _load_regen()
    original = list(module.CHAIN)
    assert len(original) == len(EXPECTED_LABELS) == 10
    assert [entry[0] for entry in original].count(missing) == 1

    shortened = [entry for entry in original if entry[0] != missing]
    assert len(shortened) == 9
    monkeypatch.setattr(module, "CHAIN", shortened)

    rc, output = _run_selftest(module, monkeypatch, capsys)
    assert rc == 1, output
    assert "SELFTEST FAIL" in output
    assert "missing: %s" % missing in output


def test_adding_an_unexpected_chain_entry_in_memory_fails_and_names_it(
        monkeypatch, capsys):
    module = _load_regen()
    bogus = (
        "BOGUS_GENERATOR",
        ["tools/bogus_generator.py"],
        "fixture-only unexpected member",
    )
    expanded = [*module.CHAIN, bogus]
    assert len(expanded) == 11
    monkeypatch.setattr(module, "CHAIN", expanded)

    rc, output = _run_selftest(module, monkeypatch, capsys)
    assert rc == 1, output
    assert "SELFTEST FAIL" in output
    assert "unexpected: BOGUS_GENERATOR" in output


def test_chain_derived_expected_set_cannot_authorize_an_omission(
        monkeypatch, capsys):
    """Named bd-mutate catcher for the historical fail-open implementation."""
    module = _load_regen()
    shortened = [entry for entry in module.CHAIN if entry[0] != "STATIC_KB"]
    assert len(module.CHAIN) == 10 and len(shortened) == 9
    monkeypatch.setattr(module, "CHAIN", shortened)

    rc, output = _run_selftest(module, monkeypatch, capsys)
    assert rc == 1, output
    assert "missing: STATIC_KB" in output


def test_transform_control_runs_only_the_unmodified_chain(monkeypatch, capsys):
    """A valid denominator transform is not caught by import or execution."""
    module = _load_regen()
    assert len(module.CHAIN) == 10
    rc, output = _run_selftest(module, monkeypatch, capsys)
    assert rc == 0, output
    assert "SELFTEST PASS" in output
