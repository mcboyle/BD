"""Cut 5 leaves one agent contract without losing the live deploy runbook.

The three retired documents are distinct failure shapes: two explicitly tell a
fresh session what to do, and one is a pasteable standing agent prompt.  Merely
removing their links would leave the competing authorities packaged and ready
to be rediscovered.  Conversely, deleting them before moving the four live
post-checkout requirements would make the repository simpler by losing an
operator safety contract.  This suite keeps both denominators visible.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parent.parent
RETIRED = (
    "project-knowledge/PROJECT_OPERATING_INSTRUCTIONS.md",
    "project-knowledge/NEXT_SESSION_BOOTSTRAP.md",
    "docs/repo/CLAUDE_CODE_PROMPT.md",
)


def _tracked(root: Path = REPO) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
    )
    paths = {p.decode() for p in proc.stdout.split(b"\0") if p}
    minimum = 1000 if root == REPO else 1
    assert len(paths) > minimum, "tracked-path denominator is unexpectedly empty"
    return paths


def _physical_offenders(root: Path, tracked: set[str]) -> list[str]:
    return [
        rel for rel in RETIRED
        if rel in tracked or os.path.lexists(root / rel)
    ]


def _reference_offenders(root: Path) -> dict[str, list[str]]:
    allowed = {"CHANGELOG.md", Path(__file__).relative_to(REPO).as_posix()}
    offenders: dict[str, list[str]] = {}
    for rel in RETIRED:
        hits = set()
        for token in (Path(rel).name, Path(rel).stem):
            proc = subprocess.run(
                ["git", "-C", str(root), "grep", "-I", "-l", "-F", token],
                capture_output=True,
                text=True,
            )
            assert proc.returncode in (0, 1), proc.stderr
            hits.update(p for p in proc.stdout.splitlines() if p not in allowed)
        if hits:
            offenders[rel] = sorted(hits)
    return offenders


_SECOND_CONTRACT_MARKERS = (
    "# BulkDownloader — Project operating instructions",
    "# Next-session bootstrap",
    "# Claude Code — BulkDownloader session prompt",
    "paste it at the top of every session",
    "read this first in a fresh conversation",
)


def _renamed_contracts(root: Path, tracked: set[str]) -> list[str]:
    """Find the retired contracts even when their pathname was changed."""
    offenders = []
    for rel in sorted(tracked):
        if not rel.endswith(".md") or rel in {"CLAUDE.md", "CHANGELOG.md"}:
            continue
        if rel.startswith(("docs/archive/", "tests/")):
            continue
        path = root / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        lowered = text.casefold()
        if any(marker.casefold() in lowered for marker in _SECOND_CONTRACT_MARKERS):
            offenders.append(rel)
    return offenders


def test_second_agent_contracts_are_physically_retired():
    tracked = _tracked()
    assert not _physical_offenders(REPO, tracked), (
        "retired agent contract still exists, including as a dangling symlink"
    )


def test_no_live_tracked_text_routes_a_reader_to_a_retired_contract():
    offenders = _reference_offenders(REPO)
    assert not offenders, f"live tracked readers still route to retired contracts: {offenders}"


def test_no_retired_contract_was_renamed_into_a_replacement():
    offenders = _renamed_contracts(REPO, _tracked())
    assert not offenders, f"renamed second agent contracts remain active: {offenders}"


def test_adversarial_repository_exposes_symlink_reference_and_rename(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "README.md").write_text(
        "Read project-knowledge/PROJECT_OPERATING_INSTRUCTIONS.md first.\n",
        encoding="utf-8",
    )
    renamed = root / "docs" / "AGENT_START_HERE.md"
    renamed.parent.mkdir()
    renamed.write_text(
        "# Claude Code — BulkDownloader session prompt\n\nStanding instructions.\n",
        encoding="utf-8",
    )
    dangling = root / RETIRED[1]
    dangling.parent.mkdir(parents=True)
    dangling.symlink_to("missing-contract")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    tracked = _tracked(root)
    assert len(tracked) == 3
    assert RETIRED[1] in _physical_offenders(root, tracked)
    assert _reference_offenders(root)[RETIRED[0]] == ["README.md"]
    assert _renamed_contracts(root, tracked) == ["docs/AGENT_START_HERE.md"]


def test_the_retirement_denominator_is_exact():
    assert len(RETIRED) == 3
    assert len(set(RETIRED)) == 3
    assert (REPO / "CLAUDE.md").is_file(), "the sole surviving agent contract is absent"
