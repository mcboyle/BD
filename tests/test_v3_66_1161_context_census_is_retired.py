"""The transcript-census subsystem is retired, not replaced.

Its estimate could not observe the provider's complete context and the tool read
provider transcript files without a product consumer.  Git history preserves
the implementation and its v1140 measurements; the live tree must preserve the
context-economy lessons without keeping an executable or a second census.

This is necessarily a tracked-source floor.  It normalizes successor filenames
and folds constant Python strings, including relative paths, but cannot see a
name computed from variables, ``chr()`` arithmetic, runtime input, or shell
variable expansion.
"""

from __future__ import annotations

import os
from pathlib import Path
import posixpath
import re
import subprocess

from python_source import assembled_strings, contains_assembled
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


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=check,
        capture_output=True,
    )


def _tracked_paths(root: Path) -> list[str]:
    result = _git(root, "ls-files", "-z")
    return [item.decode("utf-8", "surrogateescape") for item in result.stdout.split(b"\0") if item]


def _live_name_references(root: Path) -> list[str]:
    result = _git(
        root,
        "grep",
        "-I",
        "-l",
        "-F",
        "bd-context-census",
        "--",
        ".",
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr.decode("utf-8", "replace")
    references = {
        line
        for line in result.stdout.decode("utf-8", "surrogateescape").splitlines()
        if line
    }
    sources = tracked_source_files(root)
    assert sources, "tracked-source denominator collapsed"
    for rel, kind in sources:
        if kind != "python":
            continue
        path = root / rel
        if contains_assembled(path, RETIRED[0]):
            references.add(rel)
            continue
        parent = posixpath.dirname(rel)
        for value in assembled_strings(path):
            resolved = posixpath.normpath(posixpath.join(parent, value))
            if resolved == RETIRED[0]:
                references.add(rel)
                break
    return sorted(references)


def _successor_census_tools(paths: list[str]) -> list[str]:
    successors = []
    for rel in paths:
        normalized = rel.replace("\\", "/").casefold()
        root = normalized.split("/", 1)[0]
        if root not in {"bulk_downloader", "scripts", "toolchain", "tools"}:
            continue
        name = re.sub(r"[^a-z0-9]+", "", posixpath.basename(normalized))
        if "census" in name and ("context" in name or "transcript" in name):
            successors.append(rel)
    return successors


def test_the_context_census_files_are_physically_absent():
    tracked = _tracked_paths(ROOT)
    assert len(tracked) > 1000, f"tracked-path denominator collapsed to {len(tracked)}"
    present = [
        rel for rel in RETIRED
        if rel in tracked or os.path.lexists(ROOT / rel)
    ]
    assert not present, "retired context-census files returned: " + ", ".join(present)


def test_no_live_tracked_text_still_names_the_retired_command():
    paths = _tracked_paths(ROOT)
    assert len(paths) > 1000, f"tracked-path denominator collapsed to {len(paths)}"
    offenders = sorted(set(_live_name_references(ROOT)) - NAME_EXEMPT)
    assert not offenders, "live references to the retired command: " + ", ".join(offenders)


def test_no_context_or_transcript_census_successor_was_created():
    paths = _tracked_paths(ROOT)
    assert len(paths) > 1000, f"tracked-path denominator collapsed to {len(paths)}"
    successors = _successor_census_tools(paths)
    assert not successors, "replacement census tools are forbidden: " + ", ".join(successors)


def test_retirement_checks_fail_closed_on_the_three_adversarial_shapes(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README.md").write_text("run bd-context-census before every cut\n")
    retired = repo / RETIRED[0]
    retired.parent.mkdir(parents=True)
    retired.symlink_to("missing-retired-subject")
    successor = repo / "toolchain/bin/bd-transcript-census"
    successor.write_text("#!/bin/sh\n")
    _git(repo, "add", ".")

    paths = _tracked_paths(repo)
    assert len(paths) == 3, f"adversarial denominator changed: {paths}"
    assert _live_name_references(repo) == ["README.md"]
    assert RETIRED[0] in paths and os.path.lexists(retired) and not retired.exists()
    assert _successor_census_tools(paths) == [
        "toolchain/bin/bd-context-census",
        "toolchain/bin/bd-transcript-census",
    ]


def test_casefolded_tool_with_assembled_relative_retired_path_is_caught(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    successor = repo / "tools/Context_Census.py"
    successor.parent.mkdir()
    source = (
        "import os\n"
        "os.chdir(os.path.dirname(__file__))\n"
        "command = os.path.join(\n"
        "    '..', 'toolchain', '.', 'bin', '..', 'bin', "
        "'bd-context-' + 'census'\n"
        ")\n"
        "os.execv(command, [command])\n"
    )
    successor.write_text(source)
    _git(repo, "add", ".")

    paths = _tracked_paths(repo)
    assert paths == ["tools/Context_Census.py"]
    assert not paths[0].startswith("toolchain/bin/bd-")
    assert Path(paths[0]).name != Path(paths[0]).name.casefold()
    assert RETIRED[0] not in source
    assert "../toolchain/./bin/../bin/bd-context-census" in assembled_strings(successor)
    assert not contains_assembled(successor, RETIRED[0])
    assert _live_name_references(repo) == ["tools/Context_Census.py"]
    assert _successor_census_tools(paths) == ["tools/Context_Census.py"]


def test_the_agent_contract_keeps_the_lessons_without_invoking_the_tool():
    contract = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "bd-context-census" not in contract
    for lesson in (
        "CAPTURE WHOLE TO DISK, READ A SLICE",
        "A SECOND HAND-ROLLED HEREDOC IS A MISSING `bd-*` TOOL",
        "And measure before optimising",
    ):
        assert lesson in contract, f"context-economy lesson disappeared: {lesson}"
