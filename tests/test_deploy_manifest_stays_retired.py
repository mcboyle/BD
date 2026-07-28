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

REPO_ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    "tools/deploy_manifest.py",
    "toolchain/bin/bd-deploy-manifest",
    "project-knowledge/deploy_manifest.py",
    "tests/test_v3_66_722_deploy_manifest_ships.py",
)

TOMBSTONE = REPO_ROOT / "project-knowledge" / "BD_TOOLCHAIN_REFERENCE.md"


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
    assert "RETIRED" in text and "bd-deploy-manifest" in text, (
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

    offenders = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".sh"}:
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(p) for p in
               ("venv/", "audit-venv/", "node_modules/", "docs/archive/", ".git/")):
            continue
        if rel == "tests/test_deploy_manifest_stays_retired.py":
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if "deploy_manifest" not in source and "deploy-manifest" not in source:
            continue

        if path.suffix == ".py":
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
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    v = node.value
                    if v in docstrings:
                        continue
                    if "deploy_manifest" in v or "deploy-manifest" in v:
                        offenders.append(f"{rel}:{node.lineno}: string {v[:70]!r}")
        else:
            for lineno, line in enumerate(source.splitlines(), 1):
                code = line.split("#", 1)[0]
                if "deploy_manifest" in code or "deploy-manifest" in code:
                    offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        "something still invokes the retired deploy manifest:\n  "
        + "\n  ".join(offenders)
    )
