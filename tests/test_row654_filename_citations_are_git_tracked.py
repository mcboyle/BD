"""Row 654: the filename-citation scan in test_v3_66_1255 sees every tracked
test_row<N>_*.py file, not just what happens to be on disk.

test_v3_66_1255_backlog_references_resolve.py's new filename gate walks
``tests/`` with ``Path.glob`` -- fast, but blind to a file that is staged for
deletion or was never staged at all.  This is an independent cross-check via
``git ls-files``, the population FLEET_RULE 8 names as the correct
denominator, so the two scanners can never silently drift apart.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from test_v3_66_1255_backlog_references_resolve import (
    ROOT,
    _PENDING_NEW_BACKLOG_ROW_IDS,
    _ROW_FILENAME,
    _is_cut_slug_not_a_row_citation,
    _row_filename_ids,
)

BD_GATE_SCOPE = "repo-wide"


def _tracked_row_filenames() -> list[str]:
    output = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "tests/test_row*_*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line.rsplit("/", 1)[-1] for line in output.splitlines() if line]


def test_glob_scan_and_git_tracked_population_agree_on_row_citing_files() -> None:
    tracked = _tracked_row_filenames()
    assert tracked, "found zero git-tracked tests/test_row<N>_*.py: prove the population first"

    tracked_citations = set()
    for name in tracked:
        match = _ROW_FILENAME.match(name)
        if match is None:
            continue
        row_id = int(match.group(1))
        if _is_cut_slug_not_a_row_citation(ROOT / "tests" / name, row_id):
            continue
        tracked_citations.add(name)

    glob_citations = {
        name for names in _row_filename_ids(ROOT).values() for name in names
    }
    assert tracked_citations == glob_citations, (
        f"git ls-files and glob disagree on which test_row<N>_*.py files cite a "
        f"backlog row: git-only={tracked_citations - glob_citations}, "
        f"glob-only={glob_citations - tracked_citations}"
    )


def test_pending_backlog_ids_are_a_nonempty_frozenset_of_positive_ints() -> None:
    assert _PENDING_NEW_BACKLOG_ROW_IDS, "prove the pending declaration is non-empty"
    assert isinstance(_PENDING_NEW_BACKLOG_ROW_IDS, frozenset)
    assert all(isinstance(row_id, int) and row_id > 0 for row_id in _PENDING_NEW_BACKLOG_ROW_IDS)
