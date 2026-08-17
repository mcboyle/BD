"""The canonical real-pytest sweep uses the measured fixed performance knee.

The post-Cut-2 qualification compared complete 16,640-test populations under
the sanctioned command on two compatible hosts.  ``-n 34`` did not improve on
``-n 24``; a machine-derived default would silently change the experiment when
host capacity changes.  The canonical default is therefore exactly 24, while
an explicit four-worker comparison oracle remains available.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path
import subprocess


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "toolchain/bin/bd-sweep-run"


def _load_tool():
    loader = importlib.machinery.SourceFileLoader("bd_sweep_run_1162", str(TOOL))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_default_worker_choice_is_fixed_at_the_measured_performance_knee():
    tool = _load_tool()

    low_count, low_provenance = tool.derive_workers(40)
    high_count, high_provenance = tool.derive_workers(86)

    assert low_count == high_count == 24
    assert "fixed" in low_provenance.lower()
    assert "fixed" in high_provenance.lower()
    assert "nproc=40" in low_provenance
    assert "nproc=86" in high_provenance


def test_explicit_four_worker_comparison_oracle_remains_available():
    tool = _load_tool()

    assert tool.derive_workers(40, explicit=4) == (4, "explicit --workers 4")
    command = tool.suite_command(4)
    assert command[:3] == ["env", "-u", "BD_INSTALL_DIR"]
    assert command[command.index("-n") + 1] == "4"


def test_contract_and_executable_bind_the_same_exact_fixed_command():
    tool = _load_tool()

    contract = tool.contract_tokens_from_claude_md(str(ROOT / "CLAUDE.md"))
    expected = tool.section5_tokens(24)

    assert contract is not None
    assert contract == expected
    assert expected[expected.index("-n") + 1] == "24"


def test_cli_offers_a_fixed_default_and_no_dynamic_fraction_policy():
    result = subprocess.run(
        [str(TOOL), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "--workers-frac" not in result.stdout
    assert "fixed canonical default: 24" in result.stdout


def test_selftest_drives_the_fixed_default_through_the_real_run_path():
    result = subprocess.run(
        [str(TOOL), "--repo", str(ROOT), "--selftest"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )

    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined[-2000:]
    assert "SELFTEST PASS: 160 of 160 checks" in combined
    assert "fixed-worker selection + provenance" in combined
