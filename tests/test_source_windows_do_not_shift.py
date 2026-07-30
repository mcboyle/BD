"""A fixed-width slice of source is a denominator that moves when you edit above it.

THE PATTERN. Many source-scanning tests locate a function and then assert over a
FIXED CHARACTER WINDOW:

    pos = src.find("def _handle_captcha_check")
    body = src[pos:pos + 3000]
    assert "captcha_type=" in body

The assertion is correct and the subject never changes. But the window is a magic
number, so adding ANY lines above the target -- a comment, a guard, an argument
-- pushes it out of range and the test fails about a property that is still true.

MEASURED, three times in one session, each in a different file:

  test_v3_43_31_rate_limit.py:413   src[fn_pos:fn_pos + 22000]
      `_rl_slot.release()` fell out of range when comments were added to
      _http_download. The window had ALREADY been widened once -- the code
      carried the note "window bumped 20000->22000".

  test_v3_43_39_captcha_resolver.py:398   src[pos:pos + 3000]
      `captcha_type=` fell out of range when two lines were added to
      _handle_captcha_check. grep confirmed the string was still present
      (1 occurrence); only the window had moved past it.

  test_git_deploy_gaps_are_documented.py   a directory-only ignore rule
      Same shape in a different medium: a query whose answer depended on
      incidental state rather than on the rule being asserted.

Each was a false failure about correct code, and each cost a diagnosis. A gate
whose answer changes when unrelated text is added is not measuring the property
it names -- CLAUDE.md section 0's inverse: over-sensitivity is a soundness bug,
not a safe default, because a gate that cries wolf gets switched off.

THIS GATE DOES NOT CLOSE THE CLASS, AND SAYS SO. There are ~118 such windows
across ~50 files. Converting them all is a mechanical sweep with a real blast
radius and belongs in its own cut. What this does is:

  * COUNT them, so the size of the problem is a measured number rather than an
    impression, and record it here;
  * HOLD THE LINE -- the count must not grow. A new fixed-width window fails
    this test, so the pattern stops spreading while the backlog is worked down;
  * name the replacement, which is `ast.unparse` of the function node. That is
    exact, cannot drift, and is what the two repaired tests now use.

The ratchet is deliberately one-directional: lowering _MAX_WINDOWS as files are
converted is a normal part of any cut that converts one.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Measured 2026-07-29 at 118; re-measured 2026-07-30 at 115 after the
# done_today_count cut converted the three windows in test_v3_43_23_quick_wins.
# Lower it whenever a cut converts some; never raise it. Raising it is the
# switch-it-off move this gate exists to prevent.
_MAX_WINDOWS = 115

# The specific TEST FUNCTIONS converted so far. Scoped to the function, not the
# file: only one assertion in each of these files was converted, and claiming
# the whole file was clean is exactly the kind of overstated denominator this
# gate exists to catch -- the first version of this constant asserted the files
# and failed immediately, correctly.
_CONVERTED = {
    ("tests/test_v3_43_39_captcha_resolver.py",
     "test_handle_captcha_check_tracks_type"),
    ("tests/test_v3_43_31_rate_limit.py",
     "test_runner_releases_slot_in_finally"),
    # done_today_count cut: adding ten lines to _update_job_current pushed the
    # asserted condition to offset 3574, so the 3000-char window reported it
    # missing on a tree where the line was present and correct. All three
    # windows in the file were converted, not just the one that fired.
    ("tests/test_v3_43_23_quick_wins.py",
     "test_update_job_stamps_last_progress_at_on_status_change"),
    ("tests/test_v3_43_23_quick_wins.py",
     "test_load_urls_initializes_last_progress_at"),
    ("tests/test_v3_43_23_quick_wins.py",
     "test_retry_one_validates_state"),
}


def _tracked_tests() -> list[str]:
    """git ls-files, never rglob: ephemeral agent worktrees live under the
    repository root and would double-count every file in the tree."""
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "tests/*.py"],
                         capture_output=True, text=True).stdout
    return [p for p in out.split("\0") if p]


def _fixed_windows(rel: str) -> list[tuple[int, int, str]]:
    """(lineno, width, expr) for every `something[x : x + <int>]` in a file.

    AST, not a regex: the shape is a Subscript whose Slice upper bound is a
    BinOp adding an integer literal. A textual search would match arithmetic in
    strings, comments and unrelated slicing.

    Width floor of 100 excludes ordinary small slices (`s[i:i+4]` reading a
    length prefix, say) which are not source-window scans.
    """
    try:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        sl = node.slice
        if not isinstance(sl, ast.Slice) or sl.upper is None:
            continue
        up = sl.upper
        if (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Add)
                and isinstance(up.right, ast.Constant)
                and isinstance(up.right.value, int)
                and up.right.value >= 100):
            found.append((node.lineno, up.right.value, ast.unparse(node)[:70]))
    return found


def test_the_scan_can_see_the_pattern():
    """Canary. A zero total would make the ratchet vacuous -- and this pattern
    is known to exist, so zero means the detector broke, not that the tree is
    clean."""
    total = sum(len(_fixed_windows(rel)) for rel in _tracked_tests())
    assert total > 0, (
        "the scan found no fixed-width source windows at all. Six have been "
        "repaired by hand and ~115 remain, so zero means the AST predicate "
        "stopped matching -- not that the pattern is gone."
    )


def test_the_converted_tests_stay_converted():
    """Named, so a regression in these is not hidden by ratchet slack.

    Scoped to the individual test function. Each of these files still contains
    other fixed-width windows that this cut did not touch; asserting the FILE
    was clean was wrong and this gate said so on its first run.
    """
    offenders = []
    for rel, fname in sorted(_CONVERTED):
        try:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        except SyntaxError:
            offenders.append(f"{rel}: does not parse")
            continue
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == fname), None)
        if fn is None:
            offenders.append(f"{rel}: {fname} no longer exists")
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Subscript):
                continue
            sl = node.slice
            if not isinstance(sl, ast.Slice) or sl.upper is None:
                continue
            up = sl.upper
            if (isinstance(up, ast.BinOp) and isinstance(up.op, ast.Add)
                    and isinstance(up.right, ast.Constant)
                    and isinstance(up.right.value, int)
                    and up.right.value >= 100):
                offenders.append(
                    f"{rel}:{node.lineno} in {fname}: "
                    f"{ast.unparse(node)[:60]}")
    assert not offenders, (
        "these tests were converted to AST-derived function bodies and have "
        "regressed to fixed-width source windows:\n  "
        + "\n  ".join(offenders) +
        "\nUse ast.unparse of the function node -- exact, and it cannot drift "
        "when lines are added above the assertion target."
    )


def test_the_fixed_window_count_does_not_grow():
    """THE RATCHET.

    Not a claim that the pattern is fixed -- it is not. A claim that it stops
    spreading. Each conversion cut should lower _MAX_WINDOWS; nothing should
    ever raise it.
    """
    per_file = {rel: _fixed_windows(rel) for rel in _tracked_tests()}
    total = sum(len(v) for v in per_file.values())
    assert total <= _MAX_WINDOWS, (
        f"fixed-width source windows rose to {total}, above the ratchet of "
        f"{_MAX_WINDOWS}. A window like src[pos:pos + 3000] fails whenever "
        f"unrelated lines are added above the thing it asserts on -- measured "
        f"three times in one session, each a false failure about correct code. "
        f"Use ast.unparse of the function node.\n"
        f"Worst offenders now:\n  "
        + "\n  ".join(
            f"{rel}:{ln} width={w}"
            for rel, hits in sorted(
                per_file.items(),
                key=lambda kv: -max((h[1] for h in kv[1]), default=0))[:5]
            for ln, w, _s in sorted(hits, key=lambda h: -h[1])[:1])
    )
