"""Cut 10 leaves one concise agent contract and retires its temporary freezer."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_HEADINGS = (
    "## A1 | Authority and scope",
    "## A2 | Authorization and state",
    "## A3 | Change lifecycle",
    "## A4 | Writer and Git safety",
    "## A5 | Verification",
    "## A6 | Release and deployment",
    "## A7 | Engineering invariants",
    "## A8 | Focused authorities and commands",
)
RETIRED = (
    "toolchain/bin/bd-contract-rules",
    "project-knowledge/CONTRACT_RULES.baseline",
    "tests/test_v3_66_1141_no_paragraph_leaves_undeclared.py",
)
FOCUSED = (
    "project-knowledge/IMPROVEMENT_BACKLOG.md",
    "project-knowledge/TOUCHED_FILE_TO_TEST.md",
    "docs/repo/ENVIRONMENT_PROVISIONING.md",
    "docs/repo/FRESH_HOST_BRINGUP.md",
    "scripts/deploy.sh",
)
SECOND_CONTRACT_MARKERS = (
    "paste it at the top of every session",
    "read this first in a fresh conversation",
    "fresh sandbox -- please bootstrap it",
    "cowork execution prompt",
    "codex execution prompt",
    "project operating instructions",
    "next-session bootstrap",
)


def _tracked_paths(root: Path = ROOT) -> set[str]:
    proc = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        check=True,
        timeout=15,
    )
    paths = {part.decode() for part in proc.stdout.split(b"\0") if part}
    assert len(paths) > (1000 if root == ROOT else 0), (
        "tracked-path denominator is unexpectedly empty"
    )
    return paths


def _retired_residue(root: Path, tracked: set[str]) -> list[str]:
    return [
        rel for rel in RETIRED
        if rel in tracked or os.path.lexists(root / rel)
    ]


def _section_bodies(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"(?m)^## (A[1-8]) \| ([^\n]+)$", text))
    ids = [match.group(1) for match in matches]
    assert len(ids) == len(set(ids)), f"duplicate final section IDs: {ids}"
    out = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out[match.group(1)] = text[match.start():end]
    return out


def _current_agent_surfaces(root: Path, tracked: set[str]) -> list[str]:
    offenders = []
    for rel in sorted(tracked):
        if not rel.endswith((".md", ".txt")) or rel in {"CLAUDE.md", "CHANGELOG.md"}:
            continue
        if rel.startswith(("docs/archive/", "tests/")):
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8").casefold()
        except (OSError, UnicodeError):
            continue
        if any(marker in text for marker in SECOND_CONTRACT_MARKERS):
            offenders.append(rel)
    return offenders


def _retired_references(root: Path, tracked: set[str]) -> dict[str, list[str]]:
    allowed = {
        "CHANGELOG.md",
        "docs/repo/DOC_HYGIENE_AUDIT_v3_66_811.md",
        Path(__file__).relative_to(ROOT).as_posix(),
    }
    offenders = {}
    for retired in RETIRED:
        tokens = {retired, Path(retired).name, Path(retired).stem}
        hits = []
        for rel in sorted(tracked - allowed):
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if any(token in text for token in tokens):
                hits.append(rel)
        if hits:
            offenders[retired] = sorted(set(hits))
    return offenders


def test_contract_has_exactly_the_eight_approved_sections():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    sections = _section_bodies(text)
    assert tuple(f"## {key} | {sections[key].splitlines()[0].split(' | ', 1)[1]}"
                 for key in sections) == EXPECTED_HEADINGS


def test_load_bearing_semantic_families_remain_in_their_owners():
    sections = _section_bodies((ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    expected = {
        "A1": ("sole agent-facing contract", "host", "commit", "tree", "measure"),
        "A2": ("hold", "wait", "UNKNOWN", "machine-visible", "authorization"),
        "A3": ("RED-first", "one coherent feature per cut", "merge", "deploy", "review"),
        "A4": ("sole writer", "git add -A", "force-with-lease", "gitleaks", "merge commits"),
        "A5": ("real pytest", "floor", "nonzero", "split", "exact SHA"),
        "A6": ("__version__", "CHANGELOG", "PIN_INDEX", "inode", "service down"),
        "A7": ("denominator", "negative control", "assert the precondition", "environment"),
        "A8": ("IMPROVEMENT_BACKLOG.md", "TOUCHED_FILE_TO_TEST.md", "toolchain/bin"),
    }
    assert set(sections) == set(expected)
    missing = {
        section: [token for token in tokens if token.casefold() not in sections[section].casefold()]
        for section, tokens in expected.items()
    }
    assert not any(missing.values()), f"mandatory semantic families missing: {missing}"


def test_exact_canonical_commands_and_environment_contract_remain():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    tokens = (
        "env -u BD_INSTALL_DIR",
        "BD_DISABLE_KEEPALIVE=1",
        "PYTHONUNBUFFERED=1",
        "venv/bin/python -m pytest tests/",
        "-n 24",
        "--dist loadfile",
        "--timeout=240",
        "--timeout-method=thread",
        "-p no:randomly",
        'venv/bin/python toolchain/bin/bd-regen-order --work "$PWD"',
    )
    missing = [token for token in tokens if token not in text]
    assert not missing, f"canonical command tokens missing: {missing}"


def test_focused_destinations_exist_and_are_linked_once_or_more():
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [rel for rel in FOCUSED if not (ROOT / rel).is_file()]
    unlinked = [rel for rel in FOCUSED if rel not in text]
    assert not missing, f"focused authority is absent: {missing}"
    assert not unlinked, f"focused authority is not linked: {unlinked}"


def test_temporary_conservation_subsystem_is_physically_retired():
    tracked = _tracked_paths()
    assert len(RETIRED) == len(set(RETIRED)) == 3
    assert not _retired_residue(ROOT, tracked), "temporary Cut 3 machinery remains"


def test_no_current_reader_invokes_temporary_conservation_subsystem():
    offenders = _retired_references(ROOT, _tracked_paths())
    assert not offenders, f"current readers still invoke retired machinery: {offenders}"


def test_claude_is_the_only_current_agent_contract():
    offenders = _current_agent_surfaces(ROOT, _tracked_paths())
    assert not offenders, f"second agent-facing contract exists: {offenders}"


def test_helpers_fail_closed_on_duplicate_missing_renamed_and_dangling(tmp_path: Path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "CLAUDE.md").write_text(
        "## A1 | Authority and scope\n\nfirst\n\n"
        "## A1 | Authority and scope\n\nduplicate\n",
        encoding="utf-8",
    )
    invocation = root / "README.md"
    invocation.write_text("Run toolchain/bin/bd-contract-rules now.\n", encoding="utf-8")
    renamed = root / "AGENT_START.txt"
    renamed.write_text("Read this first in a fresh conversation.\n", encoding="utf-8")
    dangling = root / RETIRED[1]
    dangling.parent.mkdir(parents=True)
    dangling.symlink_to("missing-baseline")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)

    tracked = _tracked_paths(root)
    assert len(tracked) == 4
    assert os.path.lexists(dangling) and not dangling.exists()
    assert "toolchain/bin/bd-contract-rules" in invocation.read_text(encoding="utf-8")
    assert "Read this first in a fresh conversation" in renamed.read_text(encoding="utf-8")
    assert RETIRED[1] in _retired_residue(root, tracked)
    assert _retired_references(root, tracked)[RETIRED[0]] == ["README.md"]
    assert _current_agent_surfaces(root, tracked) == ["AGENT_START.txt"]
    try:
        _section_bodies((root / "CLAUDE.md").read_text(encoding="utf-8"))
    except AssertionError as exc:
        assert "duplicate final section IDs" in str(exc)
    else:
        raise AssertionError("duplicate final section was accepted")
