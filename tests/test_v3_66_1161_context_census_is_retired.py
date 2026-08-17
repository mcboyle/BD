"""The transcript-census subsystem is retired, not replaced.

Its estimate could not observe the provider's complete context and the tool read
provider transcript files without a product consumer.  Git history preserves
the implementation and its v1140 measurements; the live tree must preserve the
context-economy lessons without keeping an executable or a second census.
"""

from __future__ import annotations

from pathlib import Path

from tracked_source import tracked_source_files


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
RETIRED = (
    "toolchain/bin/bd-context-census",
    "tests/test_v3_66_1140_context_economy_tools.py",
)

# The historical release entry and this positive retirement contract must name
# the old subject.  No other live tracked source has a reason to do so.
NAME_EXEMPT = {
    "CHANGELOG.md",
    "tests/test_v3_66_1161_context_census_is_retired.py",
}


def test_the_context_census_files_are_physically_absent():
    present = [rel for rel in RETIRED if (ROOT / rel).exists()]
    assert not present, "retired context-census files returned: " + ", ".join(present)


def test_no_live_tracked_source_still_names_the_retired_command():
    entries = tracked_source_files(ROOT)
    assert len(entries) > 100, (
        f"tracked-source denominator collapsed to {len(entries)} entries"
    )

    offenders: list[str] = []
    inspected = 0
    for rel, _kind in entries:
        if rel in NAME_EXEMPT:
            continue
        path = ROOT / rel
        if not path.is_file():
            continue
        inspected += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        if "bd-context-census" in text:
            offenders.append(rel)

    assert inspected > 100, f"only {inspected} tracked source files were readable"
    assert not offenders, "live references to the retired command: " + ", ".join(offenders)


def test_the_agent_contract_keeps_the_lessons_without_invoking_the_tool():
    contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "bd-context-census" not in contract
    for lesson in (
        "CAPTURE WHOLE TO DISK, READ A SLICE",
        "A SECOND HAND-ROLLED HEREDOC IS A MISSING `bd-*` TOOL",
        "And measure before optimising",
    ):
        assert lesson in contract, f"context-economy lesson disappeared: {lesson}"
