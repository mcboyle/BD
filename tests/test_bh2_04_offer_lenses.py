from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_04_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _get_offer_lenses_program(candidate_path: Path) -> str:
    if candidate_path.is_dir():
        script_file = candidate_path / "bd-offer-sweep.sh"
    else:
        script_file = candidate_path
    assert script_file.is_file(), f"candidate script not found: {script_file}"
    content = script_file.read_text(encoding="utf-8")
    match = re.search(
        r"^offer_lenses\(\)\s*\{.*?^\}", content, re.MULTILINE | re.DOTALL
    )
    assert match, "offer_lenses function definition not found in candidate"
    fn_code = match.group(0)
    return f"""{fn_code}
BD_OFFER_LENSES="$1" offer_lenses
"""


def _run_offer_lenses(seats: str) -> list[str]:
    program = _get_offer_lenses_program(Path(CANDIDATE))
    res = subprocess.run(
        ["bash", "-c", program, "offer-probe", seats],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"},
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert res.returncode == 0 or (res.returncode == 1 and not res.stdout.strip())
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    return lines


def test_active_correctness_seats_accepted() -> None:
    seats = "correctness-A2-A correctness-B3-B codex-r127c"
    matched = _run_offer_lenses(seats)
    assert matched == ["codex-r127c", "correctness-A2-A", "correctness-B3-B"]


def test_legacy_correctness_seats_accepted() -> None:
    seats = "bd-review-correctness-A1-A bd-cx-rc-worker1"
    matched = _run_offer_lenses(seats)
    assert matched == ["bd-cx-rc-worker1", "bd-review-correctness-A1-A"]


def test_non_correctness_seats_rejected() -> None:
    seats = "bd-worker-A1-A bd-pm-C-B shape-B2-B bd-review-shape-A1-A bd-integrator-A"
    matched = _run_offer_lenses(seats)
    assert matched == []


def test_non_correctness_codex_seats_rejected() -> None:
    seats = "codex-shape-1 codex-worker-1 codex-worker-2 codex-audit-1"
    matched = _run_offer_lenses(seats)
    assert matched == []


def test_mixed_seats_filtered_and_sorted() -> None:
    seats = (
        "bd-worker-A1-A "
        "codex-shape-1 "
        "correctness-B3-B "
        "shape-B2-B "
        "codex-worker-1 "
        "codex-r127c "
        "bd-pm-C-B "
        "bd-review-correctness-legacy "
        "correctness-A2-A"
    )
    matched = _run_offer_lenses(seats)
    assert matched == [
        "bd-review-correctness-legacy",
        "codex-r127c",
        "correctness-A2-A",
        "correctness-B3-B",
    ]


def test_deduplication() -> None:
    seats = "correctness-A2-A correctness-A2-A codex-r127c codex-r127c"
    matched = _run_offer_lenses(seats)
    assert matched == ["codex-r127c", "correctness-A2-A"]
