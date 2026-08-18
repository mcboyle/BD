"""A.1 regression test — assert the inline `# INV-NNN` tag count
across the source tree does not silently decrease.

Background: each tag marks a load-bearing source line that protects
an invariant documented in DANGER_MAP.md. A Black/Ruff reformat, a
"clean up comments" pass, or a naive refactor can strip them
silently. This test fails fast if the count drops below the floor.

Maintenance:
- Floor (INV_TAG_FLOOR) may be RAISED when new tags land.
- Floor must NEVER be lowered unless an invariant is legitimately
  retired, in which case the same commit also updates DANGER_MAP.md,
  INV_TAGS.md, and (if relevant) tests/test_invariant_*_*.py.

See the generated INV_TAGS.md view and root INVARIANTS.json.
"""
from __future__ import annotations

from pathlib import Path


# Floor count at v3.64.4 (A.1 restore from session output that
# never made it into v3.64.1's actual release zip; see
# OPEN_THREADS for the rollback history). 33 = 19 inline tags +
# 8 file-top markers + 2 INV-003 explanatory comments + 4
# pre-existing dev_suite.py tags. The count is the grep output —
# don't try to maintain a per-category breakdown, just the total.
INV_TAG_FLOOR = 33

# Paths to scan, relative to the repo root. Matches the original
# A.1 scope: every module that has a tagged invariant.
_SCAN_DIRS = ["bulk_downloader"]
_SCAN_FILES: list[str] = []  # add specific files (e.g. run_tests.py) here
                              # if/when tags are added outside packages


def _count_inv_tags() -> tuple[int, list[str]]:
    """Return (count, lines) where lines are the matching source lines
    for diagnostics on failure."""
    repo_root = Path(__file__).resolve().parent.parent
    matches: list[str] = []
    for d in _SCAN_DIRS:
        for py in (repo_root / d).rglob("*.py"):
            try:
                text = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if "# INV-" in line:
                    matches.append(f"{py.relative_to(repo_root)}:{i}: {line.rstrip()}")
    for f in _SCAN_FILES:
        p = repo_root / f
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if "# INV-" in line:
                matches.append(f"{f}:{i}: {line.rstrip()}")
    return (len(matches), matches)


def test_inv_tag_count_is_at_or_above_floor():
    """The number of `# INV-` lines across the source tree must be
    >= INV_TAG_FLOOR. A drop means tags were stripped — either by a
    bulk reformat, a refactor that didn't preserve them, or an
    invariant retirement that didn't update the floor."""
    count, matches = _count_inv_tags()
    if count < INV_TAG_FLOOR:
        missing = INV_TAG_FLOOR - count
        lines_repr = "\n".join("  " + m for m in matches)
        assert False, (
            f"# INV- tag count regressed: found {count}, floor is "
            f"{INV_TAG_FLOOR} ({missing} tag(s) missing). "
            f"Current tags:\n{lines_repr}\n\n"
            f"If a tag was legitimately removed (invariant retired), "
            f"regenerate INV_TAGS.md and update INV_TAG_FLOOR in "
            f"this file in the SAME commit."
        )


def test_inv_tag_count_floor_documented():
    """Sanity check: INV_TAG_FLOOR must be a positive integer. Catches
    accidental zeroing during a debug edit."""
    assert isinstance(INV_TAG_FLOOR, int), "INV_TAG_FLOOR must be int"
    assert INV_TAG_FLOOR > 0, "INV_TAG_FLOOR must be positive"
