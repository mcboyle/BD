"""v3.66.946 -- @945's leak guard fired on state it did not cause.

THE DEFECT, MEASURED. @945 added an autouse guard failing any test that leaves
BD_INSTALL_DIR set to a RELATIVE value, to stop the leak behind register item 34.
It checks the value only at teardown, so it cannot tell a value the test LEAKED
from one the test INHERITED. With a `.env` carrying `BD_INSTALL_DIR=v3`:

    BD_ENVFILE=<tmp>/.env venv/bin/python -m pytest tests/test_contracts.py -q
    -> 5 failed, 9 passed, 12 errors

Those tests are unrelated to environment handling. They fail because
`bulk_downloader/__init__.py:31` calls `_envfile.load_envfile()` at PACKAGE
IMPORT, so every test importing the product is seeded from that file before it
runs -- and `BD_INSTALL_DIR` is in `EDITOR_KEY_NAMES`, which means the GUI env
editor can write it to `~/BulkDownloader/.env` on the deploy host. One operator
save would have failed most of a capture with a message blaming whichever test
happened to run.

THIS IS THE RULE @945 SHIPPED, BROKEN BY @945'S OWN FIX. CLAUDE.md section 0:
"a test that varies an environment variable must POP that variable -- the
parent's value is part of the denominator." The guard is a harness that varies
nothing and reads everything, and it treated the parent's value as its own
subject. @945's CHANGELOG states that rule in the same commit that violates it,
which is section 0's fix-reproduces-the-defect line landing inside the fix for
an instance of it. The band could not catch this: no test in the 114-file band
runs with a `.env` present, so the condition never arose.

THE FIX IS A COMPARISON, NOT A CHECK. Fire only when the test CHANGED
BD_INSTALL_DIR into a relative value; an inherited one is an environment
condition, reported ONCE per session rather than blamed on 1268 tests.

WHY REPORT AT ALL RATHER THAN IGNORE. An inherited relative BD_INSTALL_DIR still
breaks every database-touching test, with `unable to open database file` and no
explanation -- exactly the four-files-away confusion item 34 took three readings
to see through. Silence would be the section 0 shape in the other direction. So
it is said once, loudly, naming the likely source and the consequence, and it
does not fail anything: unknown is a third state, and the honest report of an
environment condition is not a test failure.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PY = _REPO / "venv" / "bin" / "python"


def _verdict():
    sys.path.insert(0, str(_REPO / "tests"))
    import conftest
    return conftest.install_dir_leak_verdict


# ── the predicate, positive-controlled before anything depends on it ─────────

def test_the_verdict_distinguishes_leaked_from_inherited():
    """The whole cut in one assertion set.

    @945's predicate took ONE argument and could therefore only ever answer
    "is it relative now". These are the four states that matter, and the two
    middle ones are what the single-argument form conflated.
    """
    v = _verdict()
    assert v(None, "v3") == "leaked", (
        "a test that INTRODUCED a relative value must still fail -- that is "
        "item 34's original defect and the reason the guard exists")
    assert v("/abs/install", "v3") == "leaked", (
        "a test that CHANGED an absolute value into a relative one must fail")
    assert v("v3", "v3") == "inherited", (
        "an UNCHANGED relative value is the environment's, not this test's; "
        "blaming the test fails 1268 of them for one `.env` line")
    assert v("v3", "other-rel") == "leaked", (
        "a test that swapped one relative value for another still changed it")


def test_the_verdict_is_silent_on_every_healthy_shape():
    """Over-sensitivity is a soundness bug of equal weight (CLAUDE.md s0)."""
    v = _verdict()
    for before, after in ((None, None), ("/a", "/a"), ("/a", "/b"),
                          ("v3", None), ("/a", None), (None, ""), ("v3", "")):
        assert v(before, after) is None, (
            f"the guard flagged a healthy transition {before!r} -> {after!r}; "
            f"every legitimate fixture sets an absolute value or unsets it, so "
            f"a false positive here fires on most of the suite")


# ── end to end, which is the only layer the defect was visible at ────────────

def _run(target: str, envfile_body: str | None) -> dict:
    """Run pytest in a subprocess, optionally with a seeded `.env`.

    env= is set EXPLICITLY and BD_INSTALL_DIR is POPPED rather than merely
    omitted -- CLAUDE.md s0 records a harness that copied os.environ and so
    could never test the absence of a flag its own band sets.
    """
    d = tempfile.mkdtemp(prefix="inherit_probe_")
    out = Path(d) / "result.json"
    plugin = textwrap.dedent("""
        import json, os
        def pytest_sessionfinish(session, exitstatus):
            with open(os.environ["PROBE_OUT"], "w") as f:
                json.dump({"exitstatus": exitstatus}, f)
    """)
    (Path(d) / "probe.py").write_text(plugin, encoding="utf-8")

    child = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": os.environ.get("HOME", "/root"),
             "PYTHONPATH": f"{d}:{_REPO}",
             "PROBE_OUT": str(out),
             "BD_DISABLE_KEEPALIVE": "1"}
    child.pop("BD_INSTALL_DIR", None)
    if envfile_body is not None:
        envpath = Path(d) / ".env"
        envpath.write_text(envfile_body, encoding="utf-8")
        child["BD_ENVFILE"] = str(envpath)

    proc = subprocess.run(
        [str(_PY), "-m", "pytest", "-p", "probe", target, "-q", "-p", "no:randomly"],
        cwd=str(_REPO), env=child, capture_output=True, text=True, timeout=900)
    return {"exitstatus": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


# A suite with nothing to do with environment handling. If an inherited `.env`
# value can fail THIS, it can fail anything.
_BYSTANDER = "tests/test_contracts.py"


def test_the_probe_sees_the_bystander_suite_pass_normally():
    """The control. Without it, a green result below could mean the target
    never ran, which is the failure mode section 0 is about."""
    got = _run(_BYSTANDER, envfile_body=None)
    assert got["exitstatus"] == 0, (
        "the bystander suite does not pass even WITHOUT a .env, so this file "
        "cannot attribute anything to the guard:\n" + got["stdout"][-2000:])


def test_the_guard_contributes_nothing_to_an_inherited_bad_environment():
    """RED on pristine: the guard turned 12 of these into ERRORS at teardown.

    THE FIRST VERSION OF THIS ASSERTION WAS OVER-SCOPED and the measurement
    corrected it. It asserted the bystander suite PASSES with a relative
    BD_INSTALL_DIR inherited from `.env`. It does not, and it should not: a
    relative install dir genuinely breaks the app, so 5 of these tests fail on
    their own merits and always did. Measured either side of the fix:

        @945 guard : 5 failed, 9 passed, 12 ERRORS
        @946 guard : 5 failed, 9 passed,  0 errors, 1 warning

    The subject is the guard's CONTRIBUTION, not the suite's verdict. Asserting
    the verdict makes the denominator "every failure in the run" when the
    question is "which failures are mine" -- and it would have sent me trying to
    fix an app-level consequence that is not this cut's business. CLAUDE.md
    section 1: a verification can answer a different question than the item asks.
    """
    got = _run(_BYSTANDER, envfile_body="BD_INSTALL_DIR=v3\n")
    blob = got["stdout"] + got["stderr"]
    assert "this test left BD_INSTALL_DIR" not in blob, (
        "the guard blamed a test for a value it INHERITED. `.env` is seeded at "
        "package import by bulk_downloader/__init__.py and BD_INSTALL_DIR is an "
        "EDITOR_KEY_NAME, so one save through the GUI env editor puts this in "
        "front of all 1268 tests.\n" + blob[-2500:])
    assert " error" not in got["stdout"].rsplit("\n", 3)[-2].lower(), (
        "the run's summary line still reports errors; the guard raises at "
        "TEARDOWN, which pytest reports as an error rather than a failure, so "
        "that is where its contribution shows up.\n" + got["stdout"][-1200:])


def test_the_guard_still_catches_a_real_leak():
    """The direction that must NOT be lost.

    A fix that simply stopped firing would pass every assertion above and
    reopen item 34. Proving only the permissive half is the default mistake and
    it is invisible, because everything is green either way.

    THE FIRST VERSION OF THIS TEST MEASURED NOTHING, and the way it failed is
    worth keeping. It wrote a synthetic leaking test into a tmpdir and ran it --
    but pytest loads `conftest.py` from the TARGET FILE'S ancestors, and a file
    under /tmp has none, so the guard was never installed. The run reported
    "1 passed" and the assertion read that as "the guard is broken". A harness
    whose subject is absent reports about something else entirely (CLAUDE.md s0),
    and here it would have sent me rewriting a guard that was working.

    The leak is now injected into a REAL repo test through a plugin hook, so the
    real conftest, the real fixture and the real ordering are all in play.
    """
    plugin_dir = tempfile.mkdtemp(prefix="leakinject_")
    (Path(plugin_dir) / "leakinject.py").write_text(textwrap.dedent("""
        import os
        def pytest_runtest_call(item):
            # Inside the test's own call phase, so the guard's teardown sees a
            # value that differs from what it recorded at setup.
            os.environ["BD_INSTALL_DIR"] = "deliberately-relative"
    """), encoding="utf-8")

    d = tempfile.mkdtemp(prefix="inherit_probe_")
    child = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": os.environ.get("HOME", "/root"),
             "PYTHONPATH": f"{plugin_dir}:{_REPO}",
             "BD_DISABLE_KEEPALIVE": "1"}
    child.pop("BD_INSTALL_DIR", None)
    proc = subprocess.run(
        [str(_PY), "-m", "pytest", "-p", "leakinject", _BYSTANDER,
         "-q", "-p", "no:randomly"],
        cwd=str(_REPO), env=child, capture_output=True, text=True, timeout=900)

    assert proc.returncode != 0, (
        "a test that genuinely leaks a relative BD_INSTALL_DIR was NOT caught. "
        "The guard has been relaxed into decoration and item 34 is reopened.\n"
        + proc.stdout[-2000:])
    assert "BD_INSTALL_DIR" in proc.stdout, (
        "the failure does not name the variable, so the next reader gets the "
        "same four-files-away confusion item 34 took three readings to solve")


def test_the_repair_restores_what_the_test_started_with():
    """Closes a mutation escape: repairing by POPPING passed every test above.

    The guard repairs before it fails, so the leak does not cascade and bury the
    leaker. But repairing by removing the key discards a legitimate INHERITED
    value -- every test after the leaker would then run without the install dir
    the operator configured, and the run would drift from the environment it was
    asked to measure. A "must be restored" property that nothing reads is
    indistinguishable from a "must be removed" one; only a positive control
    separates them.
    """
    plugin_dir = tempfile.mkdtemp(prefix="repairprobe_")
    (Path(plugin_dir) / "repairprobe.py").write_text(textwrap.dedent("""
        import json, os
        _n = [0]
        def pytest_runtest_call(item):
            _n[0] += 1
            if _n[0] == 1:                      # exactly one leaker
                os.environ["BD_INSTALL_DIR"] = "leaked-relative"
        def pytest_sessionfinish(session, exitstatus):
            with open(os.environ["REPAIR_OUT"], "w") as f:
                json.dump({"final": os.environ.get("BD_INSTALL_DIR")}, f)
    """), encoding="utf-8")

    out = Path(plugin_dir) / "result.json"
    original = "/tmp/an-absolute-install-dir"
    child = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
             "HOME": os.environ.get("HOME", "/root"),
             "PYTHONPATH": f"{plugin_dir}:{_REPO}",
             "REPAIR_OUT": str(out),
             "BD_DISABLE_KEEPALIVE": "1",
             "BD_INSTALL_DIR": original}
    subprocess.run(
        [str(_PY), "-m", "pytest", "-p", "repairprobe", _BYSTANDER,
         "-q", "-p", "no:randomly"],
        cwd=str(_REPO), env=child, capture_output=True, text=True, timeout=900)

    assert out.exists(), "the probe never ran to session end"
    final = json.loads(out.read_text())["final"]
    assert final == original, (
        f"after a leak, BD_INSTALL_DIR is {final!r} but the session started at "
        f"{original!r}. The repair must put back what the LEAKING TEST started "
        f"with, not remove the key -- otherwise one leaker silently strips the "
        f"operator's configured install dir from every test after it.")


def test_an_inherited_bad_value_is_reported_rather_than_ignored():
    """Unknown is a third state. Silence would trade one s0 shape for the other.

    An inherited relative value still breaks every database-touching test with
    `unable to open database file` and no explanation. It must not FAIL the run,
    and it must not be silent either.
    """
    got = _run(_BYSTANDER, envfile_body="BD_INSTALL_DIR=v3\n")
    blob = got["stdout"] + got["stderr"]
    assert "BD_INSTALL_DIR" in blob, (
        "an inherited relative BD_INSTALL_DIR produced no mention at all. The "
        "operator gets a run that behaves strangely with nothing pointing at "
        "the cause -- the same silence item 34 hid behind.")
