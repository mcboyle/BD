"""v3.66.945 -- a test leaked BD_INSTALL_DIR='v3' and poisoned every later test.

WHAT THIS FIXES, MEASURED RATHER THAN REASONED. Register item 34 carried four
"order-dependent band failures" (webhooks-SSRF x3, vpn-quarantine x1) that fail
in a multi-file band and pass 15/15 in isolation. The name was misdirection:
they are neither SSRF nor VPN failures, and the order-dependence is a symptom.

Found by wrapping the resources rather than reading the code (CLAUDE.md s0). Two
instruments, each proven in BOTH directions before its result was believed:

  * a cwd probe at every test boundary, proven able to detect a deleted cwd,
    reported **0 broken boundaries** across the full 113-suite band. That killed
    the obvious hypothesis and is why the search moved to the call itself.
  * a `sqlite3.connect` wrapper and an `os.environ.__setitem__` wrapper, proven
    to record a known-bad connect / a relative write and to stay silent on a
    good one, named the leak outright.

THE CHAIN, end to end:

  1. `test_v3_66_940_...::test_every_declared_key_can_be_seeded` builds
     `{k: f"v{i}" for i, k in enumerate(EF.EDITOR_KEY_NAMES)}`. BD_INSTALL_DIR
     is index 3, so its value is the string "v3" -- RELATIVE.
  2. `_envfile.load_envfile()` writes each key with `os.environ[k] = v`. That is
     correct product behaviour and it is INVISIBLE TO MONKEYPATCH.
  3. `clean_env` had popped the key on entry, so monkeypatch recorded nothing to
     restore; `undo()` therefore cannot remove what the code added. The value
     survives the test and the session.
  4. Worse, it self-propagates: every LATER test that monkeypatch-sets
     BD_INSTALL_DIR has its `undo()` RESTORE "v3" rather than delete the key.
     14 of the 15 captured writes are exactly that.
  5. `_resolve_db_path()` joins "v3" onto the victim's tmp cwd, giving
     `<tmp>/v3/downloader_history.db` whose parent never exists, and
     `sqlite3.connect` raises `unable to open database file`.

So the victims are simply the next tests to touch the database.

THE RULE THIS INVERTS. CLAUDE.md s0 says a test that VARIES an environment
variable must POP it, because the parent's value is part of the denominator.
This is its mirror: **a test that exercises a real environment WRITER must
contain the write.** `monkeypatch` can only undo what it recorded, and a direct
`os.environ[k] = v` inside the code under test is not recorded. Popping on entry
is necessary and not sufficient.

WHY A GUARD AND NOT JUST A FIX. The one-line repair to `clean_env` closes this
instance. Nothing would stop the next test that calls a real env writer, and the
failure surfaces four files away under a name that describes none of it -- this
item was misread three times before the instruments were built. The guard makes
the leaker fail instead of the victim.

DELIBERATELY NARROW: the guard checks BD_INSTALL_DIR for a RELATIVE value, not
every BD_ key for any change. A relative BD_INSTALL_DIR is never legitimate --
`_resolve_db_path()` joins it with a relative DB_PATH and resolves the result
against the cwd -- whereas a general env-diff guard over 1200+ test files would
fire on legitimate fixtures and get switched off, which s0 weighs equally with a
false clean. Measured denominator for that claim: across the 113-suite band
(1461 tests) exactly ONE test wrote a relative BD_INSTALL_DIR.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_PY = _REPO / "venv" / "bin" / "python"
_LEAKY = "tests/test_v3_66_940_envfile_seeds_only_declared_keys.py"


# ── the guard's predicate, tested directly ───────────────────────────────────

def _predicate():
    """The conftest guard's check, imported rather than restated.

    A second copy of the rule in this file is one more thing that can drift from
    the code it describes -- the defect CLAUDE.md section 8 names for the two
    "tools" populations and section 3 names for the version-pin greps.
    """
    sys.path.insert(0, str(_REPO / "tests"))
    import conftest
    return conftest.relative_install_dir_leak


def test_the_guards_predicate_actually_discriminates():
    """Positive control, written before the gate that depends on it.

    @944's mutation battery escaped once because a "must be empty" assertion
    cannot tell a real empty from a constant one. The same applies to a
    predicate that must return None on a clean tree: prove it can return
    something.
    """
    leak = _predicate()
    assert leak({"BD_INSTALL_DIR": "v3"}) == "v3", (
        "the guard did not flag a relative value -- it is the entire subject")
    assert leak({"BD_INSTALL_DIR": "relative/path"}) == "relative/path"
    assert leak({"BD_INSTALL_DIR": "/tmp/abs"}) is None, (
        "the guard flagged an ABSOLUTE value; every legitimate fixture sets one "
        "of those, so this would fire on most of the suite and be switched off")
    assert leak({}) is None, "the guard flagged an unset variable"
    assert leak({"BD_INSTALL_DIR": ""}) is None, (
        "the guard flagged an empty value; _resolve_db_path treats it as unset")


# ── the leak itself, end to end ──────────────────────────────────────────────

def _env_after(target: str) -> dict:
    """Run `target` in a subprocess and report the env it leaves behind.

    A subprocess because the question is what survives a whole pytest session,
    and this process is inside one. `env=` is set EXPLICITLY rather than
    inherited: CLAUDE.md section 0 records a harness that copied os.environ and
    could therefore never test the absence of a flag its own band sets.
    """
    plugin = textwrap.dedent("""
        import json, os
        def pytest_sessionfinish(session, exitstatus):
            with open(os.environ["LEAKPROBE_OUT"], "w") as f:
                json.dump({"BD_INSTALL_DIR": os.environ.get("BD_INSTALL_DIR"),
                           "exitstatus": exitstatus}, f)
    """)
    import tempfile
    d = tempfile.mkdtemp(prefix="leakprobe_")
    (Path(d) / "leakprobe.py").write_text(plugin, encoding="utf-8")
    out = Path(d) / "result.json"

    child = {k: v for k, v in os.environ.items()
             if k in ("PATH", "HOME", "LANG", "TMPDIR", "PYTEST_CURRENT_TEST")}
    child["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    child["PYTHONPATH"] = f"{d}:{_REPO}"
    child["LEAKPROBE_OUT"] = str(out)
    child["BD_DISABLE_KEEPALIVE"] = "1"
    child.pop("BD_INSTALL_DIR", None)          # pop, never merely omit

    proc = subprocess.run(
        [str(_PY), "-m", "pytest", "-p", "leakprobe", target, "-q", "-p", "no:randomly"],
        cwd=str(_REPO), env=child, capture_output=True, text=True, timeout=600)
    if not out.exists():
        raise AssertionError(
            f"probe wrote nothing; pytest exited {proc.returncode}\n"
            f"{proc.stdout[-2000:]}\n{proc.stderr[-800:]}")
    return {**json.loads(out.read_text()), "stdout": proc.stdout}


def test_the_probe_can_see_a_leak_at_all():
    """The instrument, before the measurement.

    Runs a file that deliberately sets a relative BD_INSTALL_DIR without any
    cleanup. If the probe reports None here it cannot see its subject, and the
    clean result below would be worthless.
    """
    import tempfile
    d = tempfile.mkdtemp(prefix="leakctl_")
    f = Path(d) / "test_ctl_leaks.py"
    f.write_text(textwrap.dedent("""
        import os
        def test_leaks_on_purpose():
            os.environ["BD_INSTALL_DIR"] = "deliberately-relative"
            assert True
    """), encoding="utf-8")
    got = _env_after(str(f))
    assert got["BD_INSTALL_DIR"] == "deliberately-relative", (
        f"the probe cannot observe a leak it was pointed straight at: {got!r}. "
        f"Every clean result from it would be a gate that cannot see its subject.")


def test_the_envfile_suite_leaves_no_relative_install_dir():
    """RED on pristine: BD_INSTALL_DIR == 'v3' survives the whole @940 file.

    This is item 34's root cause, asserted at the layer where it is a fact about
    the suite rather than about any one victim.
    """
    got = _env_after(_LEAKY)
    v = got["BD_INSTALL_DIR"]
    assert v is None or os.path.isabs(v), (
        f"{_LEAKY} left BD_INSTALL_DIR={v!r} in the environment. A relative "
        f"value makes _resolve_db_path() join it onto whatever cwd the NEXT "
        f"test happens to have, so the database lands in a directory that does "
        f"not exist and sqlite3 raises `unable to open database file` four "
        f"files away. See item 34.")


def test_that_suite_still_passes():
    """The fix must not be 'stop exercising the writer'.

    Containing a write is the goal; deleting the assertion that the write
    HAPPENS would satisfy the test above and destroy @940's guarantee.
    """
    got = _env_after(_LEAKY)
    assert got["exitstatus"] == 0, (
        "the @940 suite no longer passes:\n" + got["stdout"][-2000:])
    # No assertion on stdout: pytest's -q output does not reliably name
    # individual passing tests, which is why this carried an `or True` and
    # therefore asserted nothing at all. The exitstatus check above is the
    # real guarantee that @940's suite still passes.
