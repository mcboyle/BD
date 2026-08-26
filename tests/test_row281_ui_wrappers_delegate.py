"""Row 281: five original UI gate wrappers prove their delegation.

The all-frontend CI job is valuable independent coverage, but it cannot prove
that one of these Python gate nodes still invokes the focused Vitest contract
it advertises.  Exercise each wrapper with a recording delegate, then remove
the delegate's execution receipt and require that exact wrapper to fail.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class WrapperCase:
    module: str
    gate: str
    spec: str
    expected_tests: int


# Independent, exact population from the five-wrapper conversion at v3.66.1218.
# Later T-series gates have their own named mutation controls and are not row
# 281's subject.  Do not derive this set from imports/calls in the wrappers
# under test: deleting a wrapper's delegate must not delete it from this gate.
WRAPPERS = (
    WrapperCase(
        "tests.test_t1_dashboard_wired",
        "test_t1_dashboard_runtime_contract",
        "src/routes/Dashboard.wired.test.tsx",
        5,
    ),
    WrapperCase(
        "tests.test_t9a_live_stream_wired",
        "test_t9a_live_stream_runtime_contract",
        "src/routes/LiveStream.wired.test.tsx",
        5,
    ),
    WrapperCase(
        "tests.test_t9b_push_wired",
        "test_t9b_push_runtime_contract",
        "src/routes/Push.wired.test.tsx",
        6,
    ),
    WrapperCase(
        "tests.test_t10_devtools_wired",
        "test_t10_devtools_runtime_contract",
        "src/routes/DevTools.wired.test.tsx",
        7,
    ),
    WrapperCase(
        "tests.test_t11_approval_wired",
        "test_approval_caller_runtime_contract",
        "src/routes/ApprovalGate.wired.test.tsx",
        3,
    ),
)


def _receipt(case: WrapperCase) -> dict[str, int | str]:
    return {
        "spec": case.spec,
        "files_passed": 1,
        "files_collected": 1,
        "tests_passed": case.expected_tests,
        "tests_collected": case.expected_tests,
    }


def _isolate_non_delegate_work(monkeypatch, module) -> None:
    """Keep this gate focused on the wrapper-to-Vitest seam.

    T1 also checks the independently expensive Vite manifest.  Supply the
    exact healthy manifest precondition so a delegation verdict cannot be
    manufactured by an unrelated build failure.
    """
    if hasattr(module, "build_manifest"):
        monkeypatch.setattr(
            module,
            "build_manifest",
            lambda: {
                "src/routes/Dashboard.tsx": {"isDynamicEntry": True},
            },
        )


def test_row_281_wrapper_population_is_exact_nonzero_and_present():
    assert len(WRAPPERS) == 5, "row 281 must judge exactly five UI wrappers"
    assert len({case.module for case in WRAPPERS}) == len(WRAPPERS)
    assert sum(case.expected_tests for case in WRAPPERS) == 26
    for case in WRAPPERS:
        rel = Path(*case.module.split(".")).with_suffix(".py")
        assert (ROOT / rel).is_file(), f"row 281 wrapper is unavailable: {rel}"
        assert (ROOT / "frontend" / case.spec).is_file(), (
            f"row 281 delegated spec is unavailable: {case.spec}"
        )


@pytest.mark.parametrize("case", WRAPPERS, ids=lambda case: case.module.rsplit(".", 1)[-1])
def test_each_ui_wrapper_requires_its_exact_delegation_receipt(monkeypatch, case):
    module = importlib.import_module(case.module)
    gate = getattr(module, case.gate)
    _isolate_non_delegate_work(monkeypatch, module)

    completed_calls = []

    def completed_delegate(spec, *, expected_tests):
        completed_calls.append((spec, expected_tests))
        return _receipt(case)

    monkeypatch.setattr(module, "run_vitest", completed_delegate)
    gate()
    assert completed_calls == [(case.spec, case.expected_tests)], (
        f"{case.gate} did not invoke its delegate exactly once"
    )
    assert _receipt(case)["tests_passed"] > 0

    removed_calls = []

    def removed_delegate(spec, *, expected_tests):
        removed_calls.append((spec, expected_tests))
        return None

    monkeypatch.setattr(module, "run_vitest", removed_delegate)
    with pytest.raises(
        AssertionError,
        match=r"Vitest delegation evidence missing or mismatched",
    ):
        gate()
    assert removed_calls == [(case.spec, case.expected_tests)], (
        f"{case.gate} negative control did not reach the removed delegate once"
    )


def test_ui_wrapper_transform_control_imports_without_judging_delegation():
    """Mutation transform control: imports are deliberately not behaviour proof."""
    imported = [importlib.import_module(case.module) for case in WRAPPERS]
    assert len(imported) == 5
