"""v3.66.797 -- the band runner gains `--isolate`: serial subprocess-per-file.

Stash's binding gate runs `capture.sh --workers=60` -- every test FILE gets its
own interpreter process, so cross-file state (os.environ, sys.modules globals,
cwd artifacts) structurally CANNOT leak between files. The sandbox band ran the
same files SINGLE-BOOT in one process, so leaks that are impossible on stash
happen only in the sandbox. That mismatch forced the band to be read
DIFFERENTIALLY ("same failure names as baseline") instead of absolutely -- and a
gate you explain away every cut is where a real regression hides.

v3.66.796 fixed the subset of leaks that a `teardown_module` hook cleans up
(71 -> 25 band failures). This cut closes the REST of the leak class at the
execution-model level: `--isolate` runs each file through the same
subprocess-per-file machinery `--workers` already uses (fresh interpreter,
fresh per-file BD_HOME tmpdir + cwd, per-file wall timeout), just serially.
No leak idiom survives a process boundary, known or not-yet-invented.

Semantics under test:
  - `--isolate` isolates os.environ mutations between files (no teardown needed)
  - `--isolate` isolates sys.modules / module-global mutations between files
  - `--isolate` gives each file its own cwd + BD_HOME (cwd-relative runtime
    artifacts cannot collide)
  - a REAL failure still fails the run (exit 1) and is named in the JSON
    artifact -- isolation must not eat signal
  - `--isolate` with a ::-filter falls back to in-process serial, LOUDLY
    (the subprocess path runs whole files; a silent fallback would be a band
    that claims isolation it is not providing)

These tests drive the runner as a SUBPROCESS. run_tests.py replaces
sys.modules["pytest"] with its own stub at import time, so importing it inside
a real pytest session would tear down the session running these very tests.
"""
import json
import os
import subprocess
import sys
import textwrap

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNNER = os.path.join(REPO, "run_tests.py")


def _run_runner(*args, env=None):
    """Invoke the band runner with explicit args (absolute suite paths + flags)."""
    e = dict(os.environ)
    e.setdefault("BD_DISABLE_KEEPALIVE", "1")
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, RUNNER, *args],
        cwd=REPO, env=e, capture_output=True, text=True, timeout=300)


def test_isolate_contains_env_leak_without_any_teardown(tmp_path):
    """The residual leak idiom, reproduced end to end.

    Suite A mutates os.environ and NEVER restores it -- no teardown_module, so
    the v3.66.796 hook cannot help. Suite B records what it sees. Single-boot,
    B inherits A's value; under --isolate each file is its own process, so B
    must see the original. Asserting on B's observation is what makes this a
    test of the isolation model rather than of any cleanup convention.
    """
    seen = tmp_path / "seen.txt"
    suite_a = tmp_path / "test_aa_raw_leaker.py"
    suite_a.write_text(textwrap.dedent("""
        import os

        def test_leaks_env_and_never_cleans_up():
            os.environ["BD_ISO_PROBE_797"] = "leaked-from-A"
            assert True
    """))
    suite_b = tmp_path / "test_bb_observer.py"
    suite_b.write_text(textwrap.dedent(f"""
        import os

        def test_records_env():
            open({str(seen)!r}, "w").write(
                os.environ.get("BD_ISO_PROBE_797") or "<unset>")
            assert True
    """))
    r = _run_runner(str(suite_a), str(suite_b), "--isolate",
                    env={"BD_ISO_PROBE_797": "original"})
    assert seen.exists(), "observer suite did not run: %s" % r.stdout[-800:]
    observed = seen.read_text().strip()
    assert observed == "original", (
        "os.environ leaked across files under --isolate (observer saw %r) -- "
        "files are not running in separate processes" % observed)


def test_isolate_contains_sys_modules_global_leak(tmp_path):
    """The library/verify_chain/collect_data failure shape: a shared imported
    module's global is mutated by one file and read, dirty, by the next.
    Single-boot, sys.modules caches the helper so the mutation is visible
    downstream; under --isolate each file imports a pristine copy."""
    seen = tmp_path / "seen_state.txt"
    (tmp_path / "iso_helper_797.py").write_text("STATE = {'value': 'pristine'}\n")
    suite_a = tmp_path / "test_aa_module_mutator.py"
    suite_a.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(tmp_path)!r})
        import iso_helper_797

        def test_mutates_shared_module_global():
            iso_helper_797.STATE["value"] = "dirty-from-A"
            assert True
    """))
    suite_b = tmp_path / "test_bb_module_observer.py"
    suite_b.write_text(textwrap.dedent(f"""
        import os, sys
        sys.path.insert(0, {str(tmp_path)!r})
        import iso_helper_797

        def test_records_module_state():
            open({str(seen)!r}, "w").write(iso_helper_797.STATE["value"])
            assert True
    """))
    r = _run_runner(str(suite_a), str(suite_b), "--isolate")
    assert seen.exists(), "observer suite did not run: %s" % r.stdout[-800:]
    observed = seen.read_text().strip()
    assert observed == "pristine", (
        "module-global state leaked across files under --isolate (observer "
        "saw %r) -- sys.modules is being shared between files" % observed)


def test_isolate_gives_each_file_a_private_bd_home(tmp_path):
    """cwd-relative runtime state (downloader_history.db et al.) collides via
    a SHARED BD_HOME. (The runner already sandboxes each TEST's cwd into a
    tmpdir, so cwd-artifact visibility is not a usable probe -- BD_HOME is the
    channel that actually leaks.) Under --isolate each file must get its OWN
    BD_HOME, distinct per file and overriding the parent's value -- the same
    contract the --workers path already provides."""
    homes = tmp_path / "homes.txt"
    parent_home = tmp_path / "parent_home"
    parent_home.mkdir()
    body = textwrap.dedent(f"""
        import os

        def test_records_bd_home():
            with open({str(homes)!r}, "a") as fh:
                fh.write(os.environ.get("BD_HOME", "<unset>") + "\\n")
            assert True
    """)
    suite_a = tmp_path / "test_aa_home_recorder.py"
    suite_a.write_text(body)
    suite_b = tmp_path / "test_bb_home_recorder.py"
    suite_b.write_text(body)
    r = _run_runner(str(suite_a), str(suite_b), "--isolate",
                    env={"BD_HOME": str(parent_home)})
    lines = [ln for ln in homes.read_text().splitlines() if ln] \
        if homes.exists() else []
    assert len(lines) == 2, (
        "both files should have recorded BD_HOME: %r (stdout tail: %s)"
        % (lines, r.stdout[-800:]))
    assert lines[0] != lines[1], (
        "files shared one BD_HOME under --isolate: %r" % lines)
    assert "<unset>" not in lines and str(parent_home) not in lines, (
        "per-file BD_HOME did not override the parent value: %r" % lines)


def test_isolate_real_failure_still_fails_and_is_named_in_json(tmp_path):
    """Isolation must not eat signal: a genuinely failing test fails the run
    (exit 1) and comes back NAMED in the JSON artifact (schema v2 `tests`
    round-trips through the worker), not as an anonymous placeholder."""
    suite = tmp_path / "test_zz_really_fails.py"
    suite.write_text(textwrap.dedent("""
        def test_passes():
            assert True

        def test_genuinely_broken():
            assert 1 == 2, "intentional failure"
    """))
    out = tmp_path / "band.json"
    r = _run_runner(str(suite), "--isolate", f"--json={out}")
    assert r.returncode == 1, (
        "a real failure did not fail the isolated run (exit %s): %s"
        % (r.returncode, r.stdout[-800:]))
    data = json.loads(out.read_text())
    assert data["failed"] == 1 and data["passed"] == 1, data
    names = {t["test"] for t in data["tests"]}
    assert "test_genuinely_broken" in names and "test_passes" in names, (
        "per-test names were lost through the worker round-trip: %r" % names)


def test_isolate_with_subtest_filter_falls_back_loudly(tmp_path):
    """The subprocess path runs whole files, so a ::-filter cannot isolate.
    The runner must fall back to in-process serial AND SAY SO -- a silent
    fallback is a band claiming an isolation model it is not using."""
    suite = tmp_path / "test_zz_filtered.py"
    # NB: this runner's ::-grammar is file::Class::test (part 2 is a CLASS
    # filter, per the v3.47.3 docstring) -- not pytest's file::function.
    # The filter is applied at the RESULT level (the whole file executes;
    # non-matching results are dropped) -- pre-existing serial behaviour
    # this cut must preserve, not change.
    suite.write_text(textwrap.dedent("""
        class TestIso:
            def test_wanted(self):
                assert True

            def test_unwanted(self):
                assert True
    """))
    r = _run_runner(f"{suite}::TestIso::test_wanted", "--isolate")
    assert "--isolate ignored" in r.stdout, (
        "no loud fallback note for --isolate + ::-filter: %s" % r.stdout[-800:])
    assert "TestIso::test_wanted" in r.stdout, r.stdout[-800:]
    assert "test_unwanted" not in r.stdout, (
        "the ::-filter was not honoured under the fallback: %s"
        % r.stdout[-800:])
    assert "Total: 1" in r.stdout, r.stdout[-800:]
