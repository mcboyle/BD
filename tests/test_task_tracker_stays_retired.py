"""The TASK_TRACKER subsystem is retired. This keeps it that way, and says why.

TASK_TRACKER_DATA.json was the canonical ledger; TASK_TRACKER.md and .xlsx were
generated views of it. It carried 305 rows -- 283 completed, 11 awaiting
operator, 11 decided against -- and a generator, a sync tool, two operator
tools, and a drift gate inside `bd-pack` existed to keep the three files
consistent with each other.

WHY IT WENT. It was a SECOND register running beside
project-knowledge/SESSION_CARRY.md, and the two never referenced each other:
SESSION_CARRY mentioned TASK_TRACKER zero times while the tracker held eleven
open operator-bound rows nobody in the session queue could see. Two registers
that do not know about each other are worse than one that is merely
incomplete -- each looks authoritative on its own. Everything still OPEN was
absorbed into SESSION_CARRY section 15.15 before removal; the 283 completed
rows were deliberately not copied, because git history holds them and a
completed row is not a thing anyone needs to read again.

`bd-pack` anticipated this exact decision. Its own guidance at the drift gate
read: "...or formally kill the tracker and remove it from TRACKER_FILES. Do NOT
let it silently disappear again." That is what this cut did -- formally, with
the guidance's own condition satisfied, rather than by deletion-and-silence.

THE FAILURE THIS FILE GUARDS AGAINST is the mirror of the one bd-pack policed.
The tracker files vanished once already, at v3.66.700, and `_tracker_drift()`
skipped when they were absent -- so the check that existed to police the
tracker reported CLEAN over its disappearance. Silence read as pass. A
deletion with no gate is that same slot in the other direction: it is easy for
a future session to find the historical rationale, conclude the files are
missing by accident, and regenerate them. If you are here because this test
failed, read the tombstone in BD_TOOLCHAIN_REFERENCE.md before deciding.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

from python_source import assembled_strings
from tracked_source import tracked_source_files

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

RETIRED = (
    # the ledger and its generated views
    "TASK_TRACKER.md",
    "TASK_TRACKER.xlsx",
    "TASK_TRACKER_DATA.json",
    # generator + sync
    "tools/tasktracker_gen.py",
    "tools/tasktracker_sync.py",
    # operator tools whose entire subject was the tracker
    "toolchain/bin/bd-tracker-recon",
    "toolchain/bin/bd-reconcile",
    # their tests
    "tests/test_tasktracker_gen.py",
    "tests/test_tasktracker_status.py",
    "tests/test_tasktracker_sync.py",
    "tests/test_v3_66_754_tracker_decided_against.py",
    "tests/test_v3_66_721_tasktracker_audit.py",
    # the project-knowledge mirror of the generator
    "project-knowledge/tasktracker_gen.py",
    # v3.66.917: the surviving copies. The two toolchain/bin paths above were
    # listed from the start and are genuinely gone, but the tools were MIRRORED
    # into project-knowledge/ without an extension, so neither this tuple nor
    # the '*.py *.sh' scan below could see them. Retiring a tool from one of
    # two homes is not retiring it.
    "project-knowledge/bd-tracker-recon",
    "project-knowledge/bd-reconcile",
)

TOMBSTONE = REPO_ROOT / "project-knowledge" / "IMPROVEMENT_BACKLOG.md"
GATE_PATH = "tests/test_task_tracker_stays_retired.py"

# Prose about the past is not a live dependency. These files describe the
# retirement or the history and are expected to name it.
PROSE_EXEMPT = {
    GATE_PATH,
    "project-knowledge/IMPROVEMENT_BACKLOG.md",
    "CHANGELOG.md",
}


def _mentions_tracker(value: str) -> bool:
    """Match the canonical, module-name, and PEP 8 spellings in any case."""
    without_gate_path = value.casefold().replace(GATE_PATH, "")
    return "tasktracker" in without_gate_path.replace("_", "")


def _executable_tracker_references(repo_root, entries):
    """Return executable tracker references from the tracked-source census."""
    offenders = []
    for rel, kind in entries:
        if rel in PROSE_EXEMPT:
            continue
        path = repo_root / rel
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8", errors="replace")

        if kind == "python":
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                try:
                    tree = ast.parse(source)
                except SyntaxError:
                    continue
                folded_strings = assembled_strings(path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if _mentions_tracker(alias.name):
                            offenders.append(
                                f"{rel}:{node.lineno}: import {alias.name}"
                            )
                elif isinstance(node, ast.ImportFrom):
                    if node.module and _mentions_tracker(node.module):
                        offenders.append(
                            f"{rel}:{node.lineno}: from {node.module}"
                        )

            # The shared helper folds literal concatenation, str.join and
            # os.path.join. Subtract docstrings because retirement prose is
            # not an executable carrier.
            docstrings = {
                value
                for node in ast.walk(tree)
                if isinstance(
                    node,
                    (ast.Module, ast.ClassDef, ast.FunctionDef,
                     ast.AsyncFunctionDef),
                )
                if (value := ast.get_docstring(node, clean=False)) is not None
            }
            values = {
                value
                for value in folded_strings
                if _mentions_tracker(value) and value not in docstrings
            }
            for value in sorted(values):
                offenders.append(f"{rel}: assembled literal {value[:60]!r}")
        else:
            for i, line in enumerate(source.splitlines(), 1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                if _mentions_tracker(line):
                    offenders.append(f"{rel}:{i}: {stripped[:70]}")

    return offenders


def test_the_retired_files_are_gone():
    present = [p for p in RETIRED if (REPO_ROOT / p).exists()]
    assert not present, (
        "these were retired but are present again:\n  "
        + "\n  ".join(present)
        + "\n\nThe open rows were absorbed into the canonical backlog. If you are "
          "restoring the tracker, recover it from git history rather than "
          "regenerating -- TASK_TRACKER_DATA.json was canonical and the .md/"
          ".xlsx were views, so a regenerated .md without its data source is a "
          "document with no truth behind it."
    )


def test_the_retirement_is_documented_not_just_done():
    """A deletion with no explanation gets undone by the next reader."""
    text = TOMBSTONE.read_text(encoding="utf-8")
    # Both words SEPARATELY are useless here: the file already says RETIRED
    # about other tools, and already said TASK_TRACKER inside bd-pack's entry
    # describing its drift gate. An `A in text and B in text` assertion
    # therefore passed before any tombstone existed -- section 0, in this
    # gate's own first draft. Require them on ONE line, which only a real
    # tombstone heading has.
    tombstoned = [ln for ln in text.splitlines()
                  if "TASK_TRACKER" in ln and "RETIRED" in ln]
    assert tombstoned, (
        f"{TOMBSTONE.relative_to(REPO_ROOT)} has no line recording TASK_TRACKER "
        f"as RETIRED. Keep the tombstone: the reason it existed -- a durable "
        f"cross-session ledger -- is still a real need, so a reader who finds "
        f"only the rationale will rebuild it instead of using the backlog."
    )


def test_nothing_still_executes_against_the_tracker():
    """A live reference left behind fails at the worst moment.

    Denominator is `git ls-files`, not an rglob with a blocklist: the box has
    sibling worktrees holding older checkouts that still contain these files,
    and a tree walk finds those and reports this repo as still calling
    something it removed.

    Prose mentions are fine -- history and tombstones are prose. What must not
    survive is an EXECUTABLE reference.
    """
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

    offenders = _executable_tracker_references(REPO_ROOT, entries)

    assert not offenders, (
        "the tracker is retired but these still reference it executably:\n  "
        + "\n  ".join(offenders)
    )


def test_assembled_tracker_literal_is_an_executable_reference(tmp_path):
    """A runtime-built tracker path must not evade the tombstone scan."""
    from python_source import contains_assembled

    carrier = tmp_path / "tools" / "task_tracker_gen.py"
    carrier.parent.mkdir()
    carrier.write_text(
        'TRACKER_PATH = "task_" + "tracker_data.json"\n',
        encoding="utf-8",
    )
    source = carrier.read_text(encoding="utf-8")
    assert "task_tracker" not in source
    assert contains_assembled(carrier, "task_tracker")

    registry = tmp_path / "tests" / "gate_registry.py"
    registry.parent.mkdir()
    registry.write_text(
        'GATE = "tests/test_task_tracker_stays_retired.py"\n',
        encoding="utf-8",
    )
    offenders = _executable_tracker_references(
        tmp_path,
        [
            ("tools/task_tracker_gen.py", "python"),
            ("tests/gate_registry.py", "python"),
        ],
    )
    assert offenders == [
        "tools/task_tracker_gen.py: assembled literal 'task_tracker_data.json'"
    ]


BD_GATE_SCOPE = "repo-wide"
