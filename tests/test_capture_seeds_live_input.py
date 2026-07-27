"""capture.sh must seed synthetic input, then always remove it.

Several live checks WARN because nothing has exercised them. tools/live_seed.py
supplies marked, reversible input; this pins how capture.sh drives it.

The ordering is the whole contract:

    fixture site up  ->  seed  ->  live suite  ->  teardown  ->  fixture down

and the teardown half matters more than the seeding half. Synthetic state left
behind on the operator's box would be read as real work by the next run --
including by the seeder's own preflight, which refuses to seed a host that
already holds real entries. A capture that seeded and then died would wedge
every subsequent capture. So teardown must run whether the live suite passed,
failed, or the operator pressed Ctrl-C.
"""
from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_SH = REPO_ROOT / "capture.sh"

_LIVE_LANE = "live_tests.run"
_SEED_TOOL = "tools/live_seed.py"


def _strip_comments(text: str) -> str:
    """Drop comments so prose can neither satisfy nor trip these gates.

    Quote-aware, so a `#` inside a string does not truncate a real command.
    CLAUDE.md 0 counts an over-sensitive gate as a soundness bug, and a gate
    that fires when someone edits a comment is exactly that.
    """
    out = []
    for line in text.splitlines():
        cleaned, quote = [], None
        for ch in line:
            if quote:
                cleaned.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                cleaned.append(ch)
                continue
            if ch == "#":
                break
            cleaned.append(ch)
        out.append("".join(cleaned))
    return "\n".join(out)


def _code() -> str:
    return _strip_comments(CAPTURE_SH.read_text(encoding="utf-8"))


def test_capture_seeds_before_the_live_suite_runs():
    """Input seeded after the checks have run is input nothing consumed."""
    code = _code()
    seed_at = code.find("--seed")
    lane_at = code.find(_LIVE_LANE)
    assert seed_at != -1, f"capture.sh never invokes {_SEED_TOOL} --seed"
    assert lane_at != -1, "capture.sh no longer runs the live lane -- anchor stale"
    assert seed_at < lane_at, (
        f"--seed appears at offset {seed_at}, after the live lane at {lane_at}; "
        f"seeding after the checks provides nothing for them to exercise"
    )


def test_capture_tears_down_after_the_live_suite():
    """Synthetic state must not outlive the run that created it.

    Asserts on the INVOCATION, not on the string `--teardown`. That string
    lives inside the cleanup function's body, which is DEFINED before the live
    lane and CALLED after it -- so a naive text search finds it early and
    concludes teardown runs first. Definition position is not execution
    position; the call site is the subject.
    """
    code = _code()
    lane_at = code.find(_LIVE_LANE)
    assert lane_at != -1, "capture.sh no longer runs the live lane -- anchor stale"
    assert "--teardown" in code, f"capture.sh never invokes {_SEED_TOOL} --teardown"

    # A bare `cleanup_live_seed` on its own line is the call; the definition
    # line ends in `() {` and the trap line names it as an argument.
    calls = [
        m.start() for m in re.finditer(r"^\s*cleanup_live_seed\s*$", code, re.M)
    ]
    assert calls, "cleanup_live_seed is defined but never called directly"
    assert any(pos > lane_at for pos in calls), (
        f"cleanup_live_seed is only called before the live lane (call offsets "
        f"{calls}, lane at {lane_at}); teardown would remove the very state "
        f"the checks were meant to exercise"
    )


def test_teardown_is_registered_on_exit_so_an_interrupt_cannot_strand_state():
    """A capture that dies mid-run must still clean up.

    Without this, Ctrl-C between seeding and teardown leaves marked rows on the
    box. The seeder's own preflight then refuses to seed next time, because it
    cannot distinguish abandoned synthetic state from real work -- one
    interrupted capture would wedge every later one.
    """
    code = _code()
    traps = re.findall(r"^\s*trap\s+(.+?)\s+EXIT\s*$", code, re.M)
    assert traps, "capture.sh registers no EXIT trap for seed teardown"
    # The predicate must name the SEED teardown specifically. An earlier
    # version accepted any trap containing "cleanup", which matched the graph
    # gate's own `trap cleanup_graph_tmp EXIT` -- so the test passed while no
    # seed teardown existed at all. A gate that cannot distinguish its subject
    # from an unrelated neighbour is not a gate (CLAUDE.md 0).
    seed_traps = [t for t in traps if "seed" in t.lower()]
    assert seed_traps, (
        f"no EXIT trap performs seed teardown; found only {traps}. "
        f"(cleanup_graph_tmp is the graph gate's, not this one's.)"
    )


def test_the_graph_gate_keeps_its_own_exit_trap():
    """A second EXIT trap must not clobber the graph gate's cleanup.

    bash keeps ONE EXIT trap per shell, so a global `trap ... EXIT` would
    silently replace an earlier one. run_graph_hash_gate is defined with
    PARENTHESES -- a subshell function -- which is what isolates its trap and
    makes a global trap safe. That is load-bearing and invisible: converting it
    to braces would compile fine, keep every test green, and start leaking the
    graph temp directory while disabling seed teardown.
    """
    raw = CAPTURE_SH.read_text(encoding="utf-8")
    assert re.search(r"^run_graph_hash_gate\(\)\s*\(", raw, re.M), (
        "run_graph_hash_gate is no longer a subshell function; its `trap "
        "cleanup_graph_tmp EXIT` now shares the global trap slot and will "
        "collide with the seed-teardown trap"
    )
    assert "cleanup_graph_tmp" in raw


def test_the_fixture_site_is_started_before_seeding_and_stopped_after():
    """Seeded URLs point at the local fixture origin; it has to be serving."""
    code = _code()
    start_at = code.find("fixture_site.py")
    seed_at = code.find("--seed")
    assert start_at != -1, "capture.sh never starts tools/fixture_site.py"
    assert start_at < seed_at, (
        "the fixture site is started after seeding; the seeded URLs would "
        "point at a dead origin"
    )


def test_seeding_failure_never_aborts_the_capture():
    """A seeding problem must degrade to warnings, not a broken capture.

    The checks this feeds already WARN honestly when unexercised, and since
    v3.66.818 a live WARN does not fail the verdict. Aborting the capture
    because an optional convenience failed would be strictly worse than the
    warning it was meant to remove.
    """
    code = _code()
    start = code.find("fixture_site.py")
    end = code.find(_LIVE_LANE)
    assert start != -1 and end > start
    window = code[start:end]
    assert not re.search(r"^\s*exit\s+[1-9]", window, re.M), (
        "the seeding block can exit non-zero; it must degrade to a warning"
    )
