"""v3.66.796 -- the band runner must honour `teardown_module`.

`tools/run_tests.py` is the minimal pytest-compatible runner the release BAND
uses. It re-implements setup_method/teardown_method and the `bd_module_wipe`
marker, but it never called the module-level `teardown_module` hook.

That gap is not cosmetic. Six suites use the `_isolated_bd` idiom: set
BD_INSTALL_DIR in setup, restore it in `teardown_module`. Real pytest calls that
hook, so the stash suite is green. The band runner did not, so the env leaked
out of those files and every downstream DB suite wrote into the leaking suite's
tmpdir -- 71 failures across t3/t4/t5 inspectors, retention, events, and
maintenance. The band therefore reported failures that the binding gate (stash,
real pytest) did not have: a runner that cannot clean up produces a signal you
have to explain away, which is worth less than no signal.

These tests drive the runner as a SUBPROCESS. run_tests.py replaces
sys.modules["pytest"] with its own stub at import time, so importing it inside a
real pytest session would tear down the session running these very tests.
"""
import os
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO, "run_tests.py")


def _run_runner(*suite_paths, env=None):
    """Invoke the band runner on explicit absolute suite paths."""
    e = dict(os.environ)
    e.setdefault("BD_DISABLE_KEEPALIVE", "1")
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, RUNNER, *suite_paths],
        cwd=REPO, env=e, capture_output=True, text=True, timeout=300)


def test_runner_invokes_teardown_module(tmp_path):
    """The hook must actually fire. The synthetic suite records the call by
    writing a sentinel file, so this cannot pass by accident."""
    sentinel = tmp_path / "teardown_ran.txt"
    suite = tmp_path / "test_zz_synthetic_teardown.py"
    suite.write_text(textwrap.dedent(f"""
        def test_trivial():
            assert True

        def teardown_module(module):
            open({str(sentinel)!r}, "w").write("called")
    """))
    r = _run_runner(str(suite))
    assert sentinel.exists(), (
        "run_tests.py did not call teardown_module (stdout tail: %s)"
        % r.stdout[-800:])


def test_teardown_module_contains_an_env_leak_between_suites(tmp_path):
    """The behaviour that actually broke the band, reproduced end to end.

    Suite A mimics the `_isolated_bd` idiom -- it sets BD_INSTALL_DIR and only
    restores it in teardown_module. Suite B runs after it and records what it
    sees. Without the hook, B inherits A's directory; with it, B sees the
    original value. Asserting on B's observation (not on A's cleanup) is what
    makes this a test of the leak rather than of the sentinel.
    """
    seen = tmp_path / "seen.txt"
    leak_dir = tmp_path / "suite_a_home"
    leak_dir.mkdir()

    suite_a = tmp_path / "test_aa_leaker.py"
    suite_a.write_text(textwrap.dedent(f"""
        import os
        _SAVED = {{}}

        def test_sets_env():
            _SAVED["BD_INSTALL_DIR"] = os.environ.get("BD_INSTALL_DIR")
            os.environ["BD_INSTALL_DIR"] = {str(leak_dir)!r}
            assert True

        def teardown_module(module):
            v = _SAVED.get("BD_INSTALL_DIR")
            if v is None:
                os.environ.pop("BD_INSTALL_DIR", None)
            else:
                os.environ["BD_INSTALL_DIR"] = v
    """))

    suite_b = tmp_path / "test_bb_observer.py"
    suite_b.write_text(textwrap.dedent(f"""
        import os

        def test_records_env():
            open({str(seen)!r}, "w").write(
                os.environ.get("BD_INSTALL_DIR") or "<unset>")
            assert True
    """))

    r = _run_runner(str(suite_a), str(suite_b),
                    env={"BD_INSTALL_DIR": str(tmp_path / "original_home")})
    assert seen.exists(), "observer suite did not run: %s" % r.stdout[-800:]
    observed = seen.read_text().strip()
    assert observed != str(leak_dir), (
        "BD_INSTALL_DIR leaked from suite A into suite B (%s) -- "
        "teardown_module did not restore it" % observed)
    assert observed == str(tmp_path / "original_home"), observed


def test_teardown_module_failure_does_not_fail_the_suite(tmp_path):
    """Cleanup is best-effort, exactly like the runner's existing
    teardown_method handling: a raising teardown_module must not turn passing
    tests into failures (or the hook becomes a new way to break the band)."""
    suite = tmp_path / "test_zz_raising_teardown.py"
    suite.write_text(textwrap.dedent("""
        def test_passes():
            assert True

        def teardown_module(module):
            raise RuntimeError("boom")
    """))
    r = _run_runner(str(suite))
    assert "Failed: 0" in r.stdout, (
        "a raising teardown_module was surfaced as a test failure: %s"
        % r.stdout[-800:])
