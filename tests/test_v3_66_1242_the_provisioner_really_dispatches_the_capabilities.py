"""v3.66.1242 -- backlog row 228. The provisioner really dispatches, or it does not.

WHAT THE CURRENT GATE SCANS. test_v3_66_1064_provisioning_paths_do_not_diverge
judges "does provision_test_host.sh invoke the two optional capabilities?" by
tokenising the script's TEXT and asking whether each function name appears in
COMMAND POSITION -- at the start of a line or after `;`, `&&`, `||`, `|`, `(`,
`then`, `else` or `do`. That was @1210's answer to a measured evasion (a quoted
status message naming both functions), and it closed exactly that hole. Row 198
recorded the remainder in its own closure: a truly behavioural form remains open,
and it is this row.

WHY A PARSE CANNOT FINISH THE JOB, in both directions and both measured here:

  IT ACCEPTS THINGS THAT NEVER RUN. Command position is a property of the text,
  not of the execution. The same two lines moved below the script's final
  `exit`, wrapped in `if false`, or tucked inside a helper function nobody
  calls, satisfy every arm of the existing gate while the host is provisioned
  with neither capability and the verdict still reads its usual rows. So does a
  `.` of dev_capabilities.sh guarded by a condition that is never true: the
  sourcing line still begins with `.`, so the regex arm is satisfied, and on a
  real host both names then resolve to nothing.

  IT REJECTS THINGS THAT DO RUN. The counter drops everything after the first
  quote on a line, so it can only see today's invocation because the function
  name happens to sit on a `\\`-continuation LINE OF ITS OWN, after the quoted
  label. Collapse those two lines into one perfectly correct line and the
  existing arm FAILS a working provisioner -- measured below, not reasoned
  about. A name held in a variable fails it too.

WHAT THIS FILE PROVES INSTEAD. tests/provisioner_probe.py sources the shipped
script, BYTE-IDENTICAL, into an instrumented bash shell: `readonly -f` installs
a recording `run_step` that the script's own definition bounces off, the two
capability bodies and bd_start_display are replaced so nothing installs
PostgreSQL or starts an X server, and LOGDIR is pinned inside the fixture. What
the gate then reads is what the RUNNING SHELL did -- which run_step calls
happened, with which grade, and whether the dispatch actually arrived at the
function. A wrapper, a variable, an `eval` and a plain call all pass; a line
that is never reached does not.

TWO THINGS THE PROBE CANNOT SEE ON ITS OWN, and how each is covered:

  Dispatch alone cannot tell whether dev_capabilities.sh was sourced, because
  the harness defines the two names itself before the script starts. The EXIT
  trap therefore reports `bd_mod3_env_persist`, a sibling the library defines
  and the harness never touches.

  The probe replaces run_step, so it says nothing about what run_step DOES with
  the grade it was handed. The second half of this file extracts record() and
  run_step() from the script structurally and executes them: an `optional`
  command that fails must produce a WARN row and leave BLOCKING at 0, a `core`
  one must FAIL and block, and the argv must really be executed.

RED PROVENANCE, stated plainly rather than dressed up: the provisioner is
CORRECT at this base, so this gate is green on its parent by construction and
there is no defective-parent replay to run. The RED is the evasion battery --
each fixture is asserted to leave EVERY arm of the existing text gate green,
and is then rejected here -- plus the two benign fixtures that the existing arm
rejects and this one accepts. Backlog row 186 / v3.66.1234 has the same shape.

NOT CONSTRAINED, and deliberately: whether PostgreSQL actually comes up. That
is a live-host property; running the real body is the side effect this harness
exists to stop before, and the provisioner's own WARN row is what reports it.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

import provisioner_probe as probe_module
from provisioner_probe import CAPABILITIES, SCRIPT, SOURCE_WITNESS, build_tree
from shell_source import shell_code_only

# Its subject is a shipped operations script and the gate that judges it -- an
# invariant over the tree, not over any importable module.
BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parents[1]
SCAN_GATE = ROOT / "tests" / "test_v3_66_1064_provisioning_paths_do_not_diverge.py"

# MEASURED on test5 at this base: the extracted run_step unit runs in well under
# a second. max(30, 6 x measured) = 30s, under the suite's 240s bound.
UNIT_BUDGET_S = 30


# --------------------------------------------------------------------------
# structural surgery on the shipped script -- anchors located, never retyped
# --------------------------------------------------------------------------
def _script_lines() -> list[str]:
    return (ROOT / SCRIPT).read_text(encoding="utf-8").splitlines()


def _unique(lines: list[str], predicate, what: str) -> int:
    """The index of the one line matching, refusing an ambiguous anchor."""
    hits = [i for i, line in enumerate(lines) if predicate(line)]
    assert len(hits) == 1, (
        f"the fixture anchor for {what} matches {len(hits)} lines in {SCRIPT}, "
        f"expected exactly 1: {[lines[i] for i in hits][:3]}"
    )
    return hits[0]


def _capability_block(lines: list[str]) -> tuple[int, int]:
    """The two run_step calls, bounded by their own first and last lines.

    Bounded by the SLUG and by the second dispatch, never by the labels: those
    carry test counts that a later cut will edit, and an anchor that embeds a
    number is an anchor that goes stale silently.
    """
    start = _unique(lines, lambda l: l.startswith("run_step 08c_mod3_pg "), "the mod3 step")
    end = _unique(
        lines,
        lambda l: l.strip() == "bd_dev_inspect_provision || true",
        "the dev-inspect dispatch",
    )
    assert end > start, f"the capability block is inverted: {start} > {end}"
    return start, end


def _variant(mutate) -> str:
    """One provisioner variant, proven to differ from the shipped script."""
    lines = _script_lines()
    changed = mutate(list(lines))
    assert changed != lines, "the fixture is byte-identical to the shipped script"
    return "\n".join(changed) + "\n"


def _moved_below_the_verdict(lines: list[str]) -> list[str]:
    start, end = _capability_block(lines)
    block = lines[start:end + 1]
    return lines[:start] + lines[end + 1:] + [""] + block


def _never_taken_branch(lines: list[str]) -> list[str]:
    start, end = _capability_block(lines)
    return (
        lines[:start]
        + ["if false; then"]
        + ["    " + line for line in lines[start:end + 1]]
        + ["fi"]
        + lines[end + 1:]
    )


def _inside_an_uncalled_function(lines: list[str]) -> list[str]:
    start, end = _capability_block(lines)
    return (
        lines[:start]
        + ["provision_dev_capabilities() {"]
        + ["    " + line for line in lines[start:end + 1]]
        + ["}"]
        + lines[end + 1:]
    )


def _dispatched_twice(lines: list[str]) -> list[str]:
    start, end = _capability_block(lines)
    return lines[:end + 1] + lines[start:end + 1] + lines[end + 1:]


def _exits_before_the_verdict(lines: list[str]) -> list[str]:
    """Both capabilities really run, and nobody is ever told the result.

    The verdict table is the only place a WARN reaches the operator. A run that
    dispatches and then exits before printing it leaves the host missing a
    capability with nothing saying so -- which is the state backlog row 96 was
    filed about, reproduced with every text arm of the existing gate green.
    """
    _, end = _capability_block(lines)
    return lines[:end + 1] + ["exit 0"] + lines[end + 1:]


def _library_never_sourced(lines: list[str]) -> list[str]:
    index = _unique(
        lines,
        lambda l: l.strip() == 'if [ ! -r "$REPO/scripts/lib/dev_capabilities.sh" ]; then',
        "the dev_capabilities readability guard",
    )
    lines[index] = lines[index].replace("]; then", "] || true; then")
    return lines


def _graded_core(lines: list[str]) -> list[str]:
    index = _unique(lines, lambda l: l.startswith("run_step 08c_mod3_pg "), "the mod3 step")
    words = lines[index].split(" ")
    assert words.count("optional") == 1, f"expected one grade word: {lines[index]!r}"
    lines[index] = lines[index].replace(" optional ", " core ")
    return lines


def _one_line_invocation(lines: list[str]) -> list[str]:
    """The SAME dispatch, spelled without a line continuation."""
    start, end = _capability_block(lines)
    joined = "\n".join(lines[start:end + 1]).replace("\\\n", "")
    collapsed = [" ".join(line.split()) for line in joined.splitlines()]
    assert len(collapsed) == 2, f"expected two joined dispatches, got {collapsed}"
    return lines[:start] + collapsed + lines[end + 1:]


def _dispatched_through_a_variable(lines: list[str]) -> list[str]:
    """The SAME dispatch, with the function name held in a variable."""
    start, end = _capability_block(lines)
    out: list[str] = []
    for line in lines[start:end + 1]:
        named = [n for n in CAPABILITIES if line.strip() == f"{n} || true"]
        if not named:
            out.append(line)
            continue
        assert out and out[-1].rstrip().endswith("\\"), (
            f"the dispatch line for {named[0]} is not a continuation of a "
            f"run_step call: {out[-1:]!r}"
        )
        out.insert(len(out) - 1, f"_capability={named[0]}")
        out.append(line.replace(named[0], '"$_capability"'))
    assert sum(1 for l in out if l.startswith("_capability=")) == len(CAPABILITIES), out
    return lines[:start] + out + lines[end + 1:]


# The wrapper names deliberately do NOT end in the capability name: a longer
# identifier ending in the same text would make the existing gate's
# `f"{fn}()" not in code` redefinition arm fire on a fixture that redefines
# nothing, and this file would then be measuring its own naming accident.
_WRAPPERS = {
    "bd_mod3_pg_provision": "bd_dispatch_mod3_pg",
    "bd_dev_inspect_provision": "bd_dispatch_dev_inspect",
}


def _dispatched_through_a_wrapper(lines: list[str]) -> list[str]:
    """The SAME dispatch, one indirection deep, and the wrapper IS called."""
    start, end = _capability_block(lines)
    rewritten = "\n".join(lines[start:end + 1])
    definitions = []
    for name in CAPABILITIES:
        wrapper = _WRAPPERS[name]
        assert not wrapper.endswith(name), f"{wrapper} would shadow {name}"
        definitions.append(f"{wrapper}() {{ {name}; }}")
        assert rewritten.count(f"    {name} ") == 1, rewritten
        rewritten = rewritten.replace(f"    {name} ", f"    {wrapper} ")
    return lines[:start] + definitions + rewritten.splitlines() + lines[end + 1:]


def _dispatched_through_eval(lines: list[str]) -> list[str]:
    """The SAME dispatch, through the shell's own dispatch builtin."""
    start, end = _capability_block(lines)
    out = []
    for line in lines[start:end + 1]:
        named = [n for n in CAPABILITIES if line.strip() == f"{n} || true"]
        out.append(line.replace(named[0], f"eval {named[0]}") if named else line)
    assert sum(1 for l in out if "eval " in l) == len(CAPABILITIES), out
    return lines[:start] + out + lines[end + 1:]


def _dispatched_outside_any_run_step(lines: list[str]) -> list[str]:
    """Both capabilities really run -- and nothing grades, logs or reports them."""
    start, end = _capability_block(lines)
    return lines[:start] + [f"{name} || true" for name in CAPABILITIES] + lines[end + 1:]


def _both_under_one_run_step(lines: list[str]) -> list[str]:
    """One graded step covering two capabilities, so one WARN row hides which."""
    start, end = _capability_block(lines)
    head = lines[start]
    assert head.rstrip().endswith("\\"), f"the mod3 step is not a continuation: {head!r}"
    return (
        lines[:start]
        + ["bd_both_capabilities() {"]
        + [f"    {name}" for name in CAPABILITIES]
        + ["}", head, "    bd_both_capabilities || true"]
        + lines[end + 1:]
    )


def _an_extra_log_line(lines: list[str]) -> list[str]:
    start, _ = _capability_block(lines)
    return lines[:start] + ['echo "  dev capabilities:"'] + lines[start:]


# Each fixture is asserted, per test, to leave every arm of the existing text
# gate green -- that is what makes it an EVASION rather than a broken script --
# and each names the distinctive diagnostic this gate must answer with.
_EVASIONS = {
    "moved-below-the-verdict": (_moved_below_the_verdict, "was reached 0 times"),
    "wrapped-in-a-branch-never-taken": (_never_taken_branch, "was reached 0 times"),
    "inside-a-function-nobody-calls": (_inside_an_uncalled_function, "was reached 0 times"),
    "dispatched-twice": (_dispatched_twice, "was reached 2 times"),
    "library-never-sourced": (_library_never_sourced, f"{SOURCE_WITNESS} is undefined at exit"),
    "exits-before-the-verdict": (_exits_before_the_verdict, "never reached [9/9]"),
}

# Miswirings the behavioural half must grade for itself. Unlike _EVASIONS these
# are NOT claimed to leave every arm of the text gate green -- each trips at
# least the optional-not-core or the command-position arm -- and that is exactly
# why they are separated: a fixture's category is a claim, and an overstated one
# would be the same defect this row is about. They are here because inheriting
# "it is optional", "it is graded at all" and "it has a row of its own" from a
# scan is what row 228 refuses.
_MISWIRINGS = {
    "graded-core-not-optional": (_graded_core, "is dispatched as 'core'"),
    "dispatched-outside-any-run-step": (
        _dispatched_outside_any_run_step, "ran outside any run_step"),
    "both-under-one-run-step": (_both_under_one_run_step, "share the run_step"),
}

# Correct provisioners. Each must be accepted here; the first two are also
# REJECTED by the existing text arm, which is the other half of row 228.
_BENIGN = {
    "one-line-invocation": (_one_line_invocation, True),
    "dispatched-through-a-variable": (_dispatched_through_a_variable, True),
    "dispatched-through-a-wrapper": (_dispatched_through_a_wrapper, True),
    "dispatched-through-eval": (_dispatched_through_eval, True),
    "an-extra-log-line": (_an_extra_log_line, False),
}


# --------------------------------------------------------------------------
# the existing text gate, loaded so this file can measure what it accepts
# --------------------------------------------------------------------------
def _load_scan_gate():
    """Load test_v3_66_1064 by path, WITHOUT pytest's assertion rewriting.

    Its arms are called directly here, so only pass/fail is read; every one of
    them carries an explicit message anyway. Importing rather than restating its
    rule is deliberate: a copy of the predicate would drift from the gate this
    file is making claims about.
    """
    if str(ROOT / "tests") not in sys.path:
        sys.path.insert(0, str(ROOT / "tests"))
    spec = importlib.util.spec_from_file_location("_row228_scan_gate", SCAN_GATE)
    assert spec is not None and spec.loader is not None, f"cannot load {SCAN_GATE}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "_command_position_invocations"), (
        "the scan gate no longer exposes the command-position counter this file "
        "measures; row 228's subject has moved"
    )
    return module


def _scan_gate_complaints(tree: Path) -> list[str]:
    """Which arms of the existing text gate reject this provisioner tree."""
    module = _load_scan_gate()
    module._REPO = tree
    module._HOST = tree / SCRIPT
    module._LIB = tree / "scripts" / "lib" / "dev_capabilities.sh"
    complaints: list[str] = []
    for name in CAPABILITIES:
        for arm, call in (
            ("invokes-it", lambda: module.test_the_host_provisioner_actually_invokes_it(name)),
            ("no-redefine", lambda: module.test_neither_script_redefines_the_capability(
                "provision_test_host.sh", name)),
        ):
            try:
                call()
            except AssertionError as exc:
                complaints.append(f"{arm}[{name}]: {str(exc)[:120]}")
    for arm, call in (
        ("optional-not-core", module.test_the_capabilities_are_optional_not_core),
        ("sources-the-library",
         lambda: module.test_both_paths_source_the_library("provision_test_host.sh")),
    ):
        try:
            call()
        except AssertionError as exc:
            complaints.append(f"{arm}: {str(exc)[:120]}")
    return complaints


# --------------------------------------------------------------------------
# preconditions
# --------------------------------------------------------------------------
def test_the_harness_denominator_matches_the_library():
    """A third capability cannot enter the library and stay unexercised here."""
    code = shell_code_only(ROOT / "scripts" / "lib" / "dev_capabilities.sh")
    defined = {
        line.split("(")[0].strip()
        for line in code.splitlines()
        if line.strip().startswith("bd_") and "(" in line and line.rstrip().endswith("{")
    }
    assert defined, "no bd_* function definitions found in dev_capabilities.sh"
    provisioners = {name for name in defined if name.endswith("_provision")}
    assert provisioners == set(CAPABILITIES), (
        "the probe's frozen capability tuple and the library disagree, so this "
        f"battery would silently skip one: frozen={sorted(CAPABILITIES)} "
        f"library={sorted(provisioners)}"
    )
    assert SOURCE_WITNESS in defined, (
        f"{SOURCE_WITNESS} is no longer defined by dev_capabilities.sh, so the "
        "sourcing witness would report 'never sourced' for a correct script"
    )
    assert SOURCE_WITNESS not in provisioners, "the witness must not be a dispatch subject"


def test_the_scan_gate_still_accepts_the_shipped_script(tmp_path):
    """POSITIVE CONTROL for every evasion assertion below.

    If the mirrored tree were malformed, `no complaints` would stop meaning
    `this fixture evades the text gate` and start meaning nothing at all.
    """
    tree = build_tree(tmp_path)
    assert (tree / SCRIPT).read_bytes() == (ROOT / SCRIPT).read_bytes(), (
        "the mirrored provisioner is not byte-identical to the shipped one"
    )
    assert not _scan_gate_complaints(tree), _scan_gate_complaints(tree)


# --------------------------------------------------------------------------
# the behavioural half, against the shipped script
# --------------------------------------------------------------------------
def test_the_shipped_provisioner_really_dispatches_both_capabilities(tmp_path):
    """Source it, watch it run, and read what the shell actually did."""
    tree = build_tree(tmp_path)
    result = probe_module.run(tree, tmp_path / "scratch")
    assert result.dispatches, (
        "no run_step call was recorded at all, so the instrumentation did not "
        f"take and this run proves nothing: {result.describe()}"
    )
    assert result.reached_the_verdict(), result.describe()
    assert probe_module.dispatch_failures(result) == [], result.describe()
    for name in CAPABILITIES:
        arrivals = result.calls_to(name)
        assert len(arrivals) == 1, result.describe()
        assert arrivals[0].slug and arrivals[0].kind == "optional", result.describe()
        assert result.steps_for(name)[0].executed, (
            f"{name} was recorded but the harness never executed it, so 'reached "
            f"the function' was never measured: {result.describe()}"
        )
    assert result.defined_at_exit[SOURCE_WITNESS], result.describe()


def test_the_probe_leaves_the_real_provisioning_log_directory_alone(tmp_path):
    """The harness must not provision the host it is measuring."""
    real = Path("/tmp/bd_provision")
    before = (
        (real.stat().st_dev, real.stat().st_ino, real.stat().st_mtime_ns)
        if real.exists() else None
    )
    tree = build_tree(tmp_path)
    result = probe_module.run(tree, tmp_path / "scratch")
    assert result.reached_the_verdict(), result.describe()
    after = (
        (real.stat().st_dev, real.stat().st_ino, real.stat().st_mtime_ns)
        if real.exists() else None
    )
    assert after == before, (
        "the instrumented run touched the real /tmp/bd_provision; LOGDIR was not "
        "pinned into the fixture"
    )
    logs = tmp_path / "scratch" / "logs"
    assert logs.is_dir(), "the fixture log directory was never created"


def test_the_recorder_and_not_the_real_body_is_what_ran(tmp_path):
    """The harness's own safety precondition, measured rather than assumed.

    `readonly -f` is what stops the library's real bd_mod3_pg_provision from
    replacing the recorder -- and that body talks to a live PostgreSQL and
    writes a DSN into the invoking user's home. If the protection ever stopped
    holding, every run of this file would quietly start provisioning the host it
    is measuring, and every assertion above would still be green.

    Checked twice, once from each side: the shell reports which body is bound to
    the name at exit, and the isolated HOME is inspected for the artefact only
    the REAL body writes. A self-report alone would be the fail-open shape this
    row is about.
    """
    tree = build_tree(tmp_path)
    result = probe_module.run(tree, tmp_path / "scratch")
    assert result.reached_the_verdict(), result.describe()
    for name in CAPABILITIES:
        assert result.defined_at_exit.get(f"{name}#recorder"), (
            f"{name} is not bound to the harness recorder at exit, so the "
            f"library's real body ran: {result.defined_at_exit}"
        )
    home = tmp_path / "scratch" / "home"
    assert home.is_dir(), "the fixture HOME was never created, so the check below is empty"
    persisted = home / ".config" / "bd" / "mod3.env"
    assert not persisted.exists(), (
        "the real bd_mod3_pg_provision ran: it persisted a DSN into the "
        f"harness HOME at {persisted}. The recorder was replaced despite "
        "`readonly -f`, and this suite is provisioning the host it measures."
    )


def test_the_probe_survives_a_run_step_that_is_handed_no_command(tmp_path):
    """The instrumentation must be at least as `set -u`-safe as the script.

    THIS TEST EXISTS BECAUSE A DEFECT SHIPPED INTO THIS CUT. The stub's guard is
    written inside a Python f-string, where a single-braced `${1:-}` is a format
    field rather than shell text: it rendered as the digit 1 and the generated
    bash read `case "$1" in`. The provisioner runs under `set -u`, so the first
    run_step handed no command at all would have aborted the whole run -- and
    the abort would have looked exactly like the evasions this file rejects,
    turning a harness bug into a confident verdict about the subject.
    """
    prelude = probe_module._prelude(tmp_path / "probe", tmp_path / "logs", False)
    assert 'local first="${1:-}"' in prelude, (
        "the generated shell does not carry the unset-argument guard: "
        + repr([l for l in prelude.splitlines() if "case " in l or "first=" in l])
    )
    (tmp_path / "probe").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    script = tmp_path / "unset.sh"
    script.write_text(
        prelude + '\nset -uo pipefail\nrun_step slug label optional\necho SURVIVED\n',
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LC_ALL": "C",
             "TMPDIR": str(tmp_path), "_probe_python": sys.executable},
        capture_output=True, text=True, timeout=UNIT_BUDGET_S,
    )
    assert "SURVIVED" in completed.stdout, (
        "run_step with no command aborted the instrumented shell: "
        f"rc={completed.returncode} {completed.stderr.strip()[-300:]}"
    )
    assert "unbound variable" not in completed.stderr, completed.stderr[-300:]


# --------------------------------------------------------------------------
# evasions of the text gate
# --------------------------------------------------------------------------
@pytest.mark.parametrize("evasion", sorted(_EVASIONS))
def test_a_provisioner_that_never_dispatches_is_rejected(evasion, tmp_path):
    build, expected = _EVASIONS[evasion]
    text = _variant(build)
    tree = build_tree(tmp_path, script_text=text)
    syntax = subprocess.run(["bash", "-n", str(tree / SCRIPT)], capture_output=True, text=True)
    assert syntax.returncode == 0, f"{evasion} is not valid bash: {syntax.stderr}"
    complaints = _scan_gate_complaints(tree)
    assert not complaints, (
        f"{evasion} is rejected by the existing text gate ({complaints}), so it "
        "does not demonstrate the gap row 228 names"
    )
    result = probe_module.run(tree, tmp_path / "scratch")
    failures = probe_module.dispatch_failures(result)
    assert failures, f"{evasion} survived the behavioural battery: {result.describe()}"
    assert any(expected in failure for failure in failures), (
        f"{evasion} was rejected for the wrong reason: expected {expected!r} in "
        f"{failures}"
    )


@pytest.mark.parametrize("miswiring", sorted(_MISWIRINGS))
def test_a_miswired_dispatch_is_rejected(miswiring, tmp_path):
    """Graded wrong, graded by nothing, or two capabilities sharing one row."""
    build, expected = _MISWIRINGS[miswiring]
    tree = build_tree(tmp_path, script_text=_variant(build))
    syntax = subprocess.run(["bash", "-n", str(tree / SCRIPT)], capture_output=True, text=True)
    assert syntax.returncode == 0, f"{miswiring} is not valid bash: {syntax.stderr}"
    result = probe_module.run(tree, tmp_path / "scratch")
    assert result.reached_the_verdict(), (
        f"{miswiring} exited early, so the rejection below would be for the "
        f"wrong reason: {result.describe()}"
    )
    failures = probe_module.dispatch_failures(result)
    assert any(expected in failure for failure in failures), (
        f"{miswiring} was not rejected for {expected!r}: {failures} / "
        f"{result.describe()}"
    )


# --------------------------------------------------------------------------
# the over-sensitivity control
# --------------------------------------------------------------------------
@pytest.mark.parametrize("variant", sorted(_BENIGN))
def test_a_correct_provisioner_spelled_differently_is_accepted(variant, tmp_path):
    """Behavioralising a scan must not start failing on correct input.

    The two variants flagged `scanned_rejects` are the other half of row 228:
    they RUN, this gate accepts them, and the existing command-position arm
    rejects them. A gate that cries wolf gets switched off.
    """
    build, scanned_rejects = _BENIGN[variant]
    text = _variant(build)
    tree = build_tree(tmp_path, script_text=text)
    syntax = subprocess.run(["bash", "-n", str(tree / SCRIPT)], capture_output=True, text=True)
    assert syntax.returncode == 0, f"{variant} is not valid bash: {syntax.stderr}"
    result = probe_module.run(tree, tmp_path / "scratch")
    assert probe_module.dispatch_failures(result) == [], (
        f"the correct variant {variant} was rejected: {result.describe()}"
    )
    complaints = _scan_gate_complaints(tree)
    if scanned_rejects:
        assert any("invokes-it" in complaint for complaint in complaints), (
            f"{variant} was expected to trip the existing command-position arm "
            f"and did not; row 228's over-sensitivity claim needs re-measuring: "
            f"{complaints}"
        )
    else:
        assert not complaints, f"{variant} unexpectedly trips the text gate: {complaints}"


# --------------------------------------------------------------------------
# run_step's own grading, executed rather than read
# --------------------------------------------------------------------------
def _step_engine() -> str:
    """record() and run_step(), cut on STRUCTURE from the shipped script.

    A fixed line window stops covering its subject the moment anything is added
    above it, silently -- which is the defect shape this whole row is about.
    """
    lines = _script_lines()
    start = _unique(lines, lambda l: l == 'ROWS=""', "the verdict accumulator")
    definition = _unique(lines, lambda l: l.startswith("run_step() {"), "run_step's definition")
    assert definition > start, "run_step is defined before the accumulator it writes"
    end = next(i for i in range(definition, len(lines)) if lines[i] == "}")
    block = "\n".join(lines[start:end + 1]) + "\n"
    for required in ("record()", "PIPESTATUS", 'kind" = core'):
        assert required in block, f"the extracted engine is missing {required!r}: {block[:200]}"
    return block


def _drive_run_step(tmp_path, kind: str, command_rc: int):
    """Execute the real run_step with a recorder as its command."""
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    sentinel = tmp_path / "argv-executed"
    script = tmp_path / "engine.sh"
    script.write_text(
        "set -uo pipefail\n"
        f'LOGDIR="{logs}"\n'
        + _step_engine()
        # run_step pipes its command into tee, so the command runs in a SUBSHELL:
        # a shell variable set here would not survive. The sentinel is a file.
        + f'_probe_command() {{ printf \'ran\\n\' >> "{sentinel}"; return {command_rc}; }}\n'
        + f'run_step probe_slug "mod3 postgres" {kind} _probe_command\n'
        + 'printf "BLOCKING=%s WARNS=%s\\n" "$BLOCKING" "$WARNS"\n'
        + 'printf "ROWS:%s" "$ROWS"\n',
        encoding="utf-8",
    )
    syntax = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert syntax.returncode == 0, f"the extracted engine is not valid bash: {syntax.stderr}"
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=str(tmp_path),
        # Built from scratch, LC_ALL pinned: nothing here may depend on the
        # caller's locale or install-directory state.
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LC_ALL": "C",
             "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=UNIT_BUDGET_S,
    )
    return completed, sentinel


def test_run_step_really_executes_the_command_it_is_given(tmp_path):
    """The precondition every grading claim below rests on."""
    completed, sentinel = _drive_run_step(tmp_path, "optional", 0)
    assert sentinel.is_file(), (
        "run_step recorded a row without executing its argv: "
        f"{completed.stdout}{completed.stderr}"
    )
    assert sentinel.read_text(encoding="utf-8").count("ran") == 1, (
        f"the command ran {sentinel.read_text(encoding='utf-8')!r} times, expected once"
    )
    assert "mod3 postgres|OK|" in completed.stdout, completed.stdout
    assert "BLOCKING=0" in completed.stdout, completed.stdout


def test_an_optional_capability_that_fails_warns_and_does_not_block(tmp_path):
    """A box that cannot install PostgreSQL must WARN, visibly, and continue."""
    completed, sentinel = _drive_run_step(tmp_path, "optional", 1)
    assert sentinel.is_file(), "the failing command never ran"
    assert "mod3 postgres|WARN|" in completed.stdout, (
        "an optional step that failed did not record WARN: " + completed.stdout
    )
    assert "capability ABSENT" in completed.stdout, completed.stdout
    assert "BLOCKING=0 WARNS=1" in completed.stdout, (
        "an optional failure blocked the verdict, so a host that cannot install "
        "the capability now fails provisioning: " + completed.stdout
    )


def test_a_core_step_that_fails_blocks_the_verdict(tmp_path):
    """The negative control: `optional` must be doing real work in run_step."""
    completed, _ = _drive_run_step(tmp_path, "core", 1)
    assert "mod3 postgres|FAIL|" in completed.stdout, completed.stdout
    assert "BLOCKING=1 WARNS=0" in completed.stdout, (
        "a failed core step did not block the verdict, so the WARN result above "
        "does not distinguish optional from core: " + completed.stdout
    )


def test_the_probe_budget_is_below_the_suite_bound():
    """Row 1222's rule, restated where the constant lives."""
    assert probe_module.PROBE_BUDGET_S >= 30, probe_module.PROBE_BUDGET_S
    assert probe_module.PROBE_BUDGET_S < 240 and UNIT_BUDGET_S < 240, (
        "a subprocess budget at or above the 240s pytest bound can never fire; "
        "the test would be killed first and the timeout arm is dead code"
    )
