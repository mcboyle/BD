"""ci-gate-rebalance r1 (O1789): mutation shards are non-blocking, the gates they carried stay blocking.

Before this cut the required context gate-suites (mutation-tools) ran six mutation files AND eight
ordinary gates, so taking mutation out of the required set would have un-required the eight. This
gate holds the split in place:

  * the three mutation shards hold mutation files ONLY;
  * each gate moved out of mutation-tools lands in its named BLOCKING shard (a dropped pin falls
    through to a glob shard and is reported by name, not by count);
  * ci.yml's continue-on-error list, and the step that reports a failed non-blocking shard, name
    exactly the mutation shards -- a typo there would silently make a blocking shard optional;
  * the resolved shards still partition the declared gate population (set equality, each once).

Per-shard balance is measured, not asserted here: tools/ci_shards.py GLOBS cites the runs.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import yaml

from tools import ci_shards

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"

NON_BLOCKING = {
    "mutation-tools": {
        "tests/test_row810_spec_collection_slice_0.py",
        "tests/test_row788_transform_control_is_separate.py",
        "tests/test_row797_three_login_seams_carry_durable_mutant_pins.py",
    },
    "mutation-tools-b": {
        "tests/test_row810_spec_collection_slice_1.py",
    },
    "mutation-tools-c": {
        "tests/test_row810_spec_collection_slice_2.py",
        "tests/test_v3_66_1184_mutation_specs_are_tracked.py",
        "tests/test_row355_mutate_timing_is_schedule_stable.py",
        "tests/test_row357_mutant_anchors_are_not_fragile.py",
    },
}

# The eight non-mutation gates that rode in mutation-tools, and the blocking shard each now runs in.
MOVED_TO_BLOCKING = {
    "tests/test_rowssrf_loopback_reason_is_structured.py": "safety-censuses",
    "tests/test_row470_corpus_tools_fail_closed.py": "safety-censuses",
    "tests/test_cut_quality_permits.py": "safety-censuses",
    "tests/test_row531_denominators_are_derived_not_pinned.py": "safety-censuses",
    "tests/test_row705_published_denominators.py": "safety-censuses",
    "tests/test_row779_template_sandbox_browser_mode_pins_the_vetted_address.py": "safety-censuses",
    "tests/test_row804_browser_redirect_bypasses_metadata_guard.py": "safety-censuses",
    "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py": "gates-v3-c",
}


def _gate_suites() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["gate-suites"]


def _listed_names(expression: str) -> set[str]:
    """The shard names inside the one fromJSON('[...]') literal of a workflow expression."""
    literals = re.findall(r"fromJSON\('(\[[^']*\])'\)", expression)
    assert len(literals) == 1, f"REBALANCE: expected one fromJSON list literal in {expression!r}"
    return set(json.loads(literals[0]))


def _owner(table: dict[str, list[str]], suite: str) -> list[str]:
    return [name for name, files in table.items() if suite in files]


def test_mutation_shards_hold_mutation_files_only():
    table = ci_shards.shards(ROOT)
    for shard, expected in NON_BLOCKING.items():
        assert shard in table, f"REBALANCE: non-blocking shard {shard!r} is not resolved"
        got = set(table[shard])
        assert got == expected, (
            f"REBALANCE: non-blocking shard {shard!r} must hold mutation files only; "
            f"extra={sorted(got - expected)} missing={sorted(expected - got)}")


def test_gates_moved_out_of_mutation_tools_run_in_a_blocking_shard():
    table = ci_shards.shards(ROOT)
    wrong = {suite: _owner(table, suite) for suite, shard in MOVED_TO_BLOCKING.items()
             if _owner(table, suite) != [shard]}
    assert not wrong, f"REBALANCE: moved gate(s) not in their blocking shard: {wrong}"
    assert not set(MOVED_TO_BLOCKING.values()) & set(NON_BLOCKING), (
        "REBALANCE: a moved gate's target shard is itself non-blocking")


def test_continue_on_error_names_exactly_the_mutation_shards():
    job = _gate_suites()
    expression = str(job.get("continue-on-error", ""))
    assert "matrix.name" in expression, (
        f"REBALANCE: gate-suites continue-on-error does not key on the shard name: {expression!r}")
    assert _listed_names(expression) == set(NON_BLOCKING), (
        f"REBALANCE: continue-on-error names {sorted(_listed_names(expression))}, "
        f"non-blocking shards are {sorted(NON_BLOCKING)}")
    assert set(NON_BLOCKING) < set(ci_shards.names())
    reports = [step for step in job["steps"] if "failure()" in str(step.get("if", ""))]
    assert len(reports) == 1, "REBALANCE: a failed non-blocking shard has no reporting step"
    assert _listed_names(str(reports[0]["if"])) == set(NON_BLOCKING), (
        "REBALANCE: the failure-report step and continue-on-error name different shards")
    assert "::warning" in str(reports[0].get("run", ""))


def test_resolved_shards_partition_the_declared_population():
    table = ci_shards.shards(ROOT)
    counts = Counter(f for files in table.values() for f in files)
    assert set(counts) == ci_shards.population(ROOT), "REBALANCE: shard union != declared population"
    twice = sorted(f for f, n in counts.items() if n > 1)
    assert not twice, f"REBALANCE: gate(s) in more than one shard: {twice}"
