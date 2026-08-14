"""bd-chromium-race must verify its own markers, or its zeros mean nothing.

WHY THIS TOOL IS TRACKED AT ALL. It is the standalone reproducer for backlog row
144(a), and it falsifies that row's own premise: row 144 and SESSION_CARRY 15.96
both concluded that only the full 48-way suite could push
test_a_site_with_no_declared_wall_is_untouched past 240s. Measured 2026-08-14 at
d734dd5 on test3, with 48 processes launching chromium in a loop (186 browsers
resident): the test took 568.4s against a 27.0s baseline, standalone, in 9.5
minutes. Before that, the only instrument was a ~19-hour overnight hunt.

An artifact that valuable living in ~ is one disk failure from gone -- the
overnight handoff says exactly that about itself, in section 9, while sitting in
~ untracked. So it is in the repo, and this file is what keeps it honest.

THE DEFECT CLASS IT GUARDS, WHICH IS THE WHOLE POINT. A probe that greps for a
string the source does not contain reports 0 forever, and 0 gets read as a
result. That happened twice in the 2026-08-14 session: a probe grepped for
"auto-submitted on fill" when the emitted line is "already at success URL after
fill", reported 0 across two rounds, and the 0 was believed. Section 0's shape,
in an instrument rather than a gate.

So the tool has a --selftest that resolves its markers AGAINST submit.py, and
this file proves that selftest works in BOTH directions -- green on the real
tree, and RED when handed the exact historical typo. A selftest that cannot fail
is not a selftest, and proving only the green half is the default mistake
(CLAUDE.md section 6).

WHAT THIS FILE DOES NOT DO: it never launches a browser and never runs a sample.
Driving the real arm takes minutes and a quiet host, which does not belong in a
band. It checks the instrument, not the measurement.
"""

from __future__ import annotations

import ast
import contextlib
import importlib.machinery
import importlib.util
import io
import pathlib

# Its subject is one tool and the source its markers point at, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
TOOL = REPO / "toolchain" / "bin" / "bd-chromium-race"
SUBMIT = REPO / "bulk_downloader" / "login_impl" / "submit.py"


def _load():
    """Load the extensionless, python-shebang tool as a module."""
    spec = importlib.util.spec_from_loader(
        "bd_chromium_race_under_test",
        importlib.machinery.SourceFileLoader(
            "bd_chromium_race_under_test", str(TOOL)),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _selftest_rc_and_output(mod) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = mod.selftest()
    return rc, buf.getvalue()


def test_the_tool_exists_and_parses():
    """PRECONDITION -- without it every assertion below is vacuous."""
    assert TOOL.is_file(), f"no bd-chromium-race at {TOOL}"
    ast.parse(TOOL.read_text(encoding="utf-8"))


def test_selftest_passes_on_the_real_tree():
    mod = _load()
    rc, out = _selftest_rc_and_output(mod)
    assert rc == 0, f"selftest failed on a clean tree:\n{out}"


def test_selftest_fails_on_the_historical_typo():
    """THE RED DIRECTION, using the string that actually cost two probe rounds.

    Not an invented mutation: `login: auto-submitted on fill` is the exact
    marker a 2026-08-14 probe grepped for. The phrase appears in submit.py only
    inside a RETURN MESSAGE, never in the emitted `login:` diagnostic line, so
    the probe's count was structurally pinned at 0.
    """
    mod = _load()
    mod.EARLY_MARKER = "login: auto-submitted on fill"
    rc, out = _selftest_rc_and_output(mod)
    assert rc == 1, (
        "the selftest passed while its early-return marker does not exist in "
        "submit.py. That marker's count would then be 0 forever, and a 0 that "
        "cannot be anything else reads as a measurement.")
    assert "does not appear in submit.py" in out, (
        f"the failure did not name the cause:\n{out}")


def test_selftest_fails_when_the_target_test_is_gone():
    """The other half of the denominator: the test it measures must exist."""
    mod = _load()
    mod.TARGET_TEST = "test_that_does_not_exist_anywhere"
    rc, out = _selftest_rc_and_output(mod)
    assert rc == 1, "the selftest passed with a target test that does not exist"
    assert "not found in" in out, f"the failure did not name the cause:\n{out}"


def test_the_markers_actually_resolve_against_submit_py():
    """Asserted here too, so the gate does not depend solely on the tool's own
    opinion of itself. If submit.py's diagnostics are reworded, this fails in a
    band rather than silently zeroing a future probe."""
    mod = _load()
    text = SUBMIT.read_text(encoding="utf-8", errors="replace")
    for name in ("FILLED_MARKER", "EARLY_MARKER", "WALK_MARKER"):
        marker = getattr(mod, name)
        assert marker in text, (
            f"{name} {marker!r} no longer appears in "
            f"bulk_downloader/login_impl/submit.py. Re-derive it from the "
            f"source rather than adjusting the probe to match a guess.")


def test_the_walk_marker_is_a_POSITIVE_observation_not_an_inference():
    """The walk must be observable directly, not deduced from a silence.

    THIS MARKER WAS ALMOST NOT ADDED, FOR THE EXACT REASON THE TOOL EXISTS. A
    first draft grepped `_submit_login`'s body for `login: ` -- with a space
    after the colon -- found nothing, and concluded the function emits no
    diagnostics, so the walk could only be INFERRED from `filled > early`. The
    walk's prefix is `login submit: `, and there are six such writes. The grep
    could not see its subject, and the conclusion drawn from it was a plain
    statement of fact. Inferring a walk from the absence of the early-return
    line is row 147's error; observing it is not.
    """
    mod = _load()
    assert hasattr(mod, "WALK_MARKER"), (
        "no positive walk marker. Deducing the walk from filled > early alone "
        "means a run that dies before either marker is indistinguishable from "
        "one that took the early return.")
    text = SUBMIT.read_text(encoding="utf-8", errors="replace")
    assert mod.WALK_MARKER in text, f"{mod.WALK_MARKER!r} not in submit.py"
    # And it must genuinely come from inside the walk, not from do_login.
    assert "login submit:" in mod.WALK_MARKER, (
        "the walk marker does not carry _submit_login's `login submit:` prefix, "
        "so it may be observing do_login instead of the flail")


def test_it_flags_disagreement_between_its_two_routes_to_the_same_fact():
    """Observed and inferred must be cross-checked, not silently trusted.

    The tool now derives 'did the walk happen' twice -- once by observing
    `login submit:` and once from filled > early. They should always agree. If
    they ever do not, a marker has drifted against submit.py and every count in
    that row is suspect, so the row records the disagreement rather than
    quietly preferring one.
    """
    src = TOOL.read_text(encoding="utf-8")
    assert "marker_disagreement" in src, (
        "the tool derives the walk two ways and does not record whether they "
        "agree. A disagreement is the signal that a marker has rotted, and it "
        "is exactly what a future reader will not think to check by hand.")


def test_it_declares_its_blind_spots_in_its_own_output():
    """An instrument's wrong answer arrives wearing the authority of a
    measurement, so what it cannot see belongs in the output, not a README."""
    mod = _load()
    assert getattr(mod, "BLIND_SPOTS", ()), "no BLIND_SPOTS declared"
    rc, out = _selftest_rc_and_output(mod)
    assert rc == 0
    for i, _ in enumerate(mod.BLIND_SPOTS, 1):
        assert f"BLIND SPOT {i}/" in out, (
            f"blind spot {i} is declared but never printed; a caveat the reader "
            "does not see is not a caveat")


def test_it_uses_the_project_interpreter_not_a_bare_python3():
    """CLAUDE.md section 5: `python3` here is 3.11 without the project deps, and
    a probe that silently falls back to it measures a different environment."""
    mod = _load()
    assert mod.PY.name == "python", f"unexpected interpreter: {mod.PY}"
    assert "venv" in str(mod.PY), (
        f"the tool does not pin the venv interpreter: {mod.PY}")
