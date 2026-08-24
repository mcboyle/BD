"""bd-deploy-manifest is retired. This keeps it that way, and says why.

The tool enumerated files an `unzip -o` overlay had to delete by hand, because
the overlay overwrote and added but never removed. That failure was real: at
v3.66.718, `app_sched_exports.py` -- deleted at 716 -- kept living on stash, and
the disk-globbing graph gates turned three suites RED against a correct release.

The box now deploys with `git fetch origin main` + `git reset --hard
origin/main` + a restart, which deletes tracked files natively. The orphan class
therefore cannot occur, and a gate that cannot encounter its subject reports
clean -- which is worse than not having the gate at all.

So the tool was removed rather than left runnable. This file exists because a
deletion with no gate is a silent-regression slot in the other direction: it is
easy for a future session to find the historical rationale, conclude the tool is
missing by accident, and re-add it. If you are here because this test failed,
read the tombstone in BD_TOOLCHAIN_REFERENCE.md before deciding.
"""
from __future__ import annotations

from pathlib import Path

from python_source import assembled_strings, contains_assembled
from tracked_source import tracked_source_files

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    "tools/deploy_manifest.py",
    "toolchain/bin/bd-deploy-manifest",
    "project-knowledge/deploy_manifest.py",
    "tests/test_v3_66_722_deploy_manifest_ships.py",
    # v3.66.917. This tuple is the gate's ENTIRE denominator for existence --
    # it globs nothing -- so a retired tool survives simply by living at a path
    # nobody listed. All four names above are paths that do not exist and never
    # did under this spelling, while the real survivor sat one directory away
    # as an extensionless, python-shebang, TRACKED file for 59 releases. The
    # gate was green the whole time, which is exactly CLAUDE.md section 0: a
    # check whose denominator cannot contain its subject reports OK.
    "project-knowledge/bd-deploy-manifest",
)

TOMBSTONE = REPO_ROOT / "project-knowledge" / "README.md"
_RETIRED_REFERENCE_NEEDLES = ("deploy_manifest", "deploy-manifest")
_NON_INVOCATION_REFERENCES = {"tests/test_deploy_manifest_stays_retired.py"}


def test_the_fail_safe_branch_is_executable():
    """The git-unavailable branch below raised NameError instead of skipping.

    `pytest.skip(...)` sits in the `proc.returncode != 0` arm of the scan
    below, and this module never imported pytest -- so the one path written to
    make the gate fail SAFE was itself broken. It is unreachable while git
    works, which is why it survived: the branch that only runs when something
    else has already gone wrong is the branch nothing exercises.

    Asserting the binding rather than driving the branch is deliberate. Forcing
    `git ls-files` to fail means monkeypatching subprocess inside the module
    under test, and a harness that fakes the failure it is checking for is the
    shape CLAUDE.md section 6 warns about. The name either resolves or it does
    not.

    NOTE, and it is the wider finding: CI runs pyflakes over `bulk_downloader`
    and `tools` only, so `tests/` is outside the denominator of the one
    instrument that reports undefined names. Measured at v3.66.917, pyflakes
    over tests/ finds exactly two real ones -- this, and RR_MOUSE_INTERACTION
    at tests/test_v3_66_50_at2_dom_capture.py:120. Both are filed; a gate over
    tests/ is its own cut, not a rider on this one.
    """
    import sys
    mod = sys.modules[__name__]
    assert getattr(mod, "pytest", None) is not None, (
        "this module calls pytest.skip() in its fail-safe branch but does not "
        "import pytest -- that branch raises NameError instead of skipping")


def test_the_retired_files_are_gone():
    present = [p for p in RETIRED if (REPO_ROOT / p).exists()]
    assert not present, (
        "these were retired 2026-07-28 but are present again:\n  "
        + "\n  ".join(present)
        + "\n\nUnder a git deploy `git reset --hard` removes deleted files "
          "itself, so there is no orphan for a manifest to enumerate. If you "
          "are restoring a zip-based deploy, recover them from history rather "
          "than rewriting -- the _NEVER_RM list came from real incidents and "
          "is easy to under-specify from memory."
    )


def test_the_retirement_is_documented_not_just_done():
    """A deletion with no explanation gets undone by the next reader.

    The historical rationale is compelling on its own terms; without the
    tombstone beside it, re-adding the tool looks like a fix.
    """
    text = TOMBSTONE.read_text(encoding="utf-8")
    assert "bd-deploy-manifest" in text and "git reset --hard" in text and "cannot\noccur" in text, (
        f"{TOMBSTONE.relative_to(REPO_ROOT)} no longer records why "
        f"bd-deploy-manifest was retired. Keep the tombstone: the reason the "
        f"tool existed is still true of the OLD deploy model, so a reader who "
        f"finds only the rationale will restore it."
    )


def test_nothing_still_calls_the_retired_tool():
    """A live invocation left behind would fail at the worst moment.

    Prose mentions are fine -- history and the tombstone are prose. What must
    not survive is an executable reference: a shell line or a python import.
    """
    import ast

    # Denominator: files GIT TRACKS in this repository. Not an rglob with a
    # hand-written blocklist -- that shipped and failed on the box, which has
    # sibling git worktrees (.worktrees/current-main-142cebb,
    # .worktrees/pytest-architecture-repair) each holding an older checkout
    # that still contains the deleted tool. The walk found those and reported
    # this repo as still calling something it had removed.
    #
    # A blocklist is a guess about what to exclude and is wrong the moment
    # somebody adds a directory nobody thought of. `git ls-files` answers the
    # question actually being asked -- what is IN this repository -- so
    # worktrees, venv, node_modules and untracked scratch are excluded because
    # they are not tracked here, not because they were enumerated.
    # @918: NOT `-- '*.py' '*.sh'`. That glob misses 473 tracked
    # extensionless shebang scripts -- the entire toolchain/bin suite and its
    # project-knowledge mirror -- which is how three retired tools survived
    # 59 releases with this gate green. See tests/tracked_source.py.
    entries = tracked_source_files(REPO_ROOT)
    if not entries:
        pytest.skip("git ls-files unavailable; cannot establish the denominator")
    tracked = [rel for rel, _kind in entries]
    assert len(tracked) > 100, (
        f"git ls-files returned only {len(tracked)} source files -- the "
        f"denominator collapsed and a pass below would mean nothing."
    )

    offenders = []
    for rel, kind in entries:
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        if rel.startswith("docs/archive/"):
            continue
        if rel == "tests/test_deploy_manifest_stays_retired.py":
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if kind == "python":
            has_reference = any(
                contains_assembled(path, needle)
                for needle in _RETIRED_REFERENCE_NEEDLES
            )
        else:
            has_reference = any(
                needle in source for needle in _RETIRED_REFERENCE_NEEDLES
            )
        if not has_reference:
            continue

        if kind == "python":
            # AST, so DOCSTRINGS are excluded. An earlier version of this test
            # grepped lines and flagged a docstring in a sibling test that was
            # merely describing the history -- the same predicate-too-broad
            # mistake the retired tool's own docs kept making. The subject is
            # an EXECUTABLE reference; prose about the past is not one.
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    doc = ast.get_docstring(node, clean=False)
                    if doc:
                        docstrings.add(doc)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "deploy_manifest" in alias.name:
                            offenders.append(f"{rel}:{node.lineno}: import {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and "deploy_manifest" in node.module:
                        offenders.append(f"{rel}:{node.lineno}: from {node.module}")
            for value in assembled_strings(path):
                if value in docstrings or value in _NON_INVOCATION_REFERENCES:
                    continue
                if any(needle in value for needle in _RETIRED_REFERENCE_NEEDLES):
                    offenders.append(f"{rel}: assembled string {value[:70]!r}")
        else:
            for lineno, line in enumerate(source.splitlines(), 1):
                code = line.split("#", 1)[0]
                if "deploy_manifest" in code or "deploy-manifest" in code:
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "something still invokes the retired deploy manifest:\n  "
        + "\n  ".join(offenders)
    )


def test_an_assembled_literal_cannot_evade_the_invocation_scan(tmp_path, monkeypatch):
    carrier_source = (
        'import subprocess\n'
        'subprocess.run(["toolchain/bin/bd-deploy" + "-manifest", "--emit"])\n'
    )
    carrier = tmp_path / "carrier.py"
    carrier.write_text(carrier_source, encoding="utf-8")
    assert "deploy_manifest" not in carrier_source
    assert "deploy-manifest" not in carrier_source
    assert contains_assembled(carrier, "deploy-manifest")

    entries = [(carrier.name, "python")]
    for index in range(100):
        harmless = tmp_path / f"harmless_{index}.py"
        harmless.write_text("value = 1\n", encoding="utf-8")
        entries.append((harmless.name, "python"))
    assert len(entries) == 101
    assert all((tmp_path / rel).is_file() for rel, _kind in entries)

    import sys
    mod = sys.modules[__name__]
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "tracked_source_files", lambda _root: entries)
    with pytest.raises(AssertionError, match="something still invokes"):
        test_nothing_still_calls_the_retired_tool()


BD_GATE_SCOPE = "repo-wide"
