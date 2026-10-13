"""bh1-41: bd-persist/verdicts/ is the LEGACY verdict store; <cut>/.review/VERDICT-*.md is canonical.

PM ruling (harness-work/O1789/unpark/bh1-41/RULING-pm-A3-A.md, FLEET_RULE 35): no file is moved or
deleted. bd-pm-census.py's DRIFT column counts the legacy entries that have NO .review twin (the
old symmetric difference also counted every ordinary .review verdict that was never mirrored), and
the five tools that read or write bd-persist/verdicts/ say so on stderr, WARN-only: removing the
tagged lines must give back the base file byte for byte, so no exit code or stdout can move.

The candidates are harness files outside this tree; BD_BH1_41_CANDIDATE names the candidate
directory (harness-work/FIX/<slug>/, with the pre-edit copies in its base/ subdirectory).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH1_41_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

TAG = "LEGACY-VERDICT-STORE"
WARN_TOOLS = {
    "bd-boardgate.sh": 2,
    "bd-boardgate-PRECITE.sh": 1,
    "bd-cut-seats.sh": 1,
    "bd-review-prep.sh": 1,
    "bd-ellm-canary.py": 1,
}
CENSUS = "bd-pm-census.py"
# A tagged shell line may only print to stderr; a tagged Python line may only write to stderr.
_SH_WARN = re.compile(
    r'^\s*(?:case "\$\w+" in /home/mboyle/bd-persist/verdicts/\*\) )?echo "WARN '
    + TAG
    + r'[^`]*" >&2(?: ;; esac)?  # '
    + TAG
    + r"$"
)
_PY_WARN = re.compile(
    r'^\s*(?:import sys; sys|__import__\("sys"\))\.stderr\.write\("WARN '
    + TAG
    + r'[^"]*"(?: % \(?[^#]*\)?)?\)  # '
    + TAG
    + r"$"
)
_FORBIDDEN = re.compile(r"\b(?:return|exit|continue|break|rm|mv|cp)\b")


def _dir() -> Path:
    path = Path(CANDIDATE)
    assert path.is_dir(), f"BD_BH1_41_CANDIDATE={CANDIDATE!r} is not a directory"
    return path


def test_candidate_directory_holds_every_tool_and_its_recorded_base() -> None:
    root = _dir()
    recorded = {}
    for line in (root / "BASE-SHA256.txt").read_text().splitlines():
        digest, name = line.split()
        recorded[name] = digest
    assert set(recorded) == set(WARN_TOOLS) | {CENSUS}, sorted(recorded)
    for name, digest in recorded.items():
        assert (root / name).is_file(), f"candidate {name} missing"
        base = (root / "base" / name).read_bytes()
        assert hashlib.sha256(base).hexdigest() == digest, (
            f"base/{name} does not match BASE-SHA256.txt"
        )


@pytest.mark.parametrize("name", sorted(WARN_TOOLS))
def test_warn_lines_are_the_whole_change_and_only_print(name: str) -> None:
    root = _dir()
    cand = (root / name).read_text().splitlines(keepends=True)
    base = (root / "base" / name).read_text().splitlines(keepends=True)
    tagged = [line for line in cand if TAG in line]
    assert len(tagged) == WARN_TOOLS[name], (
        f"{name}: {len(tagged)} WARN line(s), want {WARN_TOOLS[name]}: the legacy-store use is not flagged"
    )
    assert [line for line in cand if TAG not in line] == base, (
        f"{name}: the candidate differs from base/ beyond its {TAG} lines -- not WARN-only"
    )
    shape = _PY_WARN if name.endswith(".py") else _SH_WARN
    for line in tagged:
        body = line.rstrip("\n")
        assert shape.match(body), (
            f"{name}: tagged line is not a pure stderr WARN: {body[:160]}"
        )
        assert not _FORBIDDEN.search(body.split("WARN " + TAG, 1)[0]), (
            f"{name}: control flow in a WARN line"
        )


@pytest.mark.parametrize("name", sorted(WARN_TOOLS) + [CENSUS])
def test_candidate_parses(name: str) -> None:
    path = _dir() / name
    if name.endswith(".py"):
        compile(path.read_text(), str(path), "exec")
    else:
        r = subprocess.run(
            ["bash", "-n", str(path)], capture_output=True, text=True, timeout=30
        )
        assert r.returncode == 0, r.stderr


def _census(tmp_path: Path, local: list[str], legacy: list[str]) -> tuple[str, str]:
    cut = "fixture-bh141"
    cuts = tmp_path / "cuts"
    review = cuts / cut / ".review"
    review.mkdir(parents=True)
    (cuts / cut / "DONE.md").write_text("VERDICT: PATCH\nBASE: 0000000\n")
    body = "VERDICT: BOARD\nSEAT: bd-review-correctness-X1-A\nTREE: 0123456789abcdef\n"
    for seat in local:
        (review / f"VERDICT-correctness-{seat}.md").write_text(body)
    legacy_dir = tmp_path / "verdicts"
    legacy_dir.mkdir()
    for seat in legacy:
        (legacy_dir / f"{cut}-VERDICT-correctness-{seat}.md").write_text(body)
    env = dict(
        os.environ, BD_PM_CENSUS_CUTS=str(cuts), BD_PM_CENSUS_VERDICTS=str(legacy_dir)
    )
    r = subprocess.run(
        [sys.executable, str(_dir() / CENSUS)],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )
    if r.returncode == 2 and "COULD NOT LOOK" in r.stderr:
        pytest.fail(
            f"census could not read the register on this host: {r.stderr.strip()}"
        )
    assert r.returncode == 0, r.stderr
    rows = [line for line in r.stdout.splitlines() if line.startswith(cut)]
    assert len(rows) == 1, f"fixture cut not censused: {r.stdout!r}"
    return rows[0], r.stderr


def test_census_drift_counts_legacy_entries_with_no_review_twin(tmp_path: Path) -> None:
    # A: in both stores. B: .review only (ordinary). C: legacy only (the drift).
    row, err = _census(tmp_path, local=["A", "B"], legacy=["A", "C"])
    drift = row.split()[-2]
    assert drift == "1", (
        f"DRIFT {drift}, want 1 (legacy C only; .review-only B is not drift): {row}"
    )
    assert "WARN " + TAG in err and "1 legacy verdict(s) with no .review twin" in err, (
        err
    )


def test_census_negative_control_no_legacy_orphans_no_drift_no_warn(
    tmp_path: Path,
) -> None:
    row, err = _census(tmp_path, local=["A", "B"], legacy=["A"])
    assert row.split()[-2] == "0", (
        f"DRIFT must be 0 when every legacy entry has a .review twin: {row}"
    )
    assert TAG not in err, f"WARN without drift: {err}"
