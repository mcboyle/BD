"""bd-doctor must probe the environment it is running in, not a retired one.

Its "headless browser" row launched a probe with a hand-built environment:

    env = dict(os.environ,
               PLAYWRIGHT_BROWSERS_PATH="/home/claude/.cache/ms-playwright",
               PYTHONPATH="/tmp/prestaged_site_packages")

Both paths are dead. Measured on this container, where the browser works:

    with that env : exit=1, Playwright's "browser not found / run install" banner
    with real env : exit=0, "ok"

So the row reported the headless browser BROKEN on a host where it is fine.
That is the section 0 INVERSE -- a gate firing on nothing. Over-sensitivity is
a soundness bug, not a safe default: a check that cries wolf gets switched off,
and then the real signal goes with it.

PYTHONPATH is REPLACED, not appended, so the override also removes the probe's
ability to import anything the caller had arranged.

WHAT THIS IS NOT. The override is not a cloak/stealth requirement.
`bulk_downloader/auto_detect.py:173-179` states the split: "CloakBrowser owns
its own binary; this warning is for the Playwright fallback path." cloakbrowser
is a separate backend that ships its own browser and never reads
PLAYWRIGHT_BROWSERS_PATH -- no cloak module references it. bd-doctor's probe is
plain Playwright, i.e. precisely the path that needs the variable it was
overwriting.

A SECOND DENOMINATOR PROBLEM, fixed in the same cut. BD chooses between two
backends (`bulk_downloader/cloak.py:225 resolve_backend()` is the single source
of truth). Probing Playwright unconditionally answers a question about a
backend the box may not be running -- a friendlier wrong answer, but the same
mistake. The row now names the backend it tested.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "toolchain" / "bin" / "bd-doctor"
PYTHON = REPO_ROOT / "venv" / "bin" / "python"

# The dead paths. Kept as data so the assertions below name what they mean
# rather than repeating string literals.
DEAD = {
    "/home/claude/.cache/ms-playwright": "the retired sandbox browser pool",
    "/tmp/prestaged_site_packages": "the retired prestaged site-packages",
    "/home/claude/work": "the retired sandbox work tree",
}


def _source() -> str:
    return TOOL.read_text(encoding="utf-8")


def test_the_tool_exists():
    assert TOOL.is_file(), f"{TOOL} missing"


@pytest.mark.parametrize("dead_path", sorted(DEAD), ids=lambda p: p.strip("/").replace("/", "_"))
def test_no_dead_sandbox_path_is_used_as_a_value(dead_path: str):
    """Present in a comment is fine; present as a VALUE is the defect.

    Distinguishing the two matters -- an earlier version of a sibling test
    grepped the whole file and tripped on the comment explaining the history,
    which is the same predicate-too-broad error the tools themselves made. A
    value here means an assignment, a default=, or a dict entry.
    """
    offenders = []
    for lineno, line in enumerate(_source().splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or dead_path not in line:
            continue
        # A quoted occurrence that is not inside a trailing comment.
        code = line.split("#", 1)[0]
        if re.search(rf"""['"]{re.escape(dead_path)}""", code):
            offenders.append(f"{lineno}: {stripped[:110]}")
    assert not offenders, (
        f"bd-doctor still uses {dead_path} ({DEAD[dead_path]}) as a value:\n  "
        + "\n  ".join(offenders)
        + "\n\nIt does not exist. Resolve from the environment or the work "
          "tree; do not substitute a second hardcoded path."
    )


def test_the_probe_does_not_overwrite_the_callers_browser_pool():
    """The specific mechanism, asserted structurally.

    A fix that merely swapped the literal for another literal would satisfy
    the test above only by accident; this one says the probe must not FORCE
    the variable at all.
    """
    src = _source()
    forced = re.findall(r"PLAYWRIGHT_BROWSERS_PATH\s*=\s*[\"'][^\"']+[\"']", src)
    assert not forced, (
        f"bd-doctor forces PLAYWRIGHT_BROWSERS_PATH to a literal: {forced}. "
        "Inherit what the caller set; resolve a value only when it is unset."
    )
    forced_pp = re.findall(r"PYTHONPATH\s*=\s*[\"'][^\"']+[\"']", src)
    assert not forced_pp, (
        f"bd-doctor forces PYTHONPATH to a literal: {forced_pp}. PYTHONPATH is "
        "replaced, not appended, so this removes whatever the caller arranged."
    )


def test_the_browser_row_names_the_backend_it_tested():
    """BD has two backends and only one of them uses a Playwright pool.

    `bulk_downloader/cloak.py:225 resolve_backend()` is the single source of
    truth for which is in use. A row that says "headless browser: ok" without
    saying which backend it launched is answering a question the reader did
    not ask.
    """
    src = _source()
    assert "resolve_backend" in src, (
        "bd-doctor never consults cloak.resolve_backend(), so its browser row "
        "reports on whichever backend it happens to probe rather than the one "
        "the box is configured to run."
    )


# @944: test_the_mirror_matches is GONE, not skipped. @943 retired the
# project-knowledge mirror, after which its `if not MIRROR.exists(): skip` guard
# could never do anything but skip -- measured in the v3.66.943 box capture,
# where it is the single test separating 85 skips from 86. A test that can only
# skip reports nothing while still being counted as coverage, which is the
# denominator defect this file exists to guard against, sitting inside it. The
# stronger property it was reaching for -- that NO duplicate of the executable
# toolchain exists anywhere -- is held by tests/test_pk_mirrors_stay_retired.py.


def test_the_tool_still_runs_end_to_end(tmp_path):
    """Guards the fix against making the tool unrunnable.

    Not asserting a verdict -- that depends on the host -- only that it
    executes and produces output rather than crashing on a missing path.
    """
    proc = subprocess.run(
        [str(PYTHON), str(TOOL), "--work", str(REPO_ROOT)],
        capture_output=True, text=True, timeout=180, cwd=str(REPO_ROOT),
    )
    assert proc.stdout.strip(), (
        f"bd-doctor produced no output (exit={proc.returncode}):\n{proc.stderr[:800]}"
    )
