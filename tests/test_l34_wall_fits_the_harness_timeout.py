"""L34 plans against a wall the harness will not give it.

THE DEFECT. Two numbers describe the same budget and neither knows about the
other:

    live_tests/checks.py:364    _L34_WALL_S = 72.0   # "of the harness's 90s"
    live_tests/harness.py:61    DEFAULT_PER_CHECK_TIMEOUT_S = 60.0

The comment is true of exactly one caller. `capture.sh:913` passes
`--per-check-timeout 90`, and that is the ONLY 90 in the tree. Every other way
of reaching L34 takes run.py's default, which is harness's 60.0 -- and that is
not only the ad-hoc `-m live_tests.run --only L34` an operator types to re-check
one thing. `tools/install_livecheck_timer.sh:152` writes a systemd unit whose
ExecStart passes `--per-check-timeout ${PER_CHECK_TIMEOUT}`, defaulted to `"60"`
at line 64. So the SCHEDULED, unattended run of the live checks is a 60s run.
Nothing in the tree tested that installer -- `git grep -ln
install_livecheck_timer -- tests/` returns nothing -- so the one invocation of
L34 that happens without anybody watching was also the one nobody had asserted
about.

There, 72 > 60: L34 believes it has a 72s wall, `harness.py:243-249` kills the
thread at 60s, and the result is

    FAIL  L34  full-route-smoke  --  TIMEOUT after 60.0s (limit 60.0s) --
          the check did not return in time; thread leaked

The verdict is not merely wrong, it is *unreachable*: four cuts of machinery
built specifically so L34 would stop overrunning -- the wall (v3.66.740), the
phase-1 deadline honouring the phase-2 reserve (741), the triage budget (744) and
the advisory diagnostic pass (746) -- all measure themselves against a wall the
harness has already withdrawn. Worst-case elapsed, derived from the real
constants rather than assumed:

    phase 1 stops SUBMITTING at   _L34_WALL_S * (1 - 0.45)          = 39.6s
    in-flight drain adds up to    _L34_TRIAGE_BUDGET_S + 2          =  7.0s
    phase 2 starts a probe while  _left() >= _L34_ROUTE_BUDGET_S+2  -> <= 62.0s
      and that probe may run      _L34_ROUTE_BUDGET_S               =  8.0s
    diagnostics start while       _left() >= _L34_TRIAGE_BUDGET_S+1 -> <= 66.0s
      and that probe may run      _L34_TRIAGE_BUDGET_S              =  5.0s
                                                                     -------
    reachable ceiling                                                ~71.0s

71 fits inside 72 and does not fit inside 60. The overrun is not hypothetical
slack -- it is what the code is written to spend when the app gives it enough
suspects to re-probe.

WHY THE EXISTING GATE DID NOT CATCH IT.
`tests/test_u45_capture_sh_shipped.py:184` asserts every `--per-check-timeout N`
in capture.sh has `N >= 90`, and it passes. But `_read_capture_sh()` is its whole
denominator, so harness.py's 60.0 and run.py's inherited default are structurally
outside what it can see. It answers "is capture.sh generous enough" truthfully
and says nothing about the property that matters, which is whether L34's wall
fits the timeout ACTUALLY IN FORCE. CLAUDE.md section 0, in the shape it keeps
taking: the check is honest, the denominator excludes the subject.

The 13 references that monkeypatch `_L34_WALL_S` do not catch it either, and
cannot: every one replaces the wall with a small number (1.5 to 8.0) so the check
finishes inside a test. The real 72.0 is invisible to all of them by
construction. That is not a criticism of those tests -- they are testing the
wall-aware LOGIC, correctly -- it is the observation that nothing in the suite
was looking at the wall's RELATIONSHIP to the timeout.

THE FIX IS TO DERIVE, NOT TO RE-ASSERT. Raising harness's default to 90 would
make today's two numbers agree and would leave the same latent trap: two
independent literals, no relationship, drifting apart again the next time either
moves. Instead the harness tells each Context the timeout it is actually running
under, and L34 asks:

    _l34_wall_s(ctx) == min(_L34_WALL_S, effective_timeout * _L34_WALL_FRACTION)

Under capture.sh that is `min(72.0, 90*0.8) == 72.0` -- byte-for-byte today's
behaviour, so the box's capture is unaffected. Under the 60s default it is
`min(72.0, 48.0) == 48.0`, which fits, so L34 returns a verdict instead of being
killed. `min` is deliberate in both directions: it never grants MORE wall than
72s has been exercised with, and it preserves the monkeypatch seam, because every
patched value in the suite is below the 48.0 floor and `min` therefore returns it
unchanged.

WHAT THIS CUT DOES NOT DO, stated so it is not read as more than it is:

  * It does not change the capture. capture.sh passes 90 and keeps passing 90;
    the derived wall there is still 72.0. The paths this repairs are the 60s
    ones: the installed daily timer, and the by-hand re-check of a single check
    -- where a phantom TIMEOUT would send an operator chasing a code defect that
    is not there.
  * It does not scale the wall UP when the timeout is generous. `--per-check-
    timeout 300` still gives 72s, because 72 is the largest wall this check has
    ever been exercised at and widening it is a separate decision with its own
    evidence.
  * It does not make L34 pass where it was failing. With a 48s wall on the
    default path, routes phase 1 cannot reach are UNPROBED and suspects phase 2
    cannot re-probe are UNCONFIRMED -- both UNKNOWN, and unknown FAILS. The
    change is from an uninformative "thread leaked" to a named list of what was
    not measured. That is the improvement being claimed, and it is the only one.

RED-first: R1, R2, R3 and R4 fail on pristine source.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_tests import checks, harness  # noqa: E402

_CHECKS_PY = ROOT / "live_tests" / "checks.py"
_HARNESS_PY = ROOT / "live_tests" / "harness.py"
_RUN_PY = ROOT / "live_tests" / "run.py"
_CAPTURE_SH = ROOT / "capture.sh"
_LIVECHECK_SH = ROOT / "tools" / "install_livecheck_timer.sh"

# The margin the wall must leave under the timeout. Not cosmetic: harness.py
# measures the timeout around the WHOLE call including Context setup and the
# final artifact write, so a wall equal to the timeout is a wall that loses a
# coin flip.
_MIN_MARGIN_S = 5.0


# ── the denominator, derived ─────────────────────────────────────────────────

def _harness_default() -> float:
    """The value run_all uses when no caller passes one. Read from the imported
    module, not from source text: the running value is the subject."""
    return float(harness.DEFAULT_PER_CHECK_TIMEOUT_S)


def _run_py_cli_default() -> float:
    """run.py's argparse default for --per-check-timeout, by AST.

    Not a regex. The default is an EXPRESSION
    (`harness.DEFAULT_PER_CHECK_TIMEOUT_S`), not a literal, so a textual scan
    would either miss it or report the identifier as the number. The AST finds
    the add_argument call; resolving the expression is then a lookup against the
    real module rather than a guess about what it says.
    """
    tree = ast.parse(_RUN_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "add_argument"):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "--per-check-timeout"):
            continue
        for kw in node.keywords:
            if kw.arg != "default":
                continue
            src = ast.unparse(kw.value)
            if src.endswith("DEFAULT_PER_CHECK_TIMEOUT_S"):
                return _harness_default()
            try:
                return float(ast.literal_eval(kw.value))
            except Exception:
                raise AssertionError(
                    f"run.py's --per-check-timeout default is the expression "
                    f"{src!r}, which this gate cannot resolve. Resolve it here "
                    f"rather than skipping it -- an unresolvable binding site "
                    f"is UNKNOWN, and unknown must fail, not vanish from the "
                    f"denominator.")
        return _harness_default()  # no explicit default -> argparse gives None
    raise AssertionError(
        "no add_argument('--per-check-timeout', ...) call found in "
        "live_tests/run.py. Either the flag was renamed -- in which case this "
        "gate's denominator no longer contains its subject and must be "
        "updated -- or the AST predicate broke.")


def _capture_sh_values() -> list[float]:
    """Every literal --per-check-timeout in the shipped capture script."""
    return [v for _ln, v, _c in _capture_sh_occurrences()]


def _capture_sh_occurrences() -> list[tuple[int, float, bool]]:
    """(lineno, value, is_comment) for each --per-check-timeout in capture.sh.

    Comment lines are reported, not dropped. capture.sh:48 documents the flag in
    prose and :913 passes it; both are 90 today, and a gate that saw only one of
    them could not notice them disagreeing. `is_comment` is carried so the
    printed denominator says which is which rather than calling a comment a
    binding site.
    """
    out = []
    for i, line in enumerate(
            _CAPTURE_SH.read_text(encoding="utf-8", errors="replace")
            .splitlines(), start=1):
        for m in re.finditer(r"--per-check-timeout[ =](\d+(?:\.\d+)?)", line):
            out.append((i, float(m.group(1)), line.lstrip().startswith("#")))
    return out


def _livecheck_timer_value() -> float:
    """The timeout baked into the systemd unit that runs the live checks daily.

    `tools/install_livecheck_timer.sh:152` writes

        ExecStart=${PYEXE} -m live_tests.run ... --per-check-timeout ${PER_CHECK_TIMEOUT}

    and `PER_CHECK_TIMEOUT="60"` at line 64. So there is a SHIPPED, SCHEDULED
    invocation of L34 under a 60s wall -- not merely an ad-hoc operator command.
    An installed timer would have reported `TIMEOUT ... thread leaked` for L34
    every morning it ran.

    The value arrives through a shell variable, so the capture.sh regex finds
    nothing here. Extending that regex to this file would have added ZERO sites
    and reported a wider denominator than it had -- the exact failure this gate
    exists to prevent. The variable is resolved, and an unresolvable one raises:
    UNKNOWN is a third state and it fails.
    """
    body = _LIVECHECK_SH.read_text(encoding="utf-8", errors="replace")
    refs = re.findall(r"--per-check-timeout[ =](\S+)", body)
    # The installer's own argument parser also contains the flag as a `case`
    # label; keep only references that carry a value we can resolve.
    resolved = []
    for raw in refs:
        tok = raw.strip('"').strip("'")
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            resolved.append(float(tok))
            continue
        m = re.fullmatch(r"\$\{?(\w+)\}?", tok)
        if not m:
            continue  # e.g. the `"$2"` in the installer's own arg parser
        var = m.group(1)
        assign = re.search(rf'^{var}="?(\d+(?:\.\d+)?)"?\s*$', body, re.M)
        assert assign, (
            f"{_LIVECHECK_SH.name} passes --per-check-timeout {tok} but no "
            f"literal default for {var} could be found in the script. That "
            f"makes the timeout this shipped systemd unit enforces UNKNOWN, and "
            f"an unresolvable binding site must fail rather than silently drop "
            f"out of the denominator.")
        resolved.append(float(assign.group(1)))
    assert resolved, (
        f"{_LIVECHECK_SH.name} no longer passes --per-check-timeout to "
        f"live_tests.run. If the installer stopped setting it the unit would "
        f"inherit run.py's default; if it stopped invoking live_tests.run at "
        f"all this function must be removed rather than left asserting over an "
        f"empty set.")
    return min(resolved)


def _binding_sites() -> dict[str, float]:
    """Every effective per-check timeout L34 can actually run under.

    THIS is the denominator the old gate was missing. It contains harness's
    default and run.py's default -- the two the old gate could not see -- plus
    capture.sh's literals, which it could, plus the systemd unit written by
    tools/install_livecheck_timer.sh, which NOTHING saw: no test in the tree
    references that installer at all (`git grep -ln install_livecheck_timer --
    tests/` is empty), and it is the one binding site that runs L34
    unattended on a schedule.
    """
    sites = {
        "harness.DEFAULT_PER_CHECK_TIMEOUT_S": _harness_default(),
        "live_tests/run.py --per-check-timeout default": _run_py_cli_default(),
        "tools/install_livecheck_timer.sh systemd unit":
            _livecheck_timer_value(),
    }
    for ln, v, is_comment in _capture_sh_occurrences():
        kind = "comment" if is_comment else "flag"
        sites[f"capture.sh:{ln} --per-check-timeout ({kind})"] = v
    return sites


_OPERATOR_ROUTES = 40
_DIAGNOSTIC_ROUTES = 8


class _Ctx(harness.Context):
    """A real Context with the HTTP layer stubbed.

    Subclassed, never reimplemented: the whole point of this cut is that the
    timeout travels on the Context, so a test that invents its own Context
    would be asserting over an object the harness does not build. That mistake
    was made once already, in the first draft of L11's gate.

    `/api/dev/routes` is SERVED, not stubbed to a failure. The first draft
    returned 503 for every path including that one, so L34 hit its
    `could not GET /api/dev/routes` early return and came back in ~0ms -- the
    end-to-end timing assertion below would have passed without the check ever
    entering a single phase. A gate that its subject exits before reaching is
    the section 0 defect in the fixture rather than in the code.
    """

    def __init__(self, *, per_check_timeout_s=None, latency_s=0.0):
        kw = {}
        if per_check_timeout_s is not None:
            kw["per_check_timeout_s"] = per_check_timeout_s
        super().__init__("http://ctx.invalid", str(ROOT), disruptive=False, **kw)
        self.latency_s = latency_s
        self.messages: list[str] = []
        self.probed: list[str] = []

    def log(self, msg):
        self.messages.append(str(msg))

    def get(self, path, timeout=15):
        if path == "/api/dev/routes":
            routes = [{"rule": f"/api/thing/{i}", "methods": ["GET"]}
                      for i in range(_OPERATOR_ROUTES)]
            routes += [{"rule": f"/api/dev/probe/{i}", "methods": ["GET"]}
                       for i in range(_DIAGNOSTIC_ROUTES)]
            return True, 200, {"routes": routes}, 1.0
        self.probed.append(path)
        if self.latency_s:
            time.sleep(min(self.latency_s, timeout))
        # 503 so every route is a phase-1 suspect: that is what drives the
        # check into phase 2 and the diagnostic pass rather than straight to a
        # clean PASS with nothing exercised.
        return True, 503, None, self.latency_s * 1000.0


# ── canaries: a vacuous pass here would be worse than no gate ────────────────

def test_the_denominator_contains_more_than_capture_sh():
    """CANARY.

    The defect this file exists for is a gate whose denominator was capture.sh
    alone. If this gate's own derivation collapses to capture.sh -- or to
    nothing -- it has inherited the flaw it was written to close, and it must
    say so rather than report OK.
    """
    sites = _binding_sites()
    assert len(sites) >= 4, (
        f"derived only {len(sites)} binding site(s): {sites}. Expected at "
        f"least four -- harness's default, run.py's CLI default, the systemd "
        f"unit tools/install_livecheck_timer.sh writes, and at least one "
        f"capture.sh literal. Fewer means a predicate stopped matching, and a "
        f"shrinking denominator reports OK for the wrong reason.")
    assert any(k.startswith("capture.sh") for k in sites), (
        f"no capture.sh literal in the denominator: {sites}")
    assert any("run.py" in k for k in sites), (
        f"run.py's CLI default is not in the denominator: {sites}")
    assert any("install_livecheck_timer" in k for k in sites), (
        f"the scheduled systemd invocation is not in the denominator: {sites}. "
        f"It is the only binding site that runs L34 unattended, and no test in "
        f"the tree referenced that installer before this one.")


def test_the_helper_exists_and_is_callable():
    """Named separately so the failure below is 'the fix is absent', not an
    AttributeError inside an assertion about arithmetic."""
    assert hasattr(checks, "_l34_wall_s"), (
        "live_tests/checks.py has no _l34_wall_s(ctx). L34's wall is still the "
        "bare module constant _L34_WALL_S, which cannot know what timeout the "
        "harness is enforcing.")
    assert hasattr(checks, "_L34_WALL_FRACTION"), (
        "checks._L34_WALL_FRACTION is absent -- the fraction of the harness "
        "timeout the wall may occupy must be a declared constant, not a "
        "literal buried in an expression.")


# ── R1: the relationship, at every binding site ──────────────────────────────

def test_the_wall_fits_every_binding_site():
    """R1 -- THE DEFECT.

    Fails on pristine source at the harness default: the wall is 72.0 and the
    timeout is 60.0.
    """
    offenders = []
    for name, limit in sorted(_binding_sites().items()):
        wall = checks._l34_wall_s(_Ctx(per_check_timeout_s=limit))
        if wall > limit - _MIN_MARGIN_S:
            offenders.append(
                f"{name}: timeout={limit:.1f}s but L34 plans a "
                f"{wall:.1f}s wall (needs <= {limit - _MIN_MARGIN_S:.1f}s)")
    assert not offenders, (
        "L34's wall does not fit the per-check timeout at these binding "
        "sites:\n  " + "\n  ".join(offenders) +
        f"\nharness.py:243-249 kills the check's thread at the timeout and "
        f"records FAIL 'TIMEOUT ... thread leaked'. A wall above the timeout "
        f"is a budget the harness has already withdrawn, so every wall-aware "
        f"branch in l34_full_route_smoke is unreachable on that path.")


def test_the_wall_fits_when_the_context_says_nothing():
    """R2 -- the fallback path.

    A Context built without a timeout (every fake context in the suite, and any
    caller that constructs one directly) must still get a wall that fits the
    default the harness will enforce, because that is the timeout it will get.
    """
    wall = checks._l34_wall_s(_Ctx())
    limit = _harness_default()
    assert wall <= limit - _MIN_MARGIN_S, (
        f"a Context carrying no timeout produced a {wall:.1f}s wall against "
        f"the {limit:.1f}s default the harness would actually enforce. The "
        f"fallback must assume the default, not the most generous caller.")


def test_l34_reads_the_derived_wall_and_not_the_bare_constant():
    """R3 -- structural, so arithmetic coincidence cannot satisfy it.

    A fix that computed the right number and then went on reading
    `_L34_WALL_S` directly in the closures would pass R1 and change nothing.
    Asserted over the function's AST -- `ast.unparse` of the FunctionDef, never
    a fixed-width source window (see test_source_windows_do_not_shift.py: three
    false failures in one session were caused by exactly that shortcut).
    """
    tree = ast.parse(_CHECKS_PY.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef)
               and n.name == "l34_full_route_smoke"), None)
    assert fn is not None, "l34_full_route_smoke not found in checks.py"
    bare = [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Name) and n.id == "_L34_WALL_S"]
    assert not bare, (
        f"l34_full_route_smoke still references the module constant "
        f"_L34_WALL_S directly at line(s) {bare}. Every use inside the check "
        f"must go through the wall derived from the Context, or the derivation "
        f"is decorative: the closures would keep measuring against 72.0 no "
        f"matter what the harness is enforcing.")


def test_the_hard_ceiling_is_reachable_at_some_binding_site():
    """R4 -- the ceiling must mean something.

    `min(_L34_WALL_S, limit * fraction)` makes the constant a CEILING. A ceiling
    above every value the derivation can produce is dead code that reads as a
    live budget -- and raising `_L34_WALL_S` to 200 would then be invisible to
    every other assertion here. Requiring it to bind at the most generous
    binding site keeps it honest: today capture.sh's 90 * 0.8 == 72.0 exactly.
    """
    sites = _binding_sites()
    best = max(sites.values())
    derived_at_best = best * checks._L34_WALL_FRACTION
    assert checks._L34_WALL_S <= derived_at_best + 1e-6, (
        f"_L34_WALL_S is {checks._L34_WALL_S:.1f}s but the most generous "
        f"binding site ({best:.1f}s) derives only {derived_at_best:.1f}s, so "
        f"the constant can never bind. An unreachable ceiling is a number "
        f"nobody is checking: it would absorb any future increase silently.")


# ── the monkeypatch seam the four existing L34 suites depend on ──────────────

@pytest.mark.parametrize("patched", [1.5, 2.0, 5.0, 6.0, 8.0])
def test_patching_the_constant_still_governs_the_wall(monkeypatch, patched):
    """THE BLAST RADIUS OF MY OWN FIX.

    13 references across test_v3_66_740/741/744/746 do
    `monkeypatch.setattr(checks, "_L34_WALL_S", <small>)` to make the check
    finish inside a test. If the derivation stopped consulting that attribute,
    every one of those patches would become INERT -- and an inert monkeypatch
    does not fail, it makes the test assert nothing while still reporting
    green. That is the section 0 defect, reintroduced by the fix for the
    section 0 defect, which is why `min` was chosen over an unconditional
    `limit * fraction`.

    The parametrised values are the real ones used by those four files.
    """
    monkeypatch.setattr(checks, "_L34_WALL_S", patched)
    got = checks._l34_wall_s(_Ctx())
    assert got == pytest.approx(patched), (
        f"patching _L34_WALL_S to {patched} produced a wall of {got}. The four "
        f"existing L34 suites steer the check entirely through this attribute; "
        f"if the derivation ignores it they all keep passing while exercising "
        f"the production wall instead of the one they set.")


def test_a_patch_above_the_derived_floor_is_clamped_and_says_so(monkeypatch):
    """The one place `min` changes a caller's meaning, made explicit.

    A patched value ABOVE the derived floor gets clamped -- so a future test
    patching 90.0 under a 60s default would silently run at 48.0. Nothing in
    the suite does that today (the largest is 8.0), but the clamp must be
    observable rather than silent, or it becomes the next invisible denominator.

    The in-range baseline has to be genuinely in range. The first draft used the
    production 72.0 as its "quiet" control, which under a 60s default IS
    clamped -- so both halves logged, the comparison was 1 > 1, and the
    assertion failed for the right reason on a test that was wrong. Recorded
    because it is the same mistake in miniature: a control that does not control.
    """
    floor = _harness_default() * checks._L34_WALL_FRACTION

    ctx_in = _Ctx()
    monkeypatch.setattr(checks, "_L34_WALL_S", floor - 10.0)
    got_in = checks._l34_wall_s(ctx_in)
    assert got_in == pytest.approx(floor - 10.0), (
        f"a wall of {floor - 10.0} fits inside the derived {floor} and must be "
        f"returned unchanged; got {got_in}")

    ctx_out = _Ctx()
    monkeypatch.setattr(checks, "_L34_WALL_S", floor + 100.0)
    got_out = checks._l34_wall_s(ctx_out)
    assert got_out == pytest.approx(floor), (
        f"a wall of {floor + 100.0} under a {_harness_default()}s timeout was "
        f"not clamped to {floor} -- it returned {got_out}")
    assert len(ctx_out.messages) > len(ctx_in.messages), (
        f"the wall was clamped from {floor + 100.0} to {floor} and nothing was "
        f"logged (in-range logged {len(ctx_in.messages)}, clamped logged "
        f"{len(ctx_out.messages)}). A budget silently reduced is a budget "
        f"nobody can account for; the operator reading a long UNPROBED list "
        f"must be able to see which of the two numbers bound.")


# ── end to end: the check now returns instead of being killed ────────────────

def test_the_context_timeout_bounds_the_check_not_the_constant(monkeypatch):
    """THE PROPERTY, OBSERVED END TO END -- and the constant deliberately put
    out of reach so it cannot be what stopped the check.

    `_L34_WALL_S` is patched to 100.0, far above anything the ctx can derive. If
    the check is still steered by the module constant it runs for ~100s; if it
    is steered by the Context it stops at `min(100.0, 5.0 * 0.8) == 4.0`. So the
    timing assertion distinguishes the fix from its absence, which a plain
    "returns quickly" assertion would not: on pristine source there is no
    derivation at all and 100.0 would bind.

    Two further assertions stop this passing vacuously. The check must have
    enumerated the served routes, and it must have actually probed some of them
    -- otherwise an early return (the `could not GET /api/dev/routes` path, or
    an exception) would satisfy the clock and prove nothing.
    """
    monkeypatch.setattr(checks, "_L34_WALL_S", 100.0)
    ctx = _Ctx(per_check_timeout_s=5.0, latency_s=0.05)
    derived = checks._l34_wall_s(ctx)
    assert derived == pytest.approx(4.0), (
        f"expected a 4.0s derived wall from a 5.0s timeout, got {derived}")

    t0 = time.monotonic()
    level, detail = checks.l34_full_route_smoke(ctx)
    elapsed = time.monotonic() - t0

    assert level in (harness.PASS, harness.WARN, harness.FAIL, harness.NA), (
        f"L34 returned a level of {level!r}")
    assert ctx.probed, (
        "L34 probed no route at all, so the timing below measures an early "
        "return rather than a bounded sweep. The fixture serves "
        f"/api/dev/routes with {_OPERATOR_ROUTES + _DIAGNOSTIC_ROUTES} routes; "
        f"log was: {ctx.messages[:4]}")
    assert any(str(_OPERATOR_ROUTES) in m for m in ctx.messages), (
        f"L34 did not report enumerating {_OPERATOR_ROUTES} operator routes; "
        f"log was: {ctx.messages[:4]}")
    assert elapsed < 5.0, (
        f"L34 ran for {elapsed:.1f}s under a 5.0s per-check timeout with "
        f"_L34_WALL_S patched to 100.0. harness.py:243 would have killed the "
        f"thread and recorded 'TIMEOUT ... thread leaked' instead of the "
        f"verdict it computed ({level}: {str(detail)[:120]!r}). The wall is "
        f"still coming from the module constant, not from the Context.")


def test_capture_sh_still_gets_the_full_seventy_two_second_wall():
    """NO CHANGE ON THE BOX -- the claim that makes this cut safe to ship
    between captures.

    capture.sh passes 90; 90 * 0.8 == 72.0, which is exactly today's constant.
    If this ever stops being true the capture's L34 budget has moved and the
    operator must be told, because every recorded L34 timing was measured at 72.
    """
    vals = _capture_sh_values()
    assert vals, "capture.sh no longer passes --per-check-timeout at all"
    wall = checks._l34_wall_s(_Ctx(per_check_timeout_s=max(vals)))
    assert wall == pytest.approx(72.0), (
        f"under capture.sh's {max(vals):.0f}s timeout the derived wall is "
        f"{wall:.1f}s, not the 72.0s every recorded L34 run was measured at. "
        f"This cut is only safe to ship without a capture because it leaves "
        f"the box's budget untouched; that no longer holds.")


# ── the wiring: the harness must DELIVER the timeout it ENFORCES ─────────────
#
# Added because a mutation survived. Removing the `per_check_timeout_s=...`
# forwarding from run_all's Context construction left all 15 assertions above
# GREEN: the helper was still correct given a Context, and capture.sh still said
# 90, but nothing connected the two. Every check would have received
# per_check_timeout_s=None, fallen back to the 60s default and been clamped to a
# 48s wall -- on the box, where the harness was in fact enforcing 90. A silent
# one-third loss of L34's coverage, invisible to the gate written to protect it.
#
# The two numbers must be observed at the same seam, not asserted separately.

def test_run_all_gives_each_check_the_timeout_it_will_be_held_to(tmp_path,
                                                                monkeypatch):
    """KILLS THE SURVIVING MUTATION.

    Runs the real run_all with a single throwaway check that reports the
    Context it was handed. The value the check sees must be the value run_all
    was told to enforce -- harness.py:243 passes that same parameter to
    _run_with_timeout, so if the Context disagrees with it, the check is pacing
    against one number while being killed by another. That was the original
    defect; this is the assertion that stops it coming back through the wiring
    instead of through the constant.
    """
    seen = {}

    def _probe(ctx):
        seen["timeout"] = getattr(ctx, "per_check_timeout_s", "ATTRIBUTE ABSENT")
        return harness.PASS, "wiring probe"

    monkeypatch.setattr(
        harness, "_REGISTRY",
        [harness.LiveTest("LWIRE", "timeout wiring probe", _probe)])
    # An unroutable target: _app_version's GET fails fast and is not the subject.
    harness.run_all("http://127.0.0.1:1", str(tmp_path),
                    results_dir=str(tmp_path / "results"),
                    per_check_timeout_s=37.0)

    assert seen.get("timeout") == pytest.approx(37.0), (
        f"run_all enforced a 37.0s per-check timeout but handed the check a "
        f"Context reporting {seen.get('timeout')!r}. A check cannot pace itself "
        f"against a wall it is not told about, and the fallback it would use "
        f"instead ({_harness_default():.0f}s) is wrong in the one direction "
        f"that matters on the box: it would clamp L34 to "
        f"{_harness_default() * checks._L34_WALL_FRACTION:.0f}s while the "
        f"harness was actually allowing 72.0s.")


def test_the_cli_forwards_its_flag_into_run_all():
    """The other half of the wiring, by AST.

    run.py prints the timeout it parsed and then calls run_all. If the keyword
    were dropped there, the printed banner would say 90 while every check ran
    against the 60s fallback -- a report that contradicts the run, which is
    worse than no report.
    """
    tree = ast.parse(_RUN_PY.read_text(encoding="utf-8"))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "run_all"]
    assert calls, "live_tests/run.py no longer calls harness.run_all"
    for call in calls:
        kwargs = {kw.arg for kw in call.keywords if kw.arg}
        assert "per_check_timeout_s" in kwargs, (
            f"the run_all call at run.py:{call.lineno} does not pass "
            f"per_check_timeout_s (passes {sorted(kwargs)}). The --per-check-"
            f"timeout flag would be parsed, printed and discarded.")


# ── the old gate keeps its job ───────────────────────────────────────────────

def test_the_capture_sh_minimum_gate_is_still_present():
    """test_u45's `>= 90` assertion is KEPT, not replaced.

    It is now defence in depth rather than the sole guarantee: the derivation
    above means a lower capture.sh timeout would no longer wedge L34, it would
    quietly shrink its wall and produce more UNPROBED routes. That is a real
    regression in coverage even though it is not a timeout, so the floor stays
    -- and deleting a passing gate needs a better reason than redundancy.
    """
    src = (ROOT / "tests" / "test_u45_capture_sh_shipped.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    names = {n.name for n in ast.walk(tree)
             if isinstance(n, ast.FunctionDef)}
    assert "test_live_tests_per_check_timeout_at_least_90" in names, (
        "test_u45_capture_sh_shipped.py no longer contains "
        "test_live_tests_per_check_timeout_at_least_90. The derived wall makes "
        "that gate non-load-bearing for the TIMEOUT failure mode, but it still "
        "protects L34's coverage budget on the box.")
