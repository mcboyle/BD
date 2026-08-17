"""CODEX_HANDOFF.md is retired. This keeps it that way, and says why.

It was a second AGENT-FACING document beside CLAUDE.md, recording a parallel
Codex agent's 34-task program: a task ledger, design decisions, and where
Analysis Task 4 paused. Eleven of the 34 groups were complete; the rest are
absorbed into project-knowledge/SESSION_CARRY.md section 15.15.

WHY IT WENT, and it is not the same reason the task tracker went. The tracker
was a duplicate REGISTER. This was a duplicate CONTRACT -- a second document an
agent reads before acting, describing a different machine. It once shipped 14
commands against a dot-prefixed `venv` that does not exist on this host while
CLAUDE.md said otherwise, and a session followed the wrong one and reported
seven failures that were not real. `tests/test_codex_handoff_defers_to_claude_md.py`
existed solely to stop that recurring: it failed if the handoff restated any
fact CLAUDE.md owns.

That test is retired with its subject, and the retirement is why: a gate whose
whole purpose is to keep a SECOND contract from contradicting the first is
answered more completely by not having a second contract. Removing the document
removes the failure class; keeping the gate without the document would be a
check that can no longer encounter its subject, which CLAUDE.md section 0 calls
worse than no gate at all.

WHAT MUST NOT BE LOST is the lesson, not the file. The `.venv` trap is recorded
in CLAUDE.md section 5 and project-knowledge/LESSONS_LEARNED_v3_66_818.md, and
those are prose about a real incident -- they stay. If you are here because this
test failed, read the tombstone in BD_TOOLCHAIN_REFERENCE.md before restoring
anything.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tracked_source import tracked_source_files

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    "CODEX_HANDOFF.md",
    "tests/test_codex_handoff_defers_to_claude_md.py",
)

TOMBSTONE = REPO_ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"

# Prose about the incident is the point of keeping the incident. These name it
# deliberately and must keep doing so.
PROSE_EXEMPT = {
    "tests/test_codex_handoff_stays_retired.py",
    "project-knowledge/BD_TOOLCHAIN_REFERENCE.md",
    "project-knowledge/IMPROVEMENT_BACKLOG.md",
    "project-knowledge/LESSONS_LEARNED_v3_66_818.md",
    "CLAUDE.md",
    "CHANGELOG.md",
}


def test_the_retired_files_are_gone():
    present = [p for p in RETIRED if (REPO_ROOT / p).exists()]
    assert not present, (
        "these were retired but are present again:\n  "
        + "\n  ".join(present)
        + "\n\nThe open task groups were absorbed into the canonical backlog. A "
          "second agent-facing contract is the failure this retirement removes "
          "-- restoring the file restores the failure, so recover it from git "
          "history for reference rather than reinstating it as a live document."
    )


def test_the_retirement_is_documented_not_just_done():
    """A deletion with no explanation gets undone by the next reader.

    Both words separately prove nothing here -- the file says RETIRED about
    several tools already. Require them on ONE line, which only a real
    tombstone heading has. (The task-tracker gate's first draft made exactly
    this mistake and passed before its tombstone existed.)
    """
    text = TOMBSTONE.read_text(encoding="utf-8")
    tombstoned = [ln for ln in text.splitlines()
                  if "CODEX_HANDOFF" in ln.upper() and "RETIRED" in ln.upper()]
    assert tombstoned, (
        f"{TOMBSTONE.relative_to(REPO_ROOT)} has no line recording "
        f"CODEX_HANDOFF as RETIRED. Keep the tombstone: a reader who finds the "
        f"34-task program referenced in the historical record and no explanation for "
        f"the document's absence will recreate it."
    )


def test_claude_md_still_owns_the_venv_fact_the_handoff_contradicted():
    """The lesson outlives the document.

    The concrete harm was a second contract naming a `.venv` that does not
    exist here. That fact must remain owned and stated in CLAUDE.md, or
    retiring the handoff quietly deletes the correction along with the error.
    """
    contract = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "venv/bin/python" in contract, (
        "CLAUDE.md no longer states the interpreter path. That is the fact the "
        "retired handoff contradicted; if the contract stops owning it, the "
        "retirement has removed the correction and kept nothing."
    )
    assert ".venv" in contract, (
        "CLAUDE.md no longer warns about the dot-prefixed `.venv` that does "
        "not exist on this host. A session followed that wrong path once and "
        "reported seven failures that were not real -- keep the warning."
    )


def test_nothing_still_executes_against_the_handoff():
    """Prose mentions are fine -- history is prose. Executable references are not."""
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
        f"denominator collapsed and a pass here would mean nothing."
    )

    offenders = []
    for rel, kind in entries:
        if rel in PROSE_EXEMPT:
            continue
        path = REPO_ROOT / rel
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if "CODEX_HANDOFF" not in source:
            continue
        if kind == "python":
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if "CODEX_HANDOFF" not in node.value:
                        continue
                    parent_doc = any(
                        ast.get_docstring(n, clean=False) == node.value
                        for n in ast.walk(tree)
                        if isinstance(n, (ast.Module, ast.ClassDef,
                                          ast.FunctionDef, ast.AsyncFunctionDef))
                    )
                    if not parent_doc:
                        offenders.append(f"{rel}:{node.lineno}: literal {node.value[:60]!r}")
        else:
            for i, line in enumerate(source.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if "CODEX_HANDOFF" in line:
                    offenders.append(f"{rel}:{i}: {line.strip()[:70]}")

    assert not offenders, (
        "CODEX_HANDOFF.md is retired but these still reference it executably:\n  "
        + "\n  ".join(offenders)
    )


BD_GATE_SCOPE = "repo-wide"
