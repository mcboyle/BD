"""@1009. The capture bundles the live checks' per-check logs -- and keeps them
safe to bundle.

WHY. The v3.66.1007 capture failed L34 with `92 route(s) UNPROBED (phase-1
deadline)` and named five example routes. Diagnosing that needs the DENOMINATOR
(how many operator routes) and the per-route timings, and L34 logs both:

    live_tests/harness.py  "<id>.log   full verbose log of the test -- ALWAYS
                            written, overwritten each run."
    live_tests/checks.py   ctx.log(f"{len(routes)} routes total; {len(targets)}
                            operator parameter-free GET routes to gate + ...")

capture.sh collected NONE of it. The tarball carried a 55-line summary, so the
operator-visible artifact stated a finding and discarded the evidence for it --
a gate whose reasoning cannot be audited. Two captures were uploaded and neither
could answer which of "the surface grew" and "N routes are pathologically slow"
was true; the answer was sitting in a file on the box the whole time.

THE SECOND HALF IS WHY THIS FILE IS NOT JUST A `cp`. Bundling more of the app's
own output widens what leaves the box, and capture.sh already carries that
lesson in step [3]: bd_session "is a live credential ... the value is not one of
[the diagnostic facts], and is replaced by its length (the same rule the capture
vault follows: status recorded, body never)." The live checks GET 264 operator
routes including /api/secrets/*, and `ctx.get` hands each check the parsed
response BODY. Nothing stopped a check from logging one.

Measured before the change, over live_tests/: 177 ctx.log call sites, 1 of them
derived from a response body, and that one logs `body.get('version')`. So the
logs were safe to bundle at the moment they started being bundled -- and that
is a property of today's checks, not a guarantee about tomorrow's. This file
pins it, so the next check that logs a body fails here instead of shipping it.

DENOMINATOR ASSERTED, NOT ASSUMED. The body-logging scan is the exact shape
CLAUDE.md section 0 is about: it would report "0 sites log a body" just as
truthfully over a parse that found no ctx.log calls at all. The site count is
asserted non-empty first, so a scan that goes blind FAILS rather than certifying.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
CAPTURE = REPO / "capture.sh"
LIVE = REPO / "live_tests"

sys.path.insert(0, str(REPO / "tests"))
from shell_source import shell_code_only   # noqa: E402


# ── the bundling half ─────────────────────────────────────────────

def test_capture_script_parses():
    r = subprocess.run(["bash", "-n", str(CAPTURE)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_the_results_are_COPIED_in_executable_shell():
    """Asserted over comment-stripped source. The step's own comment has to
    name the directory in order to explain itself, and a naive grep cannot tell
    prose from code -- CLAUDE.md section 0 records four cuts where exactly that
    failed a correct repair."""
    code = shell_code_only(CAPTURE)
    assert "live_tests/results" in code, (
        "no executable line references live_tests/results -- the collection is "
        "prose, not code")
    assert "06_live_results" in code, (
        "the collected output has no destination inside $OUT")


def test_the_copy_happens_BEFORE_the_bundle():
    """Ordering is the whole feature. A copy after `tar czf` produces a
    directory on the box that no archive contains, which looks exactly like
    success from inside the script."""
    code = shell_code_only(CAPTURE)
    copy_at = code.index("06_live_results")
    tar_at = code.index('tar czf "$ARCHIVE"')
    assert copy_at < tar_at, (
        "the results are collected at char %d, after the bundle at %d"
        % (copy_at, tar_at))


def test_a_missing_results_dir_is_RECORDED_not_silent():
    """Unknown is a third state. If the live runner wrote nothing, the capture
    must say so -- an empty 06_live_results/ and a 06_live_results/ that was
    never populated are indistinguishable to the operator otherwise."""
    code = shell_code_only(CAPTURE)
    i = code.index("06_live_results")
    window = code[max(0, i - 1200):i + 1200]
    assert "no live-check results" in window.lower(), (
        "nothing in the collection step reports the empty case")


# ── the safe-to-bundle half ───────────────────────────────────────

def _ctx_log_sites():
    """(sites, body_logging) over live_tests/.

    AST, not grep: the question is whether a value that came from `ctx.get`'s
    body position flows into `ctx.log`, and a grep for "body" matches comments,
    unrelated locals, and the word in prose. The instrument fixes the
    denominator; the predicate below fixes the subject.

    STATED LIMIT, because a scan that overstates its reach is worse than none:
    this recognises the tuple-unpack form the checks actually use
    (`ok, status, body, ms = ctx.get(...)`). A body reached some other way --
    stored on an object, returned from a helper, indexed off a saved tuple --
    is outside it. It is a floor on the risk, not a proof of zero.
    """
    sites = 0
    body_logging = []
    for p in sorted(LIVE.rglob("*.py")):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        bodies = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Tuple):
                v = n.value
                if (isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute)
                        and v.func.attr == "get"):
                    elts = n.targets[0].elts
                    if len(elts) >= 3 and isinstance(elts[2], ast.Name):
                        name = elts[2].id
                        if name != "_":       # a discarded slot is not a body
                            bodies.add(name)
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "log"):
                sites += 1
                used = {x.id for x in ast.walk(n) if isinstance(x, ast.Name)}
                for hit in sorted(used & bodies):
                    body_logging.append((p.name, n.lineno, hit))
    return sites, body_logging


# Whole-response logging is what must never appear. `body.get('version')` is a
# named scalar the check reports on purpose (harness._app_version does the same
# thing), and is allowed BY ATTRIBUTE, not by file or line -- an allowance keyed
# to a location silently re-arms the moment the line moves.
_ALLOWED_BODY_READS = ("body.get('version')", 'body.get("version")')


def test_the_body_logging_scan_can_actually_SEE_ctx_log():
    """The non-empty-denominator assertion, written BEFORE the verdict below.

    A scan whose parse found nothing reports "no site logs a body" truthfully
    and uselessly. Measured 2026-08-10: 177 sites.
    """
    sites, _ = _ctx_log_sites()
    assert sites > 100, (
        "only %d ctx.log site(s) found in live_tests/ -- the scan went blind, "
        "so its clean verdict below means nothing" % sites)


def test_no_live_check_logs_a_response_BODY_into_a_bundled_file():
    """Status recorded, body never -- capture.sh step [3]'s rule, applied to the
    files this cut starts shipping.

    A check that logs a whole response puts whatever /api/secrets/* returned
    into a tarball that leaves the box. That is not hypothetical scope: L34 GETs
    every parameter-free operator route, and `ctx.get` returns the parsed body
    to every check.
    """
    _sites, offenders = _ctx_log_sites()
    src = {}
    real = []
    for fname, lineno, name in offenders:
        p = LIVE / fname
        src.setdefault(fname, p.read_text(encoding="utf-8").splitlines())
        # the log call may span lines; take a small window from its start
        window = " ".join(src[fname][lineno - 1: lineno + 4])
        if not any(a in window for a in _ALLOWED_BODY_READS):
            real.append("%s:%d logs %r" % (fname, lineno, name))
    assert not real, (
        "a live check logs an HTTP response body, and capture.sh now bundles "
        "live_tests/results/ -- so this ships off the box:\n  "
        + "\n  ".join(real)
        + "\nLog the facts you mean (status, length, a named field), never the "
          "body. See capture.sh step [3]'s cookie rule.")


def test_the_allowance_is_by_attribute_not_by_location():
    """A guard that excuses `checks.py:85` re-arms nothing when the line moves;
    one that excuses `body.get('version')` keeps meaning the same thing. This
    asserts the allowance list stayed that shape."""
    for a in _ALLOWED_BODY_READS:
        assert "body.get(" in a and ".py" not in a and ":" not in a, a


# ── the step, EXECUTED ────────────────────────────────────────────
#
# Asserting over shell text proves the lines are present, not that they work.
# The @1003 lesson is the other half: that cut's first test ran the REAL
# capture.sh past its guard, and capture.sh deletes $OUT and sweeps __pycache__
# under $BD_HOME -- on the box, the live checkout. So this extracts the step and
# runs THAT, never the script.

_STEP_START = 'echo "=== [6b/9] Live-check per-check logs ==="'
_STEP_END = "# Remove the synthetic state"


def _extract_step() -> str:
    """The [6b] block, cut on NAMED BOUNDARIES rather than a line count.

    CLAUDE.md section 2a: a fixed-width slice swallowed a closing `fi` once and
    produced a bash syntax error that presented as a failure of the subject.
    The extraction is verified with `bash -n` by its own test below, so a cut
    that lands mid-construct fails as an extractor bug and not as a finding.
    """
    src = CAPTURE.read_text(encoding="utf-8")
    assert src.count(_STEP_START) == 1, "the [6b] step's anchor moved"
    assert src.count(_STEP_END) == 1, "the [6b] step's end anchor moved"
    i = src.index(_STEP_START)
    j = src.index(_STEP_END, i)
    return src[i:j]


def test_the_extracted_step_is_a_complete_shell_construct():
    step = _extract_step()
    r = subprocess.run(["bash", "-n", "-c", step], capture_output=True, text=True)
    assert r.returncode == 0, (
        "the extractor cut mid-construct -- fix the extractor, this is not a "
        "finding about capture.sh:\n" + r.stderr)


def _run_step(tmp_path, *, results):
    """Run the step in an isolated tree. `results` maps filename -> content;
    None means the results directory does not exist at all."""
    out = tmp_path / "out"
    out.mkdir()
    if results is not None:
        rdir = tmp_path / "live_tests" / "results"
        rdir.mkdir(parents=True)
        for name, body in results.items():
            (rdir / name).write_text(body, encoding="utf-8")
    script = 'set -u\nOUT="%s"\n%s' % (out, _extract_step())
    r = subprocess.run(["bash", "-c", script], cwd=str(tmp_path),
                       capture_output=True, text=True, timeout=60)
    return r, out / "06_live_results"


def test_it_COLLECTS_the_per_check_logs(tmp_path):
    r, dst = _run_step(tmp_path, results={
        "L34.log": "1001 routes total; 264 operator parameter-free GET routes\n",
        "L1.log": "headless chromium ok\n",
        "L34.fail.txt": "92 route(s) UNPROBED\n",
        "SUMMARY.txt": "FAIL  L34  full-route-smoke\n",
    })
    assert r.returncode == 0, r.stderr
    got = sorted(p.name for p in dst.iterdir())
    assert got == ["L1.log", "L34.fail.txt", "L34.log", "SUMMARY.tail.txt"], got
    assert "264 operator" in (dst / "L34.log").read_text(), (
        "the file was created but its content did not come across")
    assert "collected 4 file(s)" in r.stdout, r.stdout


def test_the_APPEND_only_summary_is_tailed_not_copied_whole(tmp_path):
    """SUMMARY.txt accumulates across every run the box has ever done. Copying
    it whole would grow the archive without bound, and the destination name has
    to say it is a tail or a reader will take the first line as this run's."""
    r, dst = _run_step(tmp_path, results={
        "SUMMARY.txt": "".join("line %d\n" % n for n in range(1000))})
    assert r.returncode == 0, r.stderr
    assert not (dst / "SUMMARY.txt").exists(), "shipped the unbounded file"
    lines = (dst / "SUMMARY.tail.txt").read_text().splitlines()
    assert len(lines) == 400 and lines[-1] == "line 999", (len(lines), lines[-1])


def test_an_ABSENT_results_dir_is_recorded_as_UNKNOWN(tmp_path):
    """The other direction, and the one that matters. A step that silently
    produces an empty directory when the runner wrote nothing leaves the
    operator unable to tell 'no evidence' from 'evidence says fine'."""
    r, dst = _run_step(tmp_path, results=None)
    assert r.returncode == 0, r.stderr
    assert "no live-check results collected" in r.stdout, r.stdout
    note = dst / "NOTHING_COLLECTED.txt"
    assert note.is_file(), sorted(p.name for p in dst.iterdir())
    assert "UNKNOWN, not clean" in note.read_text()


def test_an_EMPTY_results_dir_is_also_recorded(tmp_path):
    """Present-but-empty and absent are the same fact to a reader of the
    archive, so they must produce the same record."""
    r, dst = _run_step(tmp_path, results={})
    assert r.returncode == 0, r.stderr
    assert "no live-check results collected" in r.stdout, r.stdout
    assert (dst / "NOTHING_COLLECTED.txt").is_file()


@pytest.mark.parametrize("artifact", ["<id>.log", "SUMMARY.txt", "<id>.fail.txt"])
def test_the_harness_still_writes_what_this_cut_collects(artifact):
    """The collection is only worth anything while the harness still produces
    these. Its docstring is the contract; if a rename lands, this fails here
    rather than producing an empty directory in the next capture."""
    doc = (LIVE / "harness.py").read_text(encoding="utf-8")
    assert artifact in doc, (
        "live_tests/harness.py no longer documents %r -- capture.sh collects a "
        "filename that may no longer exist" % artifact)
