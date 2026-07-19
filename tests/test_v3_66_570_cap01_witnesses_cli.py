"""F-TO06-02 -- tools/audit/witnesses/cap01_witnesses.py direct-CLI path.

Two compounding defects on the `bd python3 .../cap01_witnesses.py` path the
module docstring advertises:
  (a) main() did `for cid, ok, detail in RESULTS:` but the `w` decorator stores
      5-key dicts -> unpacking a dict yields its KEYS -> a 5-key dict into 3
      targets raises `ValueError: too many values to unpack (expected 3)`.
  (b) the `if __name__ == "__main__"` guard sat BEFORE the last witness (_w7),
      so a direct run exited via sys.exit(main()) before _w7 registered -- the
      last witness (F-CAP01-manifest-gap) was silently absent.

The end-to-end test runs the module as a subprocess so its import side effects
(the witnesses monkeypatch live_recorder / capture_bodies and mutate os.environ)
stay isolated and never pollute the band's shared process. The `w` decorator
catches per-witness import/probe failures, so this test validates the FIX (the
CLI runs to completion and every witness registers) independent of whether each
witness fully reproduces in the sandbox. Two static backstops encode each defect
deterministically, immune to any subprocess-env variance.

RED on pristine 3.66.569; GREEN after moving the guard to EOF + reading RESULTS
dicts by key. Runs under run_tests.py: zero-arg tests, no caplog/tmp_path/
monkeypatch fixtures.
"""

import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)  # tests/ -> tree root
_MODULE = os.path.join(_REPO, "tools", "audit", "witnesses", "cap01_witnesses.py")


def _run_cli():
    env = dict(os.environ)
    env["PYTHONPATH"] = _REPO + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, _MODULE],
        capture_output=True, text=True, env=env, timeout=120)


def test_cap01_witnesses_direct_cli_runs_and_registers_all():
    """Both defects on the real documented direct-run path."""
    p = _run_cli()
    # (a) no unpack crash
    assert "ValueError" not in p.stderr, (
        "cap01_witnesses CLI raised ValueError on its documented direct-run "
        "path:\n" + (p.stderr[-800:] or "<no stderr>"))
    assert p.returncode in (0, 1), (
        f"cap01_witnesses CLI crashed (rc={p.returncode}):\n{p.stderr[-800:]}")
    # (b) the last witness registered (guard must follow every @w registration)
    assert "F-CAP01-manifest-gap" in p.stdout, (
        "last witness F-CAP01-manifest-gap missing from the direct-run output "
        "(the __main__ guard runs before it registers):\n"
        + (p.stdout[-800:] or "<no stdout>"))
    # sanity: the suite printed its summary line
    assert "witnesses green" in p.stdout, (
        "CAP-01 witness summary missing from direct-run output:\n"
        + (p.stdout[-800:] or "<no stdout>"))


def test_main_reads_results_by_key_not_tuple_unpack():
    """Static backstop for defect (a): the 3-tuple-unpack-over-dicts pattern is
    gone and main() reads RESULTS entries by key."""
    src = open(_MODULE, encoding="utf-8").read()
    assert "for cid, ok, detail in RESULTS" not in src, (
        "main() still unpacks 5-key RESULTS dicts as 3-tuples "
        "(`for cid, ok, detail in RESULTS`) -> ValueError on the CLI path")
    assert 'for cid, ok, detail in []' not in src, (
        "the dead empty-loop stub should be removed")
    assert 'r["detail"]' in src or "r['detail']" in src, (
        "main() should read RESULTS entries by key (r['id']/r['ok']/r['detail'])")


def test_main_guard_follows_last_witness_in_source():
    """Static backstop for defect (b): the __main__ guard must appear AFTER the
    last @w(...) witness registration so a direct run registers every witness
    before main() executes."""
    lines = open(_MODULE, encoding="utf-8").read().splitlines()
    guard = [i for i, ln in enumerate(lines)
             if ln.startswith('if __name__ == "__main__"')]
    witnesses = [i for i, ln in enumerate(lines) if ln.startswith("@w(")]
    assert guard, "no __main__ guard found in cap01_witnesses.py"
    assert witnesses, "no @w witnesses found in cap01_witnesses.py"
    assert guard[-1] > witnesses[-1], (
        f"__main__ guard at line {guard[-1] + 1} precedes the last @w witness at "
        f"line {witnesses[-1] + 1}; move the guard to EOF so every witness "
        f"registers before main() runs")


if __name__ == "__main__":
    for fn in sorted(k for k in dict(globals()) if k.startswith("test_")):
        globals()[fn]()
        print("PASS", fn)
