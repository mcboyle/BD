"""Batching safety: the three defects that make batch-cap > 1 unsafe.

RED-first against the harness as it stood on 2026-08-30.  Each test names one
mechanism, asserts its precondition explicitly, and carries a negative control
so a vacuous pass is impossible.

The subjects are the LIVE harness scripts in $HOME, not the bd-persist copies:
those copies are a backup, and a gate that reads the backup does not judge the
thing that runs.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOME = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home())))
DRAIN = HOME / "bd-drain.sh"
NIGHT = HOME / "bd-night.sh"
ROW_AUDIT = HOME / "bd-row-audit.py"


def _read(p: Path) -> str:
    assert p.is_file(), f"precondition: {p} must exist to be judged"
    text = p.read_text(encoding="utf-8")
    assert text.strip(), f"precondition: {p} is empty"
    return text


# ── D1: a multi-row batch must reach bd-row-audit as separate arguments ──────

def test_row_audit_treats_a_joined_batch_as_one_unknown_row():
    """The mechanism itself, proved directly rather than assumed.

    bd-row-audit reads row ids from separate argv entries.  A quoted batch
    string is therefore ONE row id that names no worktree -- UNKNOWN, which is
    a failing state, so bd-drain skips the whole batch.
    """
    joined = subprocess.run(
        [sys.executable, str(ROW_AUDIT), "399 402"],
        capture_output=True, text=True)
    assert "audited 1 row(s)" in joined.stdout, joined.stdout
    assert "1 UNKNOWN" in joined.stdout, joined.stdout

    # NEGATIVE CONTROL: the same two rows as separate args are measurable.
    split = subprocess.run(
        [sys.executable, str(ROW_AUDIT), "399", "402"],
        capture_output=True, text=True)
    assert "audited 2 row(s)" in split.stdout, split.stdout
    assert "0 UNKNOWN" in split.stdout, split.stdout


def test_drain_expands_a_batch_into_separate_audit_arguments():
    """bd-drain must not quote a whole batch into one argv slot."""
    text = _read(DRAIN)
    calls = re.findall(r"^\s*if\s+!\s+python3\s+\S*bd-row-audit\.py\s+(\S+)",
                       text, re.M)
    assert len(calls) == 1, f"expected exactly one audit call site, found {calls}"
    arg = calls[0]
    assert arg != '"$ROW"', (
        'bd-drain passes the whole batch as one quoted argument, so a '
        'multi-row batch audits as a single nonexistent row and the entire '
        'batch is skipped as UNKNOWN')
    assert arg in ('$ROW', '${ROW_ARGS[@]}', '"${ROW_ARGS[@]}"'), arg


# ── D2: the merge classifier must check every row in a batch ────────────────

def test_night_empty_batch_slug_is_not_a_wildcard_match():
    """An empty cut slug must not read as a match against everything.

    ``grep -q -- ""`` succeeds against any nonempty input.  The batch slug is
    derived from the batch's first row -- which is correct, a batched cut
    carries one slug -- but when that row was absent from ``todo`` the lookup
    produced an EMPTY pattern, every batch scored as MERGED, the width-failure
    streak reset on each pass, and the ladder could never demote.
    """
    text = _read(NIGHT)
    assert "_bsl=" in text, "precondition: the batch-slug lookup still exists"
    assert re.search(r'\[ -n "\$_bsl" \][^\n]*grep -q -- "\$_bsl"', text), (
        "the slug match must be guarded by a nonempty test")


def test_night_batch_merge_requires_all_rows_closed():
    """The register check must cover the whole batch, not one row."""
    text = _read(NIGHT)
    single_row_register = re.findall(
        r'grep -qE "\^\\\| \$\{_b%% \*\} \\\|', text)
    assert not single_row_register, (
        "the register CLOSED check still tests only the batch's first row: "
        f"{single_row_register}")


# ── D3: C6 must measure against the merge base, not main's moving tip ───────

def test_row_audit_c6_measures_against_the_merge_base():
    """Every diff in bd-row-audit measures against the merge base.

    The file list at the top of ``audit`` was corrected to ``mb``; the C6
    regenerated-artifact check was left comparing against ``origin/main``.
    Once main advances, C6 compares a candidate's artifact against a tree the
    candidate was never based on and can refuse a correct row.
    """
    text = _read(ROW_AUDIT)
    assert 'mb = sh(' in text, "precondition: the merge base is still computed"
    c6 = text.split("# C6 --", 1)
    assert len(c6) == 2, "precondition: the C6 block is still present"
    block = c6[1].split("return verdict", 1)[0]
    assert '"origin/main"' not in block, (
        "C6 still diffs against origin/main rather than the merge base")
    assert "mb" in block, "C6 must diff against the merge base"
