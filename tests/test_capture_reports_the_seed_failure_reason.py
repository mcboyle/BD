"""v3.66.839 -- when seeding declines, capture.sh must print the REASON.

The failure branch printed a fixed `tail -3` of a variable-length log. Measured
on the real tool at v3.66.838:

    env -u PYTHONUNBUFFERED BD_INSTALL_DIR=<tmp> venv/bin/python \\
        tools/live_seed.py --seed --start --login --vpn-tunnel --count 3 --dry-run

exits 2 with a 31-line log whose LINE 1 is

    live_seed: REFUSED - cannot read /api/vpn/status, so this host's VPN state
    is UNKNOWN and tunnel seeding is refused (...)

and whose last three lines -- exactly what capture.sh printed -- are
`"dry_run": true`, `}`, `]`. The operator was shown the tail of a JSON plan
instead of the sentence explaining why nothing was seeded.

Two independent causes, and a fix for either alone still misreports:

1. STREAM ORDER. live_seed writes its plan JSON to stdout from a `finally`
   and its diagnostic to stderr. capture.sh merges them with `2>&1`. When
   stdout is a FILE it is block-buffered while stderr is write-through, so
   the two do not interleave in source order -- the reason can land at either
   end depending on buffering, which is why a bigger tail is not a fix.
2. BLIND TAIL. `tail -3` asserts nothing about content. The diagnostic is a
   variable number of lines (the TIMEOUT arm emits one line per unresolved
   URL), so no fixed N is correct.

Both live_seed exit paths carry a stable `live_seed: ` prefix -- REFUSED at
:1365 and TIMEOUT at :1373-1383 -- so the report can select on the marker
rather than on position.

NOTE for anyone re-running the reproduction: `env -u PYTHONUNBUFFERED` is
load-bearing. pytest sets PYTHONUNBUFFERED=1, which makes stdout
write-through, hides cause 1 entirely, and makes the probe prove nothing.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CAPTURE = REPO / "capture.sh"

# The failure branch under test, located by its operator-visible string.
_BRANCH_ANCHOR = "seeding declined or failed"


@pytest.fixture(scope="module")
def capture_src() -> str:
    return CAPTURE.read_text(encoding="utf-8")


def test_canary_the_failure_branch_is_locatable(capture_src):
    """A zero-length denominator would make every rule below vacuous."""
    assert _BRANCH_ANCHOR in capture_src, (
        f"{_BRANCH_ANCHOR!r} not found in capture.sh -- this gate reads the "
        f"script's own failure branch and can no longer find it, so it cannot "
        f"answer. Re-anchor before trusting any result here."
    )


def _failure_branch(src: str) -> str:
    """The seeding-failure branch's own lines, standalone-runnable.

    Cut at the `fi`/`else` that closes the ENCLOSING branch, identified by
    indentation: the branch may legitimately contain its own nested if/else/fi,
    and a naive "stop at the first else" truncates mid-construct and yields a
    script bash cannot parse -- which would read as a failure of the thing under
    test rather than of this harness.
    """
    start = src.index(_BRANCH_ANCHOR)
    line_start = src.rfind("\n", 0, start) + 1
    lines = src[line_start:].splitlines()
    base = len(lines[0]) - len(lines[0].lstrip())
    out = []
    for line in lines:
        indent = len(line) - len(line.lstrip())
        if re.match(r"\s*(fi|else)\b", line) and indent < base:
            break
        out.append(line)
    return "\n".join(out)


# ── the defect ───────────────────────────────────────────────────────────────


def test_the_seed_failure_report_is_not_a_blind_fixed_tail(capture_src):
    """`tail -3` cannot see its subject: the reason's position is not fixed.

    The TIMEOUT arm emits one line per unresolved URL, so the diagnostic is
    variable-length; and stdout/stderr buffering decides which end it lands on.
    """
    branch = _failure_branch(capture_src)
    blind = re.search(r"tail\s+-\d+\s+\"?\$?\{?OUT", branch)
    assert not blind, (
        "the seeding-failure branch still reports with a fixed-N tail of the "
        f"log:\n{branch}\n"
        "A fixed N cannot be correct -- the diagnostic is variable-length and "
        "its position depends on stream buffering. Select on the "
        "'live_seed: ' marker instead."
    )


def test_the_report_selects_on_the_live_seed_marker(capture_src):
    """The positive form: not printing the wrong thing is not the same as
    printing the right thing. A branch that simply dropped the tail would
    satisfy the rule above and leave the operator with nothing."""
    branch = _failure_branch(capture_src)
    assert "live_seed:" in branch, (
        "the failure branch does not select on the 'live_seed: ' diagnostic "
        f"marker, so it cannot surface the REASON:\n{branch}"
    )


def test_the_seeder_is_run_unbuffered(capture_src):
    """Cause 1. Without -u, stdout is block-buffered into the log file and the
    stderr diagnostic does not interleave in source order."""
    idx = capture_src.find("tools/live_seed.py --seed")
    assert idx > 0, "the seeding invocation moved; re-anchor this gate"
    line_start = capture_src.rfind("\n", 0, idx) + 1
    # Read the whole LOGICAL command by following backslash continuations,
    # not a fixed-width slice. A fixed window is the precise pattern
    # test_source_windows_do_not_shift ratchets: anything inserted above the
    # target silently pushes it out of view and the assertion then passes over
    # nothing. Derived bounds cannot drift that way.
    invocation_lines = []
    for line in capture_src[line_start:].splitlines():
        invocation_lines.append(line)
        if not line.rstrip().endswith("\\"):
            break
    invocation = "\n".join(invocation_lines)
    assert re.search(r"python\s+-u\b", invocation), (
        "live_seed.py is invoked without -u, so its stdout is block-buffered "
        "when redirected to a file and the stderr reason will not appear in "
        f"source order:\n{invocation}"
    )


# ── functional: the branch, executed ─────────────────────────────────────────


def test_the_branch_actually_prints_the_reason(capture_src, tmp_path):
    """Derive the behaviour, do not assert the text.

    Run the real failure branch against a synthetic log shaped like the
    measured one -- reason on line 1, 30 lines of JSON after it -- and require
    the reason to reach stdout.
    """
    out = tmp_path / "out"
    out.mkdir()
    reason = (
        "live_seed: REFUSED - cannot read /api/vpn/status, so this host's "
        "VPN state is UNKNOWN and tunnel seeding is refused."
    )
    log = out / "05a_live_seed.log"
    log.write_text(
        reason + "\n" + "\n".join(
            ['[', '  {', '    "action": "seed_urls",', '    "dry_run": true', '  }', ']']
            + ['    "filler": %d,' % i for i in range(24)]
        ) + "\n",
        encoding="utf-8",
    )

    script = "OUT=%s\n%s\n" % (str(out), _failure_branch(capture_src))
    proc = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )
    combined = proc.stdout + proc.stderr
    assert "cannot read /api/vpn/status" in combined, (
        "the failure branch did not surface the reason. It printed:\n"
        f"{combined}\n--- the log's line 1 was ---\n{reason}"
    )
