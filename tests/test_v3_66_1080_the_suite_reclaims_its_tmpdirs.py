"""v3.66.1080 -- the suite reclaims what it allocates under /tmp.

MEASURED on the fleet 2026-08-13: `/tmp` held 15392 entries on test5 and grew
2373 in a single capture round, on every host, forever. CLAUDE.md section 0
already states the rule -- creating a path is a promise to remove it, and
nothing gates that promise -- and records the same shape at 744 directories.
It reached 15000.

WHY THE ROOT AND NOT THE CALL SITES. There are 579 `mkdtemp` call sites in
`tests/`, and **366 of them pass no prefix**, so their output is named `tmp*`
and cannot be attributed to a test by name: 6793 entries, 38% of the total,
invisible to any census that works backwards from the directory. Fixing call
sites would have closed the attributable half and missed that one entirely.
`tempfile.mkdtemp()` resolves its parent through `tempfile.tempdir`, so
conftest points that at a per-process root and removes it at the end -- which
covers every call site, prefixed or not, and every one written after this.

TWO GATES, because one cannot do both jobs:

  * this file is PORTABLE -- it drives the mechanism directly and means the
    same thing on a box, in CI, and in a container;
  * `test_the_fleet_leaks_no_tmpdirs` is BOX-ONLY and self-skips, because a
    count of /tmp is a fact about the machine rather than about the tree.

A gate that measured /tmp everywhere would assert something different on every
host, which is not a gate.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# It asserts an invariant about the whole suite's behaviour, not about a module.
BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parent.parent
_CONFTEST = _REPO / "tests" / "conftest.py"


def _drive(script: str) -> subprocess.CompletedProcess:
    """Run the mechanism in a FRESH interpreter and report what it did.

    Driven directly rather than by nesting pytest inside pytest -- the first
    attempt at that proved nothing in both directions at once: the probe file
    sat outside `tests/`, so conftest never loaded and both arms behaved
    identically, while the assertions globbed the ALREADY-REDIRECTED temp
    directory and looked in the wrong place.
    """
    # POP the flag, do not merely refrain from setting it. CLAUDE.md section 0:
    # a harness that copies os.environ cannot test the absence of a variable
    # the caller sets, and this file was measured failing for exactly that --
    # a full-suite control run exported KEEP_TEST_TMPDIRS=1, the child
    # inherited it, and the test written for the ENABLED path exercised the
    # disabled one.
    env = dict(os.environ, PYTHONPATH=str(_REPO / "tests"))
    env.pop("KEEP_TEST_TMPDIRS", None)
    return subprocess.run([sys.executable, "-c", script], cwd=str(_REPO),
                          capture_output=True, text=True, timeout=120, env=env)


_ALLOC = (
    "import sys, tempfile, pathlib\n"
    "import _tmproot\n"
    "root = _tmproot.install()\n"
    "made = [tempfile.mkdtemp(prefix='bd-1080-') for _ in range(5)]\n"
    "removed = _tmproot.finish({exit})\n"
    "print('ROOT', root)\n"
    "print('REMOVED', removed)\n"
    "print('SURVIVORS', sum(1 for d in made if pathlib.Path(d).exists()))\n"
)


def _field(out: str, key: str) -> str:
    for line in out.splitlines():
        if line.startswith(key + " "):
            return line[len(key) + 1:].strip()
    raise AssertionError(f"{key} missing from probe output:\n{out}")


def test_a_clean_run_reclaims_every_directory_it_made():
    """The assertion the cut exists for."""
    r = _drive(_ALLOC.format(exit=0))
    assert r.returncode == 0, r.stderr
    assert _field(r.stdout, "ROOT") != "None", (
        "the mechanism did not install, so nothing below is about it")
    assert _field(r.stdout, "REMOVED") == "True"
    assert _field(r.stdout, "SURVIVORS") == "0", (
        f"directories survived a clean run:\n{r.stdout}")


def test_the_control_shows_the_leak_is_real(monkeypatch):
    """The counterfactual. Without it, 'nothing survived' cannot be told from
    'nothing was allocated' -- a fixture that builds no subject is the
    commonest way a green test proves nothing (CLAUDE.md section 6)."""
    import pathlib as _pl
    r = subprocess.run(
        [sys.executable, "-c", _ALLOC.format(exit=0)], cwd=str(_REPO),
        capture_output=True, text=True, timeout=120,
        env=dict(os.environ, PYTHONPATH=str(_REPO / "tests"),
                 KEEP_TEST_TMPDIRS="1"))
    assert r.returncode == 0, r.stderr
    assert _field(r.stdout, "ROOT") == "None", (
        "KEEP_TEST_TMPDIRS did not disable the mechanism")
    assert _field(r.stdout, "SURVIVORS") == "5", (
        f"with the mechanism OFF the 5 directories should still be there; "
        f"if this is 0 the probe never allocated and the test above is "
        f"vacuous:\n{r.stdout}")
    for d in _pl.Path(tempfile.gettempdir()).glob("bd-1080-*"):
        d.rmdir()


def test_artifacts_survive_a_FAILING_run():
    """A debugging directory deleted on the one run that needed it is a worse
    defect than the leak this closes."""
    r = _drive(_ALLOC.format(exit=1))
    assert r.returncode == 0, r.stderr
    assert _field(r.stdout, "REMOVED") == "False", (
        "a non-zero exit still removed the root")
    assert _field(r.stdout, "SURVIVORS") == "5", (
        f"the failing run's artifacts were destroyed:\n{r.stdout}")
    import shutil as _sh
    _sh.rmtree(_field(r.stdout, "ROOT"), ignore_errors=True)


def test_installing_twice_does_not_nest_or_lose_the_first_root():
    """xdist workers and a stray double-configure must not strand a root that
    nothing then removes -- which would be this fix leaking by itself."""
    r = _drive(
        "import _tmproot\n"
        "a = _tmproot.install()\n"
        "b = _tmproot.install()\n"
        "print('SAME', a == b)\n"
        "print('REMOVED', _tmproot.finish(0))\n")
    assert r.returncode == 0, r.stderr
    assert _field(r.stdout, "SAME") == "True", (
        "a second install() made a second root; the first would never be "
        "removed")
    assert _field(r.stdout, "REMOVED") == "True"


def test_the_run_context_is_anchored_outside_the_reclaimed_root():
    """The subsystems whose output must OUTLIVE the run resolve their base at
    IMPORT, before conftest redirects.

    Found the hard way: resolving at call time put the run context inside the
    reclaimed root and deleted it with the rest, which broke two meta-tests
    that read a nested run's artifacts afterwards.
    """
    sys.path.insert(0, str(_REPO / "tests"))
    import _run_context
    assert hasattr(_run_context, "_TMP_AT_IMPORT"), (
        "_run_context no longer pins its base at import; its output will be "
        "reclaimed with the per-run root")
    assert "gettempdir" not in _run_context.sink_dir.__code__.co_names, (
        "sink_dir resolves the temp directory at CALL time again, so it "
        "follows tempfile.tempdir into the reclaimed root")


# ── the portable ratchet ─────────────────────────────────────────────────────

def test_conftest_still_installs_and_removes_the_root():
    """A source check, deliberately narrow: it guards the WIRING that the
    behavioural tests above assume, and would catch a rename that silently
    disconnects them."""
    src = _CONFTEST.read_text(encoding="utf-8")
    assert "_tmproot.install()" in src, (
        "conftest no longer installs the per-run temp root, so every test "
        "above is about a mechanism nothing switches on")
    # finish_session since v3.66.1152: the hook must also set the session's
    # exit status, so conftest calls the shared helper rather than finish()
    # directly. Accept either spelling -- the property is that the hook
    # delegates to _tmproot, not which of its two entry points it names.
    assert ("_tmproot.finish(" in src or "_tmproot.finish_session(" in src) \
        and "pytest_sessionfinish" in src, (
        "conftest no longer removes the root -- the leak returns silently")

    mech = (_REPO / "tests" / "_tmproot.py").read_text(encoding="utf-8")
    assert "KEEP_TEST_TMPDIRS" in mech, "the opt-out disappeared"
    assert "BD_KEEP_TEST_TMPDIRS" not in mech, (
        "a BD_-prefixed name would join test_gui_parity's env ledger and read "
        "as a promoted-but-unledgered config key (CLAUDE.md section 4)")


@pytest.mark.skipif(os.environ.get("FLEET_TMP_CHECK") != "1",
                    reason="box-only: a /tmp count is a fact about the machine, "
                           "not about the tree. Set FLEET_TMP_CHECK=1 on a "
                           "fleet host to arm it.")
def test_the_fleet_leaks_no_tmpdirs():
    """The box-only half. Deliberately skipped by default and loudly named, so
    a skip cannot be mistaken for a pass."""
    entries = len(list(Path("/tmp").iterdir()))
    assert entries < 5000, (
        f"/tmp holds {entries} entries. The suite reclaims its own since "
        f"v3.66.1080, so this is either a pre-existing backlog for `bd-gc` or "
        f"a new leaker outside tests/.")
