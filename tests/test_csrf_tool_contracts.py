"""Current CSRF diagnostic-tool reachability and truthful-verdict contract.

tests/test_csrf_contract_reachability.py establishes the root-body contract and
proved it BICONDITIONALLY against measured reachability -- but its denominator
was `capture.sh` and nothing else. Three siblings under tools/ kept probing the
same deleted contract, invisibly to it. That is the exact denominator gap the
sibling gate's own docstring describes for the cookie leak, repeated one
directory over: a gate that cannot see the thing it is asked about reports OK.

WHAT WAS THERE, measured at retirement time:

  tools/diag_csrf_bootstrap.py   its whole stated purpose (docstring
                                 hypotheses A/B/C) was templates/index.html and
                                 _load_index_html, both deleted at v3.66.334.
                                 No sys.exit, no assert: print-only, exit 0
                                 always. It printed `NO -- code differs!` for a
                                 regex anchor that can never match again, and
                                 its "substitution failed, show me what
                                 happened" branch had become the only branch.
                                 RETIRED -- the file's reason to exist is gone.

  tools/diag_d2_fresh_bd_home.py recorded template_exists / template_has_marker
                                 / has_csrf_meta_tag / has_unsubst_marker and
                                 csrf_token_value into a JSON result. Constants
                                 now -- and csrf_token_value would have written
                                 a live CSRF token into a shipped diagnostic if
                                 the tag ever came back.

  tools/functional_probe.py      graded `bug` unless GET / carried csrf-token.
                                 Since the tag is gone that arm ALWAYS fired:
                                 the sole bug in an otherwise healthy 24-ok
                                 run, captioned "GET / failed: HTTP 200" --
                                 a message contradicted by its own status code.

The first two cannot fail; the third cannot pass. Both are section 0 failures,
and the crying-wolf one is the worse of the pair: CLAUDE.md calls
over-sensitivity a soundness bug rather than a safe default, because a gate
that fires on healthy code gets switched off.

WHY THESE ARE NOT STRING BANS. The source test below compares each probe
against MEASURED reachability -- the set of bodies GET / can actually return,
driven through the real app on both branches -- so it would permit these probes
again the day a reachable body could satisfy one. The functional_probe test is
behavioural: it RUNS the probe against a healthy app rather than asserting
anything about its spelling, which is also why it catches a defect no literal
ban would (that arm tests `csrf-token`, a fragment that is legitimate in the
X-CSRF-Token header name and so cannot be banned by spelling at all).

UNKNOWN IS A THIRD STATE: GET /'s 200 branch is measured only when a real built
artifact exists. A clean source checkout measures its explicit not-built 503
branch and does not certify a fabricated Vite stand-in.

AN EMPTY LIST IS NOT A CLEAN BILL. Both verdicts here rest on a list being
empty -- `not offenders` and `not graded_bug` -- and emptiness cannot tell
"nothing is wrong" from "the scan stopped looking". Two mutations proved that,
each leaving every arm above passing:

  * inverting the reachability polarity, so no tools/ source was ever scanned;
  * narrowing the finding pattern to crit-only, so a `bug` grade was invisible.

So each emptiness verdict now runs through a named helper --
_scan_for_retired_probes and _graded_defect_lines -- and each helper has a
POSITIVE CONTROL that hands it an input whose correct answer is non-empty by
construction. The control has to drive the SAME helper the verdict does: with
the controls in place but the assertions still using their own inline copies,
both mutations were re-measured and both still escaped. Certifying a copy is
this file's own subject, one level in.

The controls are not a meta-gate. They assert nothing about this file's text
and add no layer over the verdict -- they are the ordinary two-sided form the
gate already uses at the tools/-subdirectory reach check and at the root-load
`[ok` check, applied to the two arms that lacked it.

THE RESIDUE THAT LEFT, and how. A positive control only proves the instrument
sees what its AUTHOR THOUGHT TO PLANT, so a defect severity nobody planted
stayed invisible: _DEFECT_GRADE_RE hardcoded `(bug|crit)`, and a new failing
grade on _probe_lib's ladder updated Report.exit_code() while leaving this
grader blind, with every control here still passing. Measured by mutation --
adding a `fatal` grade to _SYM/_ORDER and to exit_code's inline expression left
three of this file's four tests green while `assert not graded_bug` had gone
vacuous.

The set is now DERIVED from _probe_lib.FAILING_GRADES, which Report.exit_code()
also consumes, so the two cannot disagree. Note what was NOT done: deriving it
from _probe_lib._ORDER, which is the LADDER and also holds info/ok/warn. That
would have made this gate report offenders on healthy probe output -- the
opposite soundness bug, and the specific way this fix goes wrong. Both
directions are held by the grade control below, which classifies each grade by
RUNNING a Report rather than by reading either side's spelling: every grade
_probe_lib really fails on must be seen, and every grade it does not must not.

run_tests.py conventions: repo root from __file__; no pytest builtins.
"""
from __future__ import annotations

import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from tools import _probe_lib

BD_GATE_SCOPE = "repo-wide"

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools"

# The three literals that can only be satisfied by the deleted Jinja shell.
# Deliberately NOT the bare fragment "csrf-token": that is legitimate inside the
# X-CSRF-Token header name, and a gate that fired on it would be making a false
# statement about its own input. The probe that uses the bare fragment is caught
# behaviourally instead, by test_functional_probe_does_not_cry_wolf_on_a_healthy_root.
RETIRED_PROBES = (
    '<meta name="csrf-token"',
    "{{ csrf_token }}",
    "<!--CSRF_META-->",
)


def _root_bodies() -> dict[str, bytes]:
    """Every body GET / can return, MEASURED by driving the real app.

    Re-measured here rather than imported from the sibling module: this gate's
    whole subject is a denominator that excluded its subject, so it derives its
    own reachability rather than inheriting one.
    """
    previous = os.environ.get("BD_DISABLE_KEEPALIVE")
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    try:
        import bulk_downloader.app as A
        saved = A._M2_DIST_ROOT
        out: dict[str, bytes] = {}
        with contextlib.ExitStack() as stack:
            absent = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            A._M2_DIST_ROOT = absent / "no-such-dist"
            with A.app.test_client() as c:
                out["dist-absent-503"] = c.get("/").data
            built = REPO / "frontend" / "dist" / "index.html"
            if built.is_file():
                A._M2_DIST_ROOT = built.parent
                with A.app.test_client() as c:
                    out["built-dist"] = c.get("/").data
        return out
    finally:
        if "A" in locals() and "saved" in locals():
            A._M2_DIST_ROOT = saved
        if previous is None:
            os.environ.pop("BD_DISABLE_KEEPALIVE", None)
        else:
            os.environ["BD_DISABLE_KEEPALIVE"] = previous


def _root_evidence_is_complete() -> tuple[bool, str]:
    if (REPO / "frontend" / "dist" / "index.html").is_file():
        return True, "measured the real built dist/index.html"
    return True, "measured only the explicit frontend-not-built branch; no built artifact claimed"


def _tool_sources() -> list[Path]:
    return [p for p in sorted(TOOLS.rglob("*.py"))
            if "__pycache__" not in p.parts]


def _rel(p: Path) -> str:
    """Repo-relative inside the repo, absolute outside it.

    The positive control plants its offender in a tmpdir, where
    `relative_to(REPO)` raises. Labelling is not this scan's subject, and a
    control that died on it would report a failure about the wrong thing.
    """
    try:
        return str(p.relative_to(REPO))
    except ValueError:
        return str(p)


def _scan_for_retired_probes(bodies: dict[str, bytes],
                             sources: list[Path]) -> list[str]:
    """Sources probing a RETIRED_PROBES literal that NO body in `bodies` carries.

    Extracted from the assertion below so that the real gate and the positive
    control run the SAME code. A control over a re-implementation certifies the
    copy and leaves the gate itself unmeasured -- which is this file's own
    subject, one level in. Measured: with the control in place but the gate
    still scanning through its own inline copy, both mutations below still
    escaped.
    """
    offenders: list[str] = []
    for probe in RETIRED_PROBES:
        reachable = [k for k, b in bodies.items() if probe.encode() in b]
        if reachable:
            continue  # a real contract again; probing it is legitimate
        for p in sources:
            text = p.read_text(encoding="utf-8", errors="replace")
            if probe in text:
                offenders.append(f"{_rel(p)} probes {probe!r}")
    return offenders


# `_probe_lib.Report.F` prints `  <sym> [<sev:4>] <name>`, so a grade of four
# characters or fewer is padded out to four columns and the closing bracket does
# not sit against the word, while a longer grade is flush. `\s*` covers both, and
# must stay: a grade added tomorrow need not be four characters.
#
# DERIVED from _probe_lib.FAILING_GRADES rather than hardcoded. The set that
# fails a probe run is defined once, in the lib, and Report.exit_code() reads
# that same constant -- so a new failing grade cannot update the exit status
# while leaving this grader behind, which is exactly what a hardcoded
# `(bug|crit)` did. NOT derived from _probe_lib._ORDER: that is the LADDER and
# carries info/ok/warn too, so it would make this grader fire on healthy probe
# output -- over-sensitivity is a soundness bug, not a safe default.
#
# An empty FAILING_GRADES would make this pattern blind, but it would equally
# make exit_code() always return 0; the two are coupled, and
# test_the_defect_grader_sees_exactly_the_grades_probe_lib_fails_on fails
# UNKNOWN on that tree rather than certifying it.
_DEFECT_GRADE_RE = re.compile(
    r"\[(" + "|".join(map(re.escape, _probe_lib.FAILING_GRADES)) + r")\s*\]")


def _graded_defect_lines(section: str) -> list[str]:
    """Lines in a probe section graded a defect -- _probe_lib.FAILING_GRADES.

    Extracted for the same reason as _scan_for_retired_probes: the assertion
    that no defect was graded and the control proving a defect CAN be seen must
    share one predicate, or the control certifies a copy.

    `warn` is deliberately not a defect here, and that is not this module's
    opinion -- it is _probe_lib's, read from FAILING_GRADES, and it is the same
    answer Report.exit_code() gives. The two-sided root-load assertion at the
    end of the behavioural test is what catches a downgrade to warn; if that
    responsibility ever moves, move it deliberately, not by widening this.
    """
    return [ln for ln in section.splitlines() if _DEFECT_GRADE_RE.search(ln)]


def test_no_tool_probes_a_root_contract_that_cannot_be_served():
    """A tools/ probe for the meta contract is allowed IFF a body can carry it."""
    ok, why = _root_evidence_is_complete()
    assert ok, f"cannot establish what GET / can serve, so UNKNOWN and FAIL: {why}"

    assert RETIRED_PROBES, (
        "RETIRED_PROBES is empty, so this test checks for nothing at all "
        "(UNKNOWN fails)")

    sources = _tool_sources()
    assert sources, (
        "no sources found under tools/ -- the denominator is empty, so this "
        "test would certify nothing (UNKNOWN fails)")
    # Non-empty is NOT enough. Narrowing rglob to glob leaves the denominator
    # populated (measured 244 -> 224 at v3.66.824) while silently dropping every
    # nested source -- tools/decomp, tools/code_intelligence, tools/audit -- and
    # an offender planted in one of them became invisible. Emptiness was the only
    # thing this assertion rejected; reach is the property that matters.
    assert any(p.parent != TOOLS for p in sources), (
        "the tools/ denominator contains no source from a SUBDIRECTORY, so the "
        "walk has stopped descending and every nested probe is invisible "
        "(UNKNOWN fails)")

    bodies = _root_bodies()
    assert bodies, "GET / returned no measurable bodies; reachability is UNKNOWN"

    # Through _scan_for_retired_probes, NOT an inline copy of it. The verdict
    # below is `assert not offenders`, and test_the_retired_probe_scan_can_see_
    # a_planted_offender is what makes an empty list mean "clean" rather than
    # "stopped looking" -- but only for the code that test actually drives.
    offenders = _scan_for_retired_probes(bodies, sources)

    assert not offenders, (
        "these tools/ sources probe a contract no reachable GET / body can "
        f"serve ({why}), so the probe is a constant rather than a check:\n  "
        + "\n  ".join(sorted(offenders)))


def test_functional_probe_does_not_cry_wolf_on_a_healthy_root():
    """Behavioural: the probe is RUN, and must not grade a healthy root a bug.

    Asserting about functional_probe's source would be the presence-not-
    behaviour class this contract already rejects, and could not see the defect
    anyway -- the offending arm tests the fragment `csrf-token`, which is
    legitimate spelling inside the X-CSRF-Token header name.

    The subject is F.1 alone. Findings from other sections are deliberately NOT
    asserted on: they have their own subjects, and folding them in here would
    make this gate fire for reasons it cannot describe.
    """
    r = subprocess.run(
        [sys.executable, str(TOOLS / "functional_probe.py")],
        cwd=str(REPO), capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr

    header = "F.1 - CSRF bootstrap"
    # "=== F.1 " -- the delimiter AND the trailing space are both load-bearing.
    # The bare substring "F.1" is a PREFIX of the F.10, F.11 and F.12 headers,
    # so searching for it slid the window onto the diagnostics-bundle section
    # whenever the real F.1 header was absent, and the UNKNOWN arm below then
    # never fired: the gate asserted over a window containing none of its
    # subject. Measured by mutation at v3.66.824, in this very test.
    start = out.find("=== F.1 ")
    assert start != -1, (
        "functional_probe printed no F.1 section, so its CSRF arm did not run "
        f"and this test would certify nothing (UNKNOWN fails). output:\n{out[:2000]}")
    nxt = out.find("=== F.2", start)
    assert nxt != -1, (
        "no F.2 section follows F.1, so the window this test asserts over is "
        "unbounded and would silently swallow every later section (UNKNOWN "
        f"fails). output:\n{out[:2000]}")
    section = out[start:nxt]

    # The window must contain F.1's OWN subject. Without this, a mis-sliced or
    # empty window trivially satisfies the no-bug assertion below -- passing by
    # examining nothing, which is the failure this whole file exists to close.
    assert "/api/csrf" in section, (
        "the F.1 window does not mention /api/csrf, so it is not F.1's output "
        f"and nothing was actually checked (UNKNOWN fails). window:\n{section}")

    # Through _graded_defect_lines, NOT an inline copy of it -- the positive
    # control at the end of this function drives that helper, and a control
    # over a second copy of the predicate would certify the copy.
    graded_bug = _graded_defect_lines(section)
    assert not graded_bug, (
        f"functional_probe grades an expected root state a defect ({header}). "
        f"The CSRF token ships via "
        f"GET /api/csrf, not an HTML meta tag. Offending findings:\n  "
        + "\n  ".join(graded_bug))

    # TWO-SIDED. "no bug" alone is satisfied by an arm that grades nothing at
    # all, or downgrades to warn -- both measured as escapes at v3.66.824. F.1
    # must positively report the root load as ok.
    # Scoped to the ROOT-LOAD finding specifically, not to the window. The first
    # version of this assertion accepted any [ok] line in F.1 -- and F.1 also
    # grades /api/csrf, so downgrading the root-load arm to info still passed.
    # The check's subject was the root load; its denominator was the whole
    # section. That is the same defect this file exists to catch, committed
    # inside the fix for it, and caught the same way: by mutation.
    root_findings = [ln for ln in section.splitlines() if "GET /" in ln]
    assert root_findings, (
        "F.1 reports no finding about GET / at all, so the root-load arm "
        f"produced nothing and nothing was checked (UNKNOWN fails). window:\n{section}")
    if (REPO / "frontend" / "dist" / "index.html").is_file():
        assert any("[ok" in ln for ln in root_findings), (
            "F.1's GET / finding is not graded ok with a built SPA:\n  "
            + "\n  ".join(root_findings))
    else:
        assert any("[info" in ln and "frontend-not-built" in ln
                   and "503" in ln for ln in root_findings), (
            "F.1 did not report the explicit frontend-not-built 503 state; "
            "a clean source checkout must be distinguished from a broken "
            "built deployment:\n  " + "\n  ".join(root_findings))

    # POSITIVE CONTROL for the no-defect assertion above. `assert not
    # graded_bug` is satisfied by a grader that has gone blind, and an empty
    # list cannot tell "clean" from "I stopped looking": narrowing the pattern
    # to crit-only left every arm above passing, measured by mutation.
    #
    # The plant is made from a line the probe REALLY printed rather than a
    # hand-spelled imitation, so the control cannot certify a format the probe
    # stopped using. If Report.F's spelling ever changes, the re-grade below
    # produces an unchanged line and this FAILS -- unknown is a third state.
    source_line = root_findings[0]
    for sev in ("bug ", "crit"):
        forged = re.sub(r"\[[a-z]+\s*\]", f"[{sev}]", source_line, count=1)
        assert forged != source_line, (
            "could not re-grade a real root finding, so this control planted "
            "nothing and the no-defect assertion above stayed unmeasured "
            f"(UNKNOWN fails). probe printed: {source_line!r}")
        assert _graded_defect_lines(forged) == [forged], (
            f"the defect grader cannot see a [{sev.strip()}] finding in the "
            "probe's OWN output format, so the no-defect assertion above was "
            f"vacuous rather than clean. planted line: {forged!r}")


def test_the_retired_probe_scan_can_see_a_planted_offender():
    """POSITIVE CONTROL for test_no_tool_probes_a_root_contract_that_cannot_be_served.

    That test's verdict is `assert not offenders`, and an empty list is
    ambiguous between "no tools/ source probes a dead contract" and "the scan
    stopped looking". Inverting the reachability polarity produced the second
    while reading as the first, and escaped, measured by mutation.

    So the scan is driven over a source whose correct verdict is known by
    construction, and it is driven BOTH ways: an unreachable probe must be
    reported, and the same probe must be spared the moment a body can carry it.
    One-sided would re-admit the polarity flip from the other direction.

    Synthetic bodies, not the measured ones: the subject here is the scan's
    predicate, so the input has to be known rather than observed. Reachability
    against the real app is the other test's subject and stays there.
    """
    d = Path(tempfile.mkdtemp())
    planted = d / "planted_offender.py"
    innocent = d / "innocent.py"
    innocent.write_text("MARKER = 'no retired probe here'\n", encoding="utf-8")
    sources = [planted, innocent]

    assert RETIRED_PROBES, "nothing to plant, so this control proves nothing"
    for probe in RETIRED_PROBES:
        planted.write_text(f"MARKER = {probe!r}\n", encoding="utf-8")

        unreachable = {"dist-absent-503": b"<h1>installer</h1>"}
        found = _scan_for_retired_probes(unreachable, sources)
        assert [f for f in found if "planted_offender.py" in f], (
            f"the scan did not report a source probing {probe!r} when no body "
            "can carry it, so `assert not offenders` on the real tree means "
            f"nothing was looked at. returned: {found}")
        assert not [f for f in found if "innocent.py" in f], (
            "the scan reported a source that contains no retired probe, so it "
            f"fires on identity rather than on its subject. returned: {found}")

        served = {"dist-absent-503": b"<h1>installer</h1>",
                  "built-dist": probe.encode() + b" is served again"}
        assert not _scan_for_retired_probes(served, sources), (
            f"the scan reported {probe!r} as retired while a reachable body "
            "carries it. The contract is live again, so probing it is exactly "
            "what a tool should do -- this gate would be banning a spelling.")


def test_the_defect_grader_sees_exactly_the_grades_probe_lib_fails_on():
    """The defect grader matches a line IFF _probe_lib really fails the run on it.

    INSTRUMENT: a RUNNING _probe_lib.Report -- one fresh Report per grade in
    _probe_lib._ORDER, graded through Report.F, with the judged line captured
    off stdout. That is the same idiom as the positive control above: the line
    is one the probe REALLY printed, not a hand-spelled imitation, so this
    cannot certify a format Report.F has stopped using.
    PREDICATE: Report.exit_code() == 1 on a report holding exactly one finding
    of that grade -- the shipped definition of "this grade fails a probe run",
    read from behaviour rather than from either side's spelling.

    Why not `assert FAILING_GRADES == ("bug", "crit")`: that is the presence-
    not-behaviour class this file already rejects. It certifies a literal, and
    would keep passing if exit_code stopped consulting the constant, or if this
    module's regex were built from something else entirely. This drives BOTH
    consumers -- the real exit_code() and this module's real pattern -- over
    every grade in the ladder, so a divergence between them on ANY EXISTING
    grade fails here. It does not prove they can never disagree: a future
    grade added to _SYM/_ORDER but not to FAILING_GRADES is consistent for
    both consumers and correctly invisible to both. Say the weaker true
    thing.

    BOTH DIRECTIONS ARE MANDATORY, because the two failure modes are opposite
    and each looks fine from the other's side:

      * blindness -- a grade that fails a run but the grader cannot see makes
        `assert not graded_bug` above vacuous. This is the residue the previous
        cut recorded honestly: a positive control only proves the instrument
        sees what its author thought to plant, so a NEW failing grade added to
        the ladder escaped every control in this file.
      * over-sensitivity -- and this is the specific way THIS fix goes wrong.
        _ORDER is the LADDER, not the failing set: it also holds info, ok and
        warn. A grader derived from _ORDER would match the [ok  ] and [info]
        lines every healthy probe prints, and CLAUDE.md calls a gate that cries
        wolf a soundness bug rather than a safe default.
    """
    ladder = tuple(_probe_lib._ORDER)
    assert ladder, (
        "_probe_lib._ORDER is empty, so there is no severity ladder to measure "
        "and this control would certify nothing (UNKNOWN fails)")

    failing: list[tuple[str, str]] = []
    passing: list[tuple[str, str]] = []
    for grade in ladder:
        assert grade in _probe_lib._SYM, (
            f"_ORDER lists {grade!r} but _SYM carries no symbol for it, so "
            "Report.F raises on it and the ladder cannot be graded at all "
            "(UNKNOWN fails)")
        rep = _probe_lib.Report("GRADE-CONTROL")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rep.F(grade, "GET / control finding")
        printed = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        assert len(printed) == 1, (
            f"grading {grade!r} printed {len(printed)} lines rather than one, "
            "so this control does not know which line to judge (UNKNOWN "
            f"fails): {printed!r}")
        code = rep.exit_code()
        assert code in (0, 1), (
            f"exit_code() returned {code!r} for a report holding one {grade!r} "
            "finding, so the grade cannot be classified as failing or not "
            "(UNKNOWN fails)")
        (failing if code == 1 else passing).append((grade, printed[0]))

    assert failing, (
        "no grade on _probe_lib's ladder makes exit_code() report failure, so "
        "there is nothing for the defect grader to be blind TO and the "
        "blindness direction below asserts over an empty set (UNKNOWN fails)")
    assert passing, (
        "every grade on _probe_lib's ladder makes exit_code() report failure, "
        "so the over-sensitivity direction below has no witness and a grader "
        "matching every line would pass unnoticed (UNKNOWN fails)")

    for grade, line in failing:
        assert _graded_defect_lines(line) == [line], (
            f"_probe_lib fails a probe run on a {grade!r} finding, but the "
            "defect grader does not see the line the probe really printed for "
            "it. `assert not graded_bug` in the behavioural test above is "
            f"therefore vacuous for {grade!r}. probe printed: {line!r}")

    for grade, line in passing:
        assert _graded_defect_lines(line) == [], (
            f"the defect grader reports a {grade!r} finding as a defect while "
            "_probe_lib exits 0 on it, so the behavioural test above would "
            "fire on a healthy probe run. Over-sensitivity is a soundness bug, "
            f"not a safe default. probe printed: {line!r}")
