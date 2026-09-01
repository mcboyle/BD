"""bd-anchorcheck.py: does every tracked mutant anchor still resolve?

The behaviours that matter are the refusals. A checker that cannot fail is
worse than no checker, because it converts an unasked question into a green
line -- the exact fail-open shape CLAUDE.md A7 forbids.

RED/GREEN provenance for this tool is HISTORICAL, not synthetic: two real
commits in this repository broke real anchors, and both are replayed in
test_anchorcheck_catches_the_two_real_historical_breaks below.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home()))) / "bd-anchorcheck.py"
REPO = Path("/home/mboyle/BulkDownloader")


def run(work, *extra):
    return subprocess.run([sys.executable, str(SCRIPT), "--work", str(work), *extra],
                          capture_output=True, text=True)


@pytest.fixture()
def tree(tmp_path):
    """A minimal tree with one subject and one resolving anchor."""
    (tmp_path / "tests" / "mutants").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.py").write_text('def f():\n    return ["a", "--", "b"]\n')
    spec = {"mutants": [{"label": "M1 drop the terminator", "file": "src/thing.py",
                         "old": 'return ["a", "--", "b"]',
                         "new": 'return ["a", "b"]'}]}
    (tmp_path / "tests" / "mutants" / "spec.json").write_text(json.dumps(spec))
    return tmp_path


def test_a_resolving_anchor_passes(tree):
    r = run(tree)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ANCHORCHECK OK" in r.stdout


def test_a_moved_anchor_fails_and_names_it(tree):
    """The whole point: the subject moved, so the mutant judges nothing."""
    (tree / "src" / "thing.py").write_text('def f():\n    argv = ["a"]\n    return argv + ["--", "b"]\n')
    r = run(tree)
    assert r.returncode == 1, r.stdout
    assert "ANCHORCHECK FAIL" in r.stdout
    assert "M1 drop the terminator" in r.stdout
    assert "src/thing.py" in r.stdout
    assert "occurs 0 times" in r.stdout


def test_a_duplicated_anchor_also_fails(tree):
    """Exactly once, not at least once: two matches means the mutation is
    ambiguous about which site it edits."""
    p = tree / "src" / "thing.py"
    p.write_text(p.read_text() + '\ndef g():\n    return ["a", "--", "b"]\n')
    r = run(tree)
    assert r.returncode == 1, r.stdout
    assert "occurs 2 times" in r.stdout


def test_a_missing_subject_is_cannot_evaluate_not_ok(tree):
    """An absent subject is an unavailable measurement, never a pass."""
    (tree / "src" / "thing.py").unlink()
    r = run(tree)
    assert r.returncode == 2, r.stdout
    assert "CANNOT-EVALUATE" in r.stdout


def test_a_malformed_spec_is_cannot_evaluate_not_ok(tree):
    (tree / "tests" / "mutants" / "spec.json").write_text("{ not json")
    r = run(tree)
    assert r.returncode == 2, r.stdout
    assert "CANNOT-EVALUATE" in r.stdout


def test_zero_specs_is_cannot_evaluate_not_ok(tmp_path):
    """A zero denominator must never read as success -- CLAUDE.md A7."""
    (tmp_path / "tests" / "mutants").mkdir(parents=True)
    r = run(tmp_path)
    assert r.returncode == 2, r.stdout
    assert "CANNOT-EVALUATE" in r.stdout
    r2 = run(tmp_path / "nonexistent")
    assert r2.returncode == 2, r2.stdout


def test_a_regex_anchor_is_counted_too(tree):
    spec = {"mutants": [{"label": "R1 regex", "file": "src/thing.py",
                         "old_regex": r'return \["a", "--", "b"\]', "new": "x"}]}
    (tree / "tests" / "mutants" / "spec.json").write_text(json.dumps(spec))
    assert run(tree).returncode == 0
    (tree / "src" / "thing.py").write_text("def f():\n    pass\n")
    r = run(tree)
    assert r.returncode == 1 and "R1 regex" in r.stdout


def test_an_invalid_regex_is_cannot_evaluate(tree):
    spec = {"mutants": [{"label": "R2 bad", "file": "src/thing.py",
                         "old_regex": "([unclosed", "new": "x"}]}
    (tree / "tests" / "mutants" / "spec.json").write_text(json.dumps(spec))
    r = run(tree)
    assert r.returncode == 2, r.stdout


def test_json_output_carries_the_denominator(tree):
    r = run(tree, "--json")
    assert r.returncode == 0
    d = json.loads(r.stdout)
    assert d["anchors_checked"] == 1 and d["specs"] == 1 and d["stale"] == []


@pytest.mark.skipif(not REPO.is_dir(), reason="integrator repo not present")
def test_anchorcheck_catches_the_two_real_historical_breaks(tmp_path):
    """RED provenance from history rather than from a fixture.

    Two real commits moved anchored source and each cost a CI round trip:
    9e408dd1 moved a suite out of the ci.yml lines row310's M5 anchors on, and
    the commit before 726d547e rewrote _ssh_argv in bd-jobs under N12. Both
    must fail here, and their fixes must pass -- otherwise this tool would not
    have prevented the cycles it exists to prevent.
    """
    cases = [
        ("9e408dd15030", 1, "M5 remove the runtime secret census"),
        ("803494e61614", 0, None),
    ]
    for sha, expected_rc, needle in cases:
        probe = subprocess.run(["git", "-C", str(REPO), "cat-file", "-e", f"{sha}^{{commit}}"],
                               capture_output=True)
        if probe.returncode != 0:
            pytest.skip(f"history object {sha} not present in this checkout")
        work = tmp_path / sha
        work.mkdir()
        tar = subprocess.run(["git", "-C", str(REPO), "archive", sha],
                             capture_output=True, check=True)
        subprocess.run(["tar", "-x", "-C", str(work)], input=tar.stdout, check=True)
        r = run(work)
        assert r.returncode == expected_rc, f"{sha}: {r.stdout}"
        if needle:
            assert needle in r.stdout, f"{sha} did not name the stale anchor: {r.stdout}"
