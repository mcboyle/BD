"""capture.sh's pytest lanes must be able to RECORD an xdist wedge, not just suffer one.

BACKLOG 147, THE HALF THAT WAS NEVER APPLIED TO THE GATE. v3.66.1126 fixed two
independent blindfolds in the sanctioned whole-suite form and in both tools that
implement it. `capture.sh` -- which is THE GATE, the thing that certifies a tree
on the box -- was named as unaudited in SESSION_CARRY 15.96 and left carrying
both of them. Measured at v3.66.1129: both lanes pass `-q` and neither sets
`PYTHONUNBUFFERED`.

BLINDFOLD ONE, THE FLAG. `-q` sets `verbose == -1`, and pytest-xdist guards its
entire crash-recovery narration on `verbose >= 0` (`DSession.report_line`). So
`replacing crashed worker gwN` and `maximum crashed workers reached: N` are
NEVER WRITTEN under `-q`, while `pytest_testnodedown` writes UNGUARDED and so
`[gwN] node down: Not properly terminated` always is. The reader is shown the
symptom and denied the response. Measured on a reproducer, same code path, only
the flag differing: `-q` -> 0 replace lines, no flag -> 8, `-v` -> 8. DROPPING
`-q` IS SUFFICIENT; `-v` IS NOT REQUIRED and costs ~16k lines a run.

BLINDFOLD TWO, THE BUFFER. A run that never exits never flushes. Measured
across 657 captures: 15 of 15 WEDGED logs end MID-LINE at a 4KB stdio boundary,
642 of 642 COMPLETED logs end with a newline. The stranded ~4KB is exactly where
the recovery narration lands, because it is emitted immediately after the crash.

WHY BOTH, AND WHY NEITHER ALONE IS ENOUGH. Dropping `-q` makes the line get
WRITTEN; `PYTHONUNBUFFERED=1` makes it SURVIVE. Production confirmation at
16:26Z on 2026-08-14 (`logs/test2-full-005-160601.log`) carried the replace line
at line 31828 -- inside the tail that buffering had eaten on all 15 earlier
wedges. Either fix alone leaves the evidence invisible.

WHY THE ENV VAR AND NOT `-u`. `-u` is an interpreter flag and never reaches
`sys.argv`, so a gate comparing a built command against the argv pytest actually
received cannot see it. bd-sweep-run's selftest caught that as 24 red checks the
first time this was written as `-u`.

THIS DOES NOT CHANGE WHAT IS EXECUTED. Both are observability-only: the lane
still collects the same tests under the same markers, and the capture VERDICT is
read from the junit XML by tools/pytest_capture_results.py, never from the log.
Nothing in the tree parses these logs for a result -- checked at v3.66.1130.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

# Its subject is capture.sh's two lane invocations, not an invariant over the
# tree. It does not enumerate tests/, bulk_downloader/ or git ls-files.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
CAPTURE = REPO / "capture.sh"

sys.path.insert(0, str(REPO / "tests"))
from shell_source import shell_code_only   # noqa: E402


def _logical_lines(code: str) -> list[str]:
    """Join backslash continuations into whole commands.

    Cut on STRUCTURE, never on a fixed width (CLAUDE.md section 2a): a shell
    line continuation is syntax, so joining on it cannot slice mid-construct
    the way a fixed window does. The lane invocations span six physical lines
    each, so a per-physical-line assertion would be looking at `-q tests
    --tb=short` in isolation and could not see the `env` prefix three lines
    above it -- the line-scoped-assertion trap, in its multi-line form.
    """
    out: list[str] = []
    buf = ""
    for raw in code.splitlines():
        stripped = raw.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def _pytest_lanes() -> list[str]:
    """Every logical command in capture.sh that invokes pytest as a module.

    Comment-stripped first: a comment is inside the denominator of every gate
    that reads source text, and this file's own docstring names `-q` many
    times. Without `shell_code_only` the explanation would satisfy the check
    it explains -- CLAUDE.md section 0, four recorded instances.
    """
    code = shell_code_only(CAPTURE)
    return [ln for ln in _logical_lines(code) if re.search(r"-m\s+pytest\b", ln)]


def test_capture_script_exists_and_parses():
    assert CAPTURE.is_file(), f"no capture.sh at {CAPTURE}"
    r = subprocess.run(["bash", "-n", str(CAPTURE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_harness_found_both_lanes_before_judging_them():
    """PRECONDITION, ASSERTED BEFORE ANY VERDICT.

    A harness must assert that it built the shape before it asserts the
    outcome (CLAUDE.md section 6): without this, "no lane passes -q" and "the
    extractor found no lanes" are the same green. capture.sh runs exactly two
    pytest lanes, a parallel one and a serial one.
    """
    lanes = _pytest_lanes()
    assert len(lanes) == 2, (
        f"expected exactly 2 pytest lanes in capture.sh, found {len(lanes)}. "
        "If a lane was added or removed this test's denominator is stale and "
        "must be re-derived rather than the count relaxed.\n"
        + "\n".join(f"  | {ln[:160]}" for ln in lanes)
    )
    markers = " ".join(lanes)
    assert "-m capture_parallel" in markers, "parallel lane not found"
    assert "-m capture_serial" in markers, "serial lane not found"


def test_no_capture_lane_passes_dash_q():
    """`-q` suppresses xdist's crash-recovery narration. Backlog 147."""
    for lane in _pytest_lanes():
        assert "-q" not in lane.split(), (
            "a capture pytest lane passes -q, which sets verbose == -1 and "
            "suppresses xdist's `replacing crashed worker gwN` and "
            "`maximum crashed workers reached` (DSession.report_line guards on "
            "verbose >= 0). The capture would show the wedge symptom and hide "
            "the response. Backlog 147.\n"
            f"  lane: {lane[:200]}"
        )


def test_every_capture_lane_is_unbuffered():
    """A run that never exits never flushes, so the wedge tail is lost."""
    for lane in _pytest_lanes():
        assert "PYTHONUNBUFFERED=1" in lane, (
            "a capture pytest lane does not set PYTHONUNBUFFERED=1. A wedged "
            "run never reaches process exit, so everything written since the "
            "last 4KB flush is stranded -- measured as 15 of 15 wedged logs "
            "ending mid-line against 642 of 642 completed logs ending clean. "
            "That stranded tail is exactly where the recovery narration lands. "
            "It must be the ENV VAR and not -u: an interpreter flag never "
            "reaches sys.argv, so nothing can verify it. Backlog 147.\n"
            f"  lane: {lane[:200]}"
        )


def test_the_command_line_is_the_only_source_of_verbosity():
    """THIS FILE'S OWN BLIND SPOT, CLOSED RATHER THAN MERELY STATED.

    Asserting that a lane's argv carries no quiet flag answers "is this lane
    quiet?" ONLY IF argv is the only place quiet can come from. It is not, in
    general: pytest also reads `addopts` from pytest.ini, setup.cfg, tox.ini and
    pyproject.toml, and a conftest can inject arguments at collection time. If
    any of those appeared, the checks above would quietly narrow to a subset of
    their own subject and keep reporting green -- CLAUDE.md section 0 exactly.

    MEASURED at v3.66.1130: none of those four config files exists in this repo
    and no conftest references addopts, so argv IS the whole denominator. This
    test exists to fail at the moment that stops being true, rather than to let
    a future reader inherit an assumption nobody re-checked.
    """
    for name in ("pytest.ini", "setup.cfg", "tox.ini", "pyproject.toml"):
        cfg = REPO / name
        if not cfg.is_file():
            continue
        text = cfg.read_text(encoding="utf-8", errors="replace")
        assert "addopts" not in text, (
            f"{name} now sets addopts. The lane-verbosity checks in this file "
            "read the COMMAND LINE only, so an addopts entry can make a lane "
            "quiet while they still pass. Either drop it or widen those checks "
            "to cover it -- do not leave them reading a partial denominator."
        )

    for conf in (REPO / "conftest.py", REPO / "tests" / "conftest.py"):
        if not conf.is_file():
            continue
        assert "addopts" not in conf.read_text(encoding="utf-8", errors="replace"), (
            f"{conf.name} now touches addopts; see the reasoning above."
        )


def test_the_lanes_were_not_gutted_to_pass_this_file():
    """THE OVER-SENSITIVE DIRECTION, asserted in the same file as the fix.

    A change that deleted the lanes' flags wholesale would satisfy both checks
    above. CLAUDE.md section 6 requires the over-sensitive control beside the
    escape's own test, because a fix that destroys the tool passes the fix's
    test. Each lane must still select its marker and still write the junit XML
    the capture VERDICT is actually read from.
    """
    lanes = _pytest_lanes()
    for lane in lanes:
        assert "--junitxml=" in lane, (
            "a capture lane no longer writes a junit XML. The capture verdict "
            "comes from that XML via tools/pytest_capture_results.py, not from "
            "the log, so losing it turns a graded lane into an ungraded one.\n"
            f"  lane: {lane[:200]}"
        )
        assert re.search(r"-m\s+capture_(parallel|serial)\b", lane), (
            "a capture lane no longer selects a lane marker, so it would run "
            "the wrong denominator.\n"
            f"  lane: {lane[:200]}"
        )
    assert any("--dist loadfile" in ln for ln in lanes), (
        "the parallel lane no longer pins --dist loadfile; the distribution "
        "that every wedge measurement was taken under would change."
    )
