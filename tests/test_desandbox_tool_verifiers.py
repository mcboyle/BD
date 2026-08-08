"""bd-mutation-test and bd-band-derive verified nothing, because they were aimed
at a sandbox that does not exist on this tree.

Both tools' ENGINES are sound -- bd-mutation-test's four-state discipline
(CAUGHT / SURVIVED / UNKNOWN / BASELINE-RED) and bd-band-derive's ~19 selftest
controls are real work. What was broken is where they pointed:

  bd-mutation-test  11 registry gates shelled `python3` -- the container's 3.11
                    WITHOUT the project deps, so three of the load-bearing gate
                    rows (route_index, route_map, body_contract) went
                    BASELINE-RED purely on the interpreter and could never
                    report SURVIVED. 4 rows copied src="/home/claude/bin", a
                    path absent here, and crashed in shutil.copytree.
  bd-band-derive    selftest() hardcoded /home/claude/work in TWO places
                    (:750 and :1079), so ~19 controls whose own docstring says
                    they "WOULD HAVE CAUGHT the constant-band bug this tool
                    shipped with" printed `SKIP  no work tree` and PASSED
                    without executing.

THE SHAPE, and why it is this project's recurring one: a verifier that cannot
reach its subject reports success. bd-band-derive's SKIP->PASS is CLAUDE.md
section 0 exactly -- and it is worse than an ordinary blind gate because the
thing it fails to verify is OTHER gates' blast radius.

THREE ANCHORS WERE ALSO ROTTEN, which is the second half of the fix. A mutation
anchor that matches 442 sites and is applied with count=1 mutates whichever site
re.subn reaches first -- not the one the row names -- so the row's CAUGHT verdict
is evidence about a different location. Measured on pristine:

    ROUTE_INDEX.json            '"spa_wired": true'  -> 442 matches, count=1
    tools/BODY_CONTRACT_CALLS   '"/api/'             -> 259 matches, count=1
    live_tests/checks.py        (L34 exceeded)       ->   0 matches, count=1

The third is ROT: it matches nothing, so the row can only ever return UNKNOWN.
A3 below fails on all three and passes only when each anchor resolves to exactly
the count the row declares.

A3 IS AST-BASED ON PURPOSE. Reading row["mutate"].pattern off the loaded closure
would require first patching the shared _sub helper -- which would make this
assertion untestable against pristine source, and couple an anchor-uniqueness
check to an unrelated edit. It parses the REGISTRY literal instead, so it runs
identically before and after the fix.

A3 IS ALSO FAIL-CLOSED. Every registry row must resolve to an existing target
file and a parseable _sub(pattern, ..., count); anything unrecognised FAILS
rather than being skipped. A row pointing at a renamed file must not be silently
certified -- unknown is a third state and it fails.

RED-first: A1, A2, A3, A5, A6 and B1 all fail on pristine source. A4 is a
labelled regression guard that passes today and is not counted as RED.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"
MT = BIN / "bd-mutation-test"
BD = BIN / "bd-band-derive"


def _load_mt():
    """Load bd-mutation-test as a module. Its main() is __main__-guarded, so
    importing has no side effects."""
    import importlib.machinery
    import importlib.util
    spec = importlib.util.spec_from_loader(
        "bd_mutation_test_under_test",
        importlib.machinery.SourceFileLoader(
            "bd_mutation_test_under_test", str(MT)))
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(BIN))
    try:
        spec.loader.exec_module(mod)
    finally:
        if str(BIN) in sys.path:
            sys.path.remove(str(BIN))
    return mod


# ── the registry must not re-sandbox the interpreter or the tree ─────────────

def test_no_gate_reruns_the_wrong_interpreter():
    """RED. Pristine: 11 rows shell `python3`, which on this container is 3.11
    without the project dependencies -- so the gate fails to import flask and
    the row reports BASELINE-RED regardless of the mutation.

    The `/home/` clause is the cry-wolf half: baking an absolute interpreter
    path into a gate would work on one machine and break every other, so the
    placeholder must survive rather than being resolved at edit time.
    """
    mod = _load_mt()
    bad = []
    for row in mod.REGISTRY:
        gate = row.get("gate") or ""
        if not gate:
            continue
        if gate.split()[0] == "python3":
            bad.append(f"{row['id']}: gate shells python3 -- {gate[:60]}")
        if "/home/" in gate:
            bad.append(f"{row['id']}: gate bakes an absolute path -- {gate[:60]}")
    assert not bad, (
        "these gates cannot execute against this tree's interpreter:\n  "
        + "\n  ".join(bad))


def test_no_row_copies_a_tree_that_does_not_exist():
    """RED. Pristine: 4 rows set src="/home/claude/bin", which is absent here,
    so the row dies in shutil.copytree -- bypassing the tool's own doctrine
    that an UNKNOWN must be reported rather than raised."""
    mod = _load_mt()
    bad = []
    for row in mod.REGISTRY:
        src = row.get("src")
        if not src:
            continue
        # The property is "this directory EXISTS", not "this string does not
        # contain /home/". A first draft used the substring and fired on the
        # CORRECT fixed value, because this checkout lives under /home/user --
        # a gate failing on identity rather than behaviour, which is section 0's
        # inverse and gets gates switched off. The /home/claude clause below is
        # deliberately narrow: it names the one dead sandbox, so it cannot fire
        # on a real tree that happens to sit under some other /home path.
        if str(src).startswith("/home/claude"):
            bad.append(f"{row['id']}: src is the retired sandbox -- {src}")
        elif not os.path.isdir(str(src)):
            bad.append(f"{row['id']}: src does not exist -- {src}")
    assert not bad, (
        "these rows copy a source tree that is not present:\n  "
        + "\n  ".join(bad))


def _registry_rows_from_ast():
    """Every REGISTRY row as (id, target, has_src, mutate_node), read from the
    SOURCE rather than the loaded closure.

    Reading the compiled closure would require the tool to expose the pattern,
    which pristine source does not -- this assertion has to be runnable on both
    sides of the fix or it proves nothing.
    """
    tree = ast.parse(MT.read_text(encoding="utf-8"))
    reg = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "REGISTRY" for t in node.targets):
            reg = node.value
    assert reg is not None, "REGISTRY assignment not found in bd-mutation-test"
    assert isinstance(reg, (ast.List, ast.Tuple)), "REGISTRY is not a literal list"
    rows = []
    for el in reg.elts:
        # The rows are dict(id=..., target=..., mutate=...) CALLS, not {...}
        # literals. An earlier draft of this helper filtered on ast.Dict and so
        # matched NOTHING -- 16 rows in, 0 rows out, and the assertion below
        # passed over an empty denominator. That is the exact section 0 failure
        # this cut exists to fix, reproduced inside its own test; the length
        # assertion at the end is what makes it impossible to repeat silently.
        if isinstance(el, ast.Dict):
            rows.append({k.value: v for k, v in zip(el.keys, el.values)
                         if isinstance(k, ast.Constant)})
        elif (isinstance(el, ast.Call)
              and getattr(el.func, "id", getattr(el.func, "attr", "")) == "dict"):
            rows.append({kw.arg: kw.value for kw in el.keywords if kw.arg})
    assert len(rows) == len(reg.elts), (
        f"the REGISTRY reader understood {len(rows)} of {len(reg.elts)} rows. "
        f"A row it cannot parse is a row it cannot check, and a shrinking "
        f"denominator here certifies anchors nobody verified.")
    assert rows, "REGISTRY is empty -- nothing to verify"
    return rows


def test_every_mutation_anchor_resolves_to_exactly_its_declared_count():
    """RED, and the assertion that makes every other CAUGHT verdict meaningful.

    An anchor matching 442 sites applied with count=1 mutates whichever site
    re.subn reaches first, so the row proves something about a location it does
    not name. An anchor matching 0 sites can only ever return UNKNOWN.

    FAIL-CLOSED: a row whose target is missing, or whose mutate is not a
    recognisable _sub(...), FAILS here. It is not skipped. Only an explicit
    __PLANT__ row is exempt.
    """
    bad = []
    for d in _registry_rows_from_ast():
        target_node = d.get("target")
        if target_node is None or not isinstance(target_node, ast.Constant):
            bad.append("a row has no literal target")
            continue
        target = target_node.value
        if str(target).startswith("__PLANT__:"):
            continue
        rid = d.get("id")
        rid = rid.value if isinstance(rid, ast.Constant) else "<unknown id>"
        mut = d.get("mutate")
        if not (isinstance(mut, ast.Call)
                and getattr(mut.func, "id", getattr(mut.func, "attr", "")) == "_sub"):
            bad.append(f"{rid}: mutate is not a recognisable _sub(...) call")
            continue
        try:
            pat = ast.literal_eval(mut.args[0])
            cnt = ast.literal_eval(mut.args[2]) if len(mut.args) >= 3 else 1
        except Exception as exc:
            bad.append(f"{rid}: could not read the anchor -- {exc}")
            continue
        base = BIN if "src" in d else REPO
        path = base / target
        if not path.exists():
            bad.append(f"{rid}: target does not exist -- {path}")
            continue
        try:
            found = len(re.findall(pat, path.read_text(errors="replace")))
        except re.error as exc:
            bad.append(f"{rid}: anchor is not a valid regex -- {exc}")
            continue
        if cnt > 0:
            if found != cnt:
                bad.append(f"{rid}: anchor matches {found} site(s) in {target}, "
                           f"but the row applies count={cnt}")
        elif found < 1:
            bad.append(f"{rid}: replace-all anchor matches nothing in {target}")
    assert not bad, (
        "these mutation anchors do not identify the site they claim:\n  "
        + "\n  ".join(bad))


# @877 -- the carriers this gate KNOWS about. Anything outside this set is a
# regression; anything inside it that gets fixed must be REMOVED from the list
# in the same cut, which is why the assertion is set EQUALITY and not "no new
# ones". A one-directional count would let the list rot into a permanent
# amnesty, and this repo already has that failure recorded: a floor of 150 sat
# under a real population of 258 and could not see a narrowing it was written
# to catch (@870).
#
# NONE of these is on a band path. The two that were -- bd-band and
# bd-bandcheck -- were fixed at @876; the rest are operator-invoked
# checkpoint/rollback/snapshot tools and decomp helpers, which is item 8c and
# is deliberately NOT in this cut. Listing them makes them visible; before
# this, a gate reading ONE file certified the whole population.
_SANDBOX_WORK_DEFAULT = 'default="/home/claude/work"'
_KNOWN_SANDBOX_DEFAULT_CARRIERS = {
    "project-knowledge/review_merge.py",
    "project-knowledge/seed_review_state.py",
    "toolchain/bin/bd-checkpoint",
    "toolchain/bin/bd-rollback",
    "toolchain/bin/bd-since",
    "toolchain/bin/bd-snapshot",
    "tools/bd-scan.py",
    "tools/bd_decomp_lib.py",
    "tools/decomp_regen.py",
    "tools/invariants.py",
    "tools/review_merge.py",
    "tools/risk_score.py",
    "tools/seed_review_state.py",
    # this file: the literal appears in the assertion itself
    "tests/test_desandbox_tool_verifiers.py",
}


def _python_typed_tracked():
    """Tracked files that are Python by EXTENSION *or* by SHEBANG.

    `git ls-files -- '*.py'` is NOT "the Python files in this repo" -- a *.py
    glob reaches 2.5% of toolchain/, where the tools are extensionless bd-*
    scripts. Typing on the shebang as well is the difference between a
    denominator of 2110 and one of 2568.
    """
    out = subprocess.run(["git", "ls-files", "-z"], cwd=str(REPO),
                         capture_output=True, text=True, check=True).stdout
    keep = []
    for rel in out.split("\0"):
        if not rel:
            continue
        p = REPO / rel
        if rel.endswith(".py"):
            keep.append(rel)
            continue
        if "." in rel.rsplit("/", 1)[-1]:
            continue
        try:
            head = p.read_bytes()[:80]
        except OSError:
            continue
        if head[:2] == b"#!" and b"python" in head.split(b"\n")[0]:
            keep.append(rel)
    return keep


def _classify_sandbox_carriers(n_files, carriers, known, floor=2000):
    """Every problem with this scan, as a list of messages. PURE on purpose.

    Extracted so the three guards can be driven with constructed input -- see
    test_the_sandbox_carrier_classifier_fires_in_all_three_directions. Left
    inline, all three were unreachable by any mutant on a healthy tree.
    """
    problems = []
    if n_files <= floor:
        problems.append(
            "the python-typed denominator collapsed to %d files (floor %d); "
            "every assertion here would be passing over almost nothing"
            % (n_files, floor))
    new = set(carriers) - set(known)
    if new:
        problems.append(
            "%d file(s) newly default --work to the retired sandbox path, so a "
            "bare invocation measures a tree that is not there:\n  %s"
            % (len(new), "\n  ".join(sorted(new))))
    fixed = set(known) - set(carriers)
    if fixed:
        problems.append(
            "%d file(s) no longer carry the sandbox default -- good, but remove "
            "them from _KNOWN_SANDBOX_DEFAULT_CARRIERS in the SAME cut. A list "
            "that only ever grows stale becomes a permanent amnesty:\n  %s"
            % (len(fixed), "\n  ".join(sorted(fixed))))
    return problems


def test_the_bare_work_default_is_not_a_sandbox_path():
    """@877 -- this gate used to read ONE FILE.

    `MT.read_text()` -- bd-mutation-test, and nothing else -- while 20 other
    tracked files carried the identical `default="/home/claude/work"`. It
    certified a population of 2568 by looking at one member of it, and reported
    OK. CLAUDE.md section 0: the denominator excluded the subject.

    Kept NARROW on the `default=` FORM, not on the bare path. Several of these
    files legitimately mention /home/claude in a comment explaining this very
    class of bug -- including the one @876 added to bd-bandcheck -- so a
    whole-file scan would fire on a fixed tree. Over-sensitivity is a soundness
    bug too; that distinction is why the original author scoped it, and it is
    kept.
    """
    files = _python_typed_tracked()
    carriers = set()
    for rel in files:
        try:
            if _SANDBOX_WORK_DEFAULT in (REPO / rel).read_text(
                    encoding="utf-8", errors="replace"):
                carriers.add(rel)
        except OSError:
            continue
    problems = _classify_sandbox_carriers(
        len(files), carriers, _KNOWN_SANDBOX_DEFAULT_CARRIERS)
    assert not problems, "\n".join(problems)


def test_the_sandbox_carrier_classifier_fires_in_all_three_directions():
    """@877 -- the assertions above are unconstrained ON A HEALTHY TREE.

    A mutation battery proved it: deleting the empty-denominator canary, or the
    new-carrier check, or the fixed-carrier check, ALL left the suite green,
    because on this tree `new` and `fixed` are both empty and the denominator
    is nowhere near collapsing. Three guards that fire only in a state the tree
    is not in are three guards no mutant can reach -- and a test that passes
    before and after is not a test.

    So the decision is a PURE function and this drives it with constructed
    input. That is the only way to exercise a guard whose real-world trigger is
    an event that has not happened yet.
    """
    known = {"a.py", "b.py"}

    # clean: same set, plausible denominator -> silent
    assert _classify_sandbox_carriers(2500, {"a.py", "b.py"}, known) == []

    # collapsed denominator -> the canary fires even though the sets agree
    out = _classify_sandbox_carriers(3, {"a.py", "b.py"}, known)
    assert out and "denominator" in out[0], out

    # a NEW carrier -> named
    out = _classify_sandbox_carriers(2500, {"a.py", "b.py", "c.py"}, known)
    assert out and "c.py" in out[0] and "newly" in out[0], out

    # a FIXED carrier -> also named, so the list cannot rot into an amnesty
    out = _classify_sandbox_carriers(2500, {"a.py"}, known)
    assert out and "b.py" in out[0], out

    # both at once -> BOTH reported, not just the first
    out = _classify_sandbox_carriers(2500, {"a.py", "c.py"}, known)
    assert len(out) == 2, out


# ── the engines still work (behavioural) ────────────────────────────────────

def test_the_mutation_engine_selftest_still_passes():
    """REGRESSION GUARD -- passes on pristine too. Labelled, NOT counted as RED.

    The engine and its four-state discipline are sound and this cut must not
    disturb them.
    """
    r = _run_tool([sys.executable, str(MT), "--selftest"],
                  budget_s=300, what="bd-mutation-test", cwd=REPO)
    assert r.returncode == 0, f"selftest exit={r.returncode}\n{r.stdout[-2000:]}"
    assert "SELFTEST PASS" in r.stdout, r.stdout[-2000:]


@pytest.mark.slow
def test_a_real_gate_row_runs_end_to_end_and_catches_its_mutation():
    """RED, and the only assertion that exercises the {py} substitution.

    Everything above is structural over the registry text. If the substitution
    in check() were reverted, every gate would be the literal string
    "{py} run_tests.py ..." -- unrunnable -- and no structural assertion would
    notice. This drives one real row all the way through copy -> mutate -> gate
    and requires the four-state engine to return CAUGHT.
    """
    r = _run_tool(
        [sys.executable, str(MT), "--only", "route_index/spa_wired",
         "--work", str(REPO), "--json"],
        budget_s=600, what="bd-mutation-test", cwd=REPO)
    assert r.returncode in (0, 1), f"exit={r.returncode}\n{r.stdout[-3000:]}"
    import json as _json
    # The payload is PRETTY-PRINTED across many lines, so parse from the first
    # brace to the end rather than line-by-line (a per-line json.loads only ever
    # sees "{" and fails, which reads as "the tool produced no output" -- a
    # wrong diagnosis of a working tool).
    start = r.stdout.find("{")
    assert start != -1, f"no JSON in output:\n{r.stdout[-3000:]}"
    try:
        payload = _json.loads(r.stdout[start:])
    except ValueError as exc:
        pytest.fail(f"could not parse the JSON payload ({exc}):\n"
                    f"{r.stdout[-3000:]}")
    rows = payload.get("results") if isinstance(payload, dict) else payload
    assert rows, f"no result rows: {payload}"
    state = rows[0].get("state")
    assert state == "CAUGHT", (
        f"the row reported {state!r}, not CAUGHT. A gate that cannot execute "
        f"reports BASELINE-RED and can never prove a mutation was caught.\n"
        f"{r.stdout[-3000:]}")


@pytest.mark.slow
def test_band_derive_selftest_actually_executes_its_controls():
    """RED. Pristine prints `SKIP  no work tree` and PASSES -- ~19 controls
    whose docstring says they "WOULD HAVE CAUGHT the constant-band bug this
    tool shipped with" never ran.

    The RED signal is CONTENT, not the exit code: pristine exits 0 either way,
    which is precisely why a SKIP that returns success is dangerous.

    INVARIANT: invoke the tool at its REAL path. bdtools_sec.DEFAULT_WORK is
    derived by walking up from bdtools_sec.py's own location, so running a copy
    from anywhere else resolves back to the sandbox default and reproduces the
    false green this test exists to catch.
    """
    env = {**os.environ, "PYTHONPATH": str(BIN)}
    r = _run_tool([sys.executable, str(BD), "--selftest"],
                  budget_s=600, what="bd-band-derive", cwd=REPO, env=env)
    out = r.stdout + r.stderr
    assert "SKIP  no work tree" not in out, (
        "bd-band-derive still skipped its own controls and reported success:\n"
        + out[-2000:])
    assert "SIGNAL 7" in out, (
        "the controls did not run -- SIGNAL 7 is absent:\n" + out[-2000:])
    assert "SELFTEST PASS" in out, out[-2000:]
    assert r.returncode == 0, f"exit={r.returncode}\n{out[-2000:]}"


# --- C. the two slow rows must fail LOUDLY, and `slow` must be a real mark ----
#
# Measured on the operator's box: this file's own
# test_a_real_gate_row_runs_end_to_end_and_catches_its_mutation blew its 600s
# subprocess budget inside a ten-file run and surfaced as a raw
# subprocess.TimeoutExpired traceback -- no verdict, no next step, and a whole
# capture graded FAIL on `unit failures=1`.
#
# Two separate defects, and the budget is NOT one of them:
#
#   * `@pytest.mark.slow` was applied here but never registered, so pytest
#     warned PytestUnknownMarkWarning and the mark deselected nothing. A marker
#     that reads as a control and controls nothing is exactly the shape this
#     file exists to catch. Registered in tests/conftest.py.
#
#   * A TimeoutExpired escaping the test body gives the reader a stack trace
#     from subprocess.py and nothing about WHICH tool, what budget, or what to
#     run next. `_run_tool` converts it into a verdict that says so.
#
# WHY THE BUDGET IS UNCHANGED. Measured runtimes for the two tools:
# bd-mutation-test 25.8s and bd-band-derive 2.0s in the cloud sandbox; the whole
# file runs in 52.7s on the box. The failing run exceeded 600s -- roughly 13x
# the box's standalone figure -- so it hung rather than ran slowly, and a larger
# number would only make a hang burn more of the capture before failing. The
# budget stays calibrated to measurement and the message names the ratio, so the
# next reader can tell "slow" from "stuck" without re-deriving it.

_MEASURED_S = {"bd-mutation-test": 25.8, "bd-band-derive": 2.0}


def _run_tool(argv, *, budget_s, what, **kwargs):
    """Run a whole-tree tool, converting a timeout into a DIAGNOSIS.

    Unknown is a third state and it fails -- this never downgrades a timeout to
    a skip. It only replaces an opaque traceback with the verdict, the budget,
    the measured baseline, and the one command that distinguishes a hang from
    ordinary slowness.
    """
    try:
        return subprocess.run(argv, capture_output=True, text=True,
                              timeout=budget_s, **kwargs)
    except subprocess.TimeoutExpired as exc:
        baseline = _MEASURED_S.get(what)
        ratio = f" (~{budget_s / baseline:.0f}x its measured {baseline}s)" if baseline else ""
        printable = " ".join(str(a) for a in argv)
        pytest.fail(
            f"{what} did not finish within {budget_s}s{ratio}, so this row "
            f"produced NO verdict -- it is not a pass and not a fail of the "
            f"thing under test.\n"
            f"  command: {printable}\n"
            f"  next:    run that command by hand. If it completes in about "
            f"{baseline or '<measured>'}s, the tool is fine and this was a "
            f"HANG under suite load, not slowness -- raising the budget would "
            f"only make the next occurrence burn longer before failing.\n"
            f"  partial stdout: {(exc.stdout or b'')[-1500:]!r}"
        )


def test_the_slow_marker_is_registered():
    """RED. `@pytest.mark.slow` sits on two tests in this file, but nothing
    registered it -- pytest warned PytestUnknownMarkWarning and `-m 'not slow'`
    selected nothing, so the mark was decoration.

    Asserted by asking pytest for its OWN registry rather than grepping
    conftest: the question is "can a caller deselect these", and only the
    resolved marker list answers it.
    """
    r = subprocess.run([sys.executable, "-m", "pytest", "--markers"],
                       cwd=REPO, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]
    assert "@pytest.mark.slow" in r.stdout, (
        "the `slow` marker is not registered, so the @pytest.mark.slow on the "
        "two tool rows in this file deselects nothing and pytest warns on every "
        "run:\n" + r.stdout[-2000:])


def test_a_tool_timeout_reports_a_diagnosis_not_a_bare_traceback():
    """RED. Before _run_tool the timeout propagated as subprocess.TimeoutExpired
    straight out of the test body -- the reader got subprocess.py's stack and no
    statement of which tool, what budget, or what to do next.

    Drives a real timeout with a 1s budget. Asserts the failure is pytest's own
    (a stated verdict) and that it carries the three things the box run lacked:
    the tool name, the budget, and a next step. Also asserts it did NOT become a
    skip -- a timeout is 'could not evaluate', which fails.
    """
    with pytest.raises(pytest.fail.Exception) as caught:
        _run_tool([sys.executable, "-c", "import time; time.sleep(30)"],
                  budget_s=1, what="bd-mutation-test")
    message = str(caught.value)
    assert "bd-mutation-test" in message, message
    assert "1s" in message, message
    assert "next:" in message, message
    assert "NO verdict" in message, message


def test_the_tool_rows_go_through_the_diagnosing_runner():
    """The helper is worthless if a row bypasses it.

    Without this, someone could revert a call site to a bare
    subprocess.run(..., timeout=...) and the timeout test above would STILL be
    green -- it exercises _run_tool directly. That is the decoration defect this
    section exists to remove, one level up.

    AST over this module, scoped to the functions that actually shell out to a
    tool (they reference MT or BD): those must contain no direct subprocess.run.
    Structural rather than a substring scan, because `subprocess.run` appears
    legitimately inside _run_tool itself and in the --markers probe.
    """
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    offenders = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name != "_run_tool"]:
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        if not ({"MT", "BD"} & names):
            continue
        for call in [n for n in ast.walk(fn) if isinstance(n, ast.Call)]:
            f = call.func
            if (isinstance(f, ast.Attribute) and f.attr == "run"
                    and isinstance(f.value, ast.Name) and f.value.id == "subprocess"):
                offenders.append(f"{fn.name}:{call.lineno}")
    assert not offenders, (
        "these tool-invoking tests call subprocess.run directly instead of "
        f"_run_tool, so a timeout there still surfaces as a bare "
        f"TimeoutExpired traceback with no verdict: {offenders}"
    )
