"""The improvement backlog is the one live task register.

Historical checklists belong in Git history.  A second register, an executable
session queue, or a directory literally named ``pending-specs`` makes open work
ambiguous even when every individual file parses correctly.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
BACKLOG = ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
RETIRED = (
    "project-knowledge/SESSION_CARRY.md",
    "tests/test_register_promises_resolve.py",
    "toolchain/bin/bd-pending",
    "toolchain/bin/bd-session",
)
CURRENT_SURFACES = (
    "CLAUDE.md",
    "toolchain/bin",
    "project-knowledge/README.md",
    "project-knowledge/README_KB.md",
    "project-knowledge/SANDBOX.md",
    "project-knowledge/BD_TOOLCHAIN_REFERENCE.md",
    "project-knowledge/BD_TOOLCHAIN_WHEN_TO_USE.md",
    "tests/test_stale_locks_check_is_gone.py",
    "tests/test_zip_era_tools_stay_retired.py",
)

_ROW = re.compile(
    r"^\|\s*(?P<id>\d+)\s*\|\s*(?P<status>[A-Z]+)(?P<evidence>[^|]*)\|\s*(?P<text>.+?)\s*\|\s*$"
)
_META = re.compile(
    r"^<!-- canonical-task-register schema=1 rows=(\d+) open=(\d+) "
    r"ids-sha256=([0-9a-f]{64}) -->$",
    re.MULTILINE,
)


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=check, capture_output=True
    )


def _tracked() -> list[str]:
    result = _git("ls-files", "-z")
    return [p.decode("utf-8", "surrogateescape") for p in result.stdout.split(b"\0") if p]


def _rows() -> list[tuple[int, str, str]]:
    rows = []
    for line in BACKLOG.read_text(encoding="ascii").splitlines():
        if match := _ROW.match(line):
            rows.append((int(match.group("id")), match.group("status"), match.group("text")))
    return rows


def test_only_the_canonical_backlog_remains_a_live_task_register():
    tracked = _tracked()
    assert len(tracked) > 1000, f"tracked denominator collapsed: {len(tracked)}"
    present = [rel for rel in RETIRED if rel in tracked or os.path.lexists(ROOT / rel)]
    assert not present, f"retired competing task surfaces returned: {present}"

    pending = [rel for rel in tracked if rel.startswith("project-knowledge/pending-specs/")]
    assert not pending, f"pending-spec task sources remain outside the backlog: {pending}"


def test_current_operator_and_tool_surfaces_point_only_at_the_backlog():
    tracked = set(_tracked())
    offenders = []
    for rel in CURRENT_SURFACES:
        path = ROOT / rel
        paths = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
        for candidate in paths:
            if str(candidate.relative_to(ROOT)) not in tracked:
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore")
            if "SESSION_CARRY" in text or "bd-pending" in text or "bd-session" in text:
                offenders.append(str(candidate.relative_to(ROOT)))
    assert not offenders, f"current surfaces still route task work to retired authorities: {offenders}"


def test_the_backlog_publishes_and_matches_its_exact_denominator():
    rows = _rows()
    assert rows, "canonical backlog parser examined zero rows"
    ids = [row[0] for row in rows]
    assert len(ids) == len(set(ids)), "duplicate canonical backlog ids"
    open_count = sum(status == "OPEN" for _, status, _ in rows)
    digest = hashlib.sha256(",".join(map(str, ids)).encode("ascii")).hexdigest()
    match = _META.search(BACKLOG.read_text(encoding="ascii"))
    assert match, "canonical backlog has no machine-visible exact denominator"
    assert (int(match.group(1)), int(match.group(2)), match.group(3)) == (
        len(rows), open_count, digest
    )


def test_pending_spec_obligations_have_exact_lifecycle_owners():
    rows = _rows()
    expected = {"S5-RESIDUE": "OPEN", "NESTED-PART": "CLOSED"}
    for token, wanted_status in expected.items():
        matches = [
            (status, text)
            for _, status, text in rows
            if text.startswith(token + " --")
        ]
        assert len(matches) == 1, f"{token} must resolve to one canonical row: {matches}"
        assert matches[0][0] == wanted_status, (token, matches)
    assert "| 162 | CLOSED @1178 | NESTED-PART --" in BACKLOG.read_text(
        encoding="ascii"
    )
    recon = [(status, text) for _, status, text in rows if text.startswith("RECON-7 ")]
    assert len(recon) == 1 and recon[0][0] == "CLOSED", recon

    by_id = {row_id: status for row_id, status, _ in rows}
    for row_id in range(119, 129):
        assert row_id in by_id, f"migrated SESSION item 31 subrow {row_id} disappeared"


def test_retired_names_fail_closed_in_an_adversarial_tracked_fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    for rel in RETIRED:
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to("missing-retired-subject")
    pending = repo / "project-knowledge/pending-specs/open.md"
    pending.parent.mkdir(parents=True, exist_ok=True)
    pending.write_text("still open\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    listed = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], check=True, capture_output=True
    ).stdout.split(b"\0")
    tracked = {p.decode() for p in listed if p}
    assert len(tracked) == len(RETIRED) + 1
    assert set(RETIRED) <= tracked
    assert all(os.path.lexists(repo / rel) and not (repo / rel).exists() for rel in RETIRED)
    assert "project-knowledge/pending-specs/open.md" in tracked
