"""bd-wedge-hunt must not leave a remote pytest master running when it gives up.

BACKLOG 146. Five orphaned masters were found across the fleet at the end of the
~19h hunt on 2026-08-14, the oldest 41614s (11.6 HOURS), each a master plus up
to 48 idle workers. Load is this bug's dominant covariate -- reduced-size arms
are 0/73 against 15 of ~620 on full -- so an unreaped master progressively
corrupts the very measurement the hunt exists to take, in the direction that
INCREASES the apparent rate over time.

ROW 146's DIAGNOSIS WAS WRONG, AND THE CORRECTION IS THE POINT. It says the hunt
"captures forensics and sends SIGINT, but a master livelocked per row 145 is not
in a state that SIGINT unwinds, so the run simply stays". Read the code: the
WEDGE-CONFIRMED path sends SIGINT, waits `sigint_grace`, re-runs forensics, and
THEN sends `kill -9` to the process GROUP and the pid. That path is correct and
is not where the orphans came from. Diagnosing the one correct path as the
defect would have produced a fix that changed nothing.

THE ORPHANS COME FROM THE PATHS THAT ABANDON A RUN WITHOUT KILLING IT. Measured
by reading every terminal branch of the monitor loop at v3.66.1131:

  * INTERRUPT. `main` catches KeyboardInterrupt, sets STOP, and RETURNS. The
    host threads are `daemon=True`, so the interpreter kills them at exit: the
    remote run is never killed AND its row is never written. That is the whole
    of the five observed orphans, and it is why `rows.jsonl` contains ZERO
    abandoned rows -- they were not mis-recorded, they were never recorded.
  * --hours. Says "letting in-flight samples finish" and does the opposite: the
    monitor loop is `while not STOP.is_set()`, so it exits on the next tick and
    the row falls through to `setdefault("state", "COMPLETED")`. A run that was
    abandoned mid-flight is recorded as COMPLETED with no `pytest_exit`, which
    is a FALSE NEGATIVE in the wedge denominator. It never fired during the
    2026-08-14 hunt -- measured: 686 of 686 COMPLETED rows carry a real
    `pytest_exit` -- so no preserved row is contaminated. The defect is live
    regardless.
  * CAPPED. Kills the master pid ONLY. The wedge path three lines above kills
    the process GROUP. Same job, two branches, one of them leaves up to 48
    workers behind.
  * UNKNOWN. Records "the run was NOT killed and may still be there" and does
    not try. Honest, and still a leak.

WHY A STRUCTURAL TEST RATHER THAN A LIVE ONE. Driving the real abandon paths
needs a remote host, an ssh round trip and a wedged pytest master -- none of
which belongs in a band. These assertions read the tool's own source, which is
the same instrument `test_bd_ready_preflight.py` uses for the same reason, and
they are paired with a direct behavioural test of the one piece that CAN be
isolated: the command builder.

WHAT THIS FILE CANNOT SEE, stated because a gate that cannot say so is worse
than none: it does not prove a reap SUCCEEDS on a live host, only that every
abandon path issues one and that the command targets the group. A remote kill
that silently fails is outside its denominator.
"""

from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import os
import pathlib
import re
import signal
import subprocess
import time

# Its subject is one tool's source and one pure function in it, not the tree.
BD_GATE_SCOPE = "module"

REPO = pathlib.Path(__file__).resolve().parent.parent
HUNT = REPO / "toolchain" / "bin" / "bd-wedge-hunt"


def _load():
    """Import the extensionless, python-shebang tool as a module.

    `git ls-files -- '*.py'` cannot see this file and neither can a plain
    import; CLAUDE.md section 1 is about exactly this population.
    """
    spec = importlib.util.spec_from_loader(
        "bd_wedge_hunt_under_test",
        importlib.machinery.SourceFileLoader("bd_wedge_hunt_under_test", str(HUNT)),
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _source() -> str:
    return HUNT.read_text(encoding="utf-8")


def _string_constants() -> list[str]:
    """Every string LITERAL in the tool, with comments structurally excluded.

    A comment is inside the denominator of every gate that reads source text,
    and CLAUDE.md section 0 records four separate times an assertion in this
    repo could not tell prose from code -- including one where the comment
    written to explain a removal re-created the thing it described. This file
    is full of prose naming the very markers it asserts on, so it must not
    grep raw source. Comments never reach the AST, so reading literals out of
    it fixes the denominator for free.
    """
    return [n.value for n in ast.walk(ast.parse(_source()))
            if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def test_the_tool_exists_and_parses():
    """PRECONDITION. Without it, every assertion below is vacuous on a typo."""
    assert HUNT.is_file(), f"no bd-wedge-hunt at {HUNT}"
    ast.parse(_source())


def test_it_imports_without_side_effects():
    """PRECONDITION for the behavioural test: the module must be importable."""
    mod = _load()
    assert hasattr(mod, "STOP"), "module loaded but does not look like the hunt"


def test_a_reap_command_builder_exists_and_targets_the_process_GROUP():
    """The one piece testable in isolation, asserted behaviourally.

    A master is `setsid`-launched, so its workers share its process group. A
    kill aimed at the pid alone leaves up to 48 workers running -- which is the
    CAPPED path's bug. The builder must aim at the group.
    """
    mod = _load()
    assert hasattr(mod, "reap_cmd"), (
        "bd-wedge-hunt has no reap_cmd(). Every path that abandons a remote run "
        "needs ONE shared way to kill it; four branches hand-rolling their own "
        "is how the CAPPED path ended up killing the master and leaving its 48 "
        "workers. Backlog 146."
    )
    cmd = mod.reap_cmd("12345")
    assert "12345" in cmd, "the builder ignored the pid it was given"
    assert "pgid" in cmd.lower(), (
        "reap_cmd does not resolve the process GROUP. Killing the master alone "
        "leaves its workers running -- the exact leak backlog 146 measured."
    )
    assert re.search(r"kill\s+-9\s+-", cmd), (
        "reap_cmd never issues a negative-pid (process-group) kill -9. "
        f"built: {cmd!r}"
    )


def test_reap_cmd_actually_kills_a_real_process_GROUP():
    """THE SEAM, DRIVEN. A structural test cannot tell a correct kill from a
    plausible-looking one, and this is how that mattered:

    the first version of `reap_cmd` used `kill -0` for its liveness check and
    reported REAP-SURVIVED after successfully killing all five processes of a
    real tree -- because `kill -0` succeeds on a ZOMBIE, and a master whose
    parent has not wait()ed yet is exactly that. A gate firing on correct work
    gets switched off (section 0), so this arm exists to keep the verdict
    honest, not only the kill.

    Shape matched to production: `setsid` so the tree has its own process group,
    which is what the hunt's runner does.
    """
    mod = _load()
    proc = subprocess.Popen(
        ["setsid", "bash", "-c", "sleep 300 & sleep 300 & sleep 300 & sleep 300"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1)
        pgid = os.getpgid(proc.pid)

        def live_in_group():
            r = subprocess.run(["ps", "-eo", "pgid=,pid=,stat="],
                               capture_output=True, text=True)
            rows = [l.split() for l in r.stdout.splitlines() if l.split()]
            return [x for x in rows
                    if x[0] == str(pgid) and not x[2].startswith("Z")]

        before = live_in_group()
        # PRECONDITION: assert the fixture built the shape before judging it.
        # Without this, "nothing survived" and "nothing was ever there" are the
        # same green -- CLAUDE.md section 6.
        assert len(before) >= 4, (
            f"the fixture did not build a process group to kill (found "
            f"{len(before)}); this test would otherwise pass vacuously")

        out = subprocess.run(["bash", "-c", mod.reap_cmd(str(proc.pid))],
                             capture_output=True, text=True, timeout=60)
        time.sleep(1)
        after = live_in_group()

        assert not after, (
            f"{len(after)} process(es) survived the reap. Killing the master "
            "alone leaves its workers -- the CAPPED path's bug, backlog 146.")
        assert out.stdout.strip() == "REAP-OK", (
            f"the group WAS killed but reap_cmd reported {out.stdout.strip()!r}. "
            "A false SURVIVED is a gate firing on correct work: `kill -0` "
            "succeeds on a zombie, so the liveness probe must read the process "
            "STATE and treat Z as gone.")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass


def test_reap_cmd_can_still_report_a_survivor():
    """OVER-SENSITIVITY'S TWIN: the check must not be trivially always-OK.

    The zombie fix above moves the verdict toward reporting OK. Left
    unconstrained, "treat anything that is not clearly alive as reaped" would
    pass the test above while never being able to report a leak at all -- which
    is the failure the test above exists to prevent, inverted. So: strip the
    kills out of the built command and point its verdict logic at a process that
    is genuinely alive. It must say SURVIVED.
    """
    mod = _load()
    live = subprocess.Popen(["sleep", "300"])
    try:
        time.sleep(0.5)
        cmd = mod.reap_cmd(str(live.pid))
        # Neutralise only the two kills; the verdict logic is left untouched.
        neutered = (cmd.replace('kill -9 -"$G" 2>/dev/null;', "true;")
                       .replace('kill -9 "$P" 2>/dev/null;', "true;"))
        assert "kill -9" not in neutered, (
            "the kills were not neutralised, so this arm would kill its own "
            f"subject and prove nothing: {neutered!r}")
        out = subprocess.run(["bash", "-c", neutered],
                             capture_output=True, text=True, timeout=60)
        assert out.stdout.strip() == "REAP-SURVIVED", (
            f"reap_cmd reported {out.stdout.strip()!r} for a process that is "
            "alive and was never killed. The verdict cannot distinguish a leak "
            "from a clean reap, so REAP-OK means nothing.")
    finally:
        live.kill()
        live.wait()


def test_every_abandon_path_reaps():
    """No terminal branch may leave a remote master running.

    Read as SOURCE STRUCTURE rather than per-line: the abandon branches are
    multi-line and a line-scoped check cannot see a reap three lines below the
    state it is judging (CLAUDE.md section 0's shell-construct trap, in Python).
    """
    src = _source()
    tree = ast.parse(src)
    consts = _string_constants()

    # The branches that END a run. Each is identified by the state it records,
    # asserted over LITERALS so a comment naming a state cannot satisfy it.
    for state in ("CAPPED", "UNKNOWN", "ABANDONED"):
        assert state in consts, (
            f"no branch records state {state!r} as a string literal. If the "
            "monitor loop can end a run without recording a distinguishable "
            "state, an abandoned sample is indistinguishable from a completed "
            "one -- which is how a false COMPLETED enters the wedge "
            "denominator. Backlog 146."
        )

    # Every reap must go through the shared builder, so there is exactly one
    # definition of "kill it properly" to get right.
    #
    # EXEMPT reap_cmd's OWN BODY BY STRUCTURE, NOT BY A SUBSTRING. The first
    # draft filtered constants that did not contain the word "reap", which
    # flagged reap_cmd itself -- the one place the kill is supposed to live.
    # Walking to the FunctionDef and excluding its subtree asks the question
    # that was actually meant.
    builder = next((n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == "reap_cmd"),
                   None)
    assert builder is not None, "reap_cmd is not a module-level function"
    exempt = {id(n) for n in ast.walk(builder)}

    hand_rolled = sorted(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and "kill -9" in n.value and id(n) not in exempt
    )
    assert not hand_rolled, (
        "a hand-rolled `kill -9` survives outside reap_cmd, at line(s) "
        f"{hand_rolled}. Four branches with four kill spellings is what let the "
        "CAPPED path kill only the master and orphan its 48 workers. Route it "
        "through reap_cmd."
    )


def test_an_abandoned_run_is_not_recorded_as_COMPLETED():
    """A false COMPLETED is a false NEGATIVE in the wedge rate.

    The monitor loop is `while not STOP.is_set()`, so a STOP set by --hours or
    by an interrupt drops it out on the next tick with no `pytest_exit`. If the
    row then defaults to COMPLETED, an abandoned sample silently joins the
    denominator as a non-wedge.
    """
    consts = _string_constants()
    assert "ABANDONED" in consts, (
        "no ABANDONED state is ever recorded as a literal. The row still "
        "defaults to COMPLETED with no guard for the abandoned case, so a run "
        "dropped by STOP -- which has no pytest_exit -- is counted as a "
        "completed non-wedge, a false negative in the wedge rate."
    )
    assert "pytest_exit" in consts, "the completion marker vanished"

    # The ABANDONED branch must be GUARDED on the run not having finished, not
    # written unconditionally: an unconditional ABANDONED would mark every
    # completed sample abandoned, which passes the literal check above and
    # destroys the data. Over-sensitivity is a soundness bug (section 0).
    tree = ast.parse(_source())
    guarded = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.unparse(node.test)
        body_src = "\n".join(ast.unparse(b) for b in node.body)
        if "ABANDONED" in body_src and "pytest_exit" in test_src:
            guarded = True
    assert guarded, (
        "the ABANDONED state is not guarded on the absence of pytest_exit. It "
        "must fire only for a run that never finished; marking a completed "
        "sample abandoned would be the same defect pointing the other way."
    )


def test_the_interrupt_handler_does_not_promise_what_it_does_not_do():
    """CLAUDE.md section 10: the verdict line is the least-tested output.

    Two messages here were false. The interrupt said in-flight samples are NOT
    killed -- true, and a leak. `--hours` said it was 'letting in-flight samples
    finish', which the loop's own STOP predicate makes impossible. A message
    that misdescribes the behaviour beside it is how row 146 got its wrong
    diagnosis in the first place.
    """
    # Over LITERALS, not raw source. This test's own explanatory prose quotes
    # both retired phrases, and the first draft grepped the file -- it passed
    # only because the comment wrap happened to split one of them across a
    # newline. That is luck, not a check.
    said = " || ".join(_string_constants())
    assert "in-flight samples are NOT killed" not in said, (
        "the interrupt handler still advertises that it leaks. It should reap "
        "in-flight runs and join its threads so their rows are written."
    )
    assert "letting in-flight samples finish" not in said, (
        "--hours still claims to let in-flight samples finish. The monitor loop "
        "is `while not STOP.is_set()`, so they are abandoned on the next tick."
    )


def test_the_interrupt_joins_its_threads_so_rows_are_written():
    """Daemon threads die at interpreter exit, taking their unwritten rows.

    That is why `rows.jsonl` held ZERO abandoned rows after an interrupted 19h
    hunt: the samples were not mis-recorded, they were never recorded at all.
    """
    tree = ast.parse(_source())
    handlers = [n for n in ast.walk(tree)
                if isinstance(n, ast.ExceptHandler)
                and isinstance(n.type, ast.Name)
                and n.type.id == "KeyboardInterrupt"]
    assert handlers, "no KeyboardInterrupt handler found -- has main() changed?"

    # ASK FOR A THREAD JOIN, NOT FOR THE SUBSTRING "join". The first version of
    # this assertion was `"join" in ast.unparse(handler)`, and a mutation
    # battery escaped it immediately: the handler's own warning line contains
    # `", ".join(alive)`, so the str method satisfied a check written about
    # Thread.join. CLAUDE.md section 1 -- a predicate over the wrong part of
    # the syntax is worse than a grep, because it looks rigorous.
    #
    # The shape required: a `for` loop over the threads whose body joins the
    # loop variable.
    def _joins_its_loop_var(handler: ast.ExceptHandler) -> bool:
        for node in ast.walk(handler):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Name):
                continue
            var = node.target.id
            for call in ast.walk(node):
                if (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "join"
                        and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == var):
                    return True
        return False

    assert any(_joins_its_loop_var(h) for h in handlers), (
        "the KeyboardInterrupt handler does not join its host threads. They are "
        "daemon=True, so the interpreter kills them at exit: the remote run is "
        "never reaped AND its row is never written. That is the whole of "
        "backlog 146's five orphaned masters, and it is why rows.jsonl held "
        "ZERO abandoned rows after an interrupted 19h hunt. Join them (bounded) "
        "so each thread can reap and record."
    )
