"""o1698-worker-done-fullindex: bd-worker-done.sh identity uses the rule-16 patch bytes (H606).

The checker hashed `git diff --cached --binary --no-ext-diff --no-color <BASE>`. `--binary` does not imply full object
names here, so the `index` lines are abbreviated to core.abbrev / object-count length and PATCH-SHA256 differed from the
rule-16 recipe the lens recomputes (`git diff --cached --full-index <BASE>`): a correct DONE.md failed [IDENTITY].

  BD_TEST_O1698_WORKER_DONE_FULLINDEX=1 and BD_O1698_WORKER_DONE_FULLINDEX_CANDIDATE=<abs path to candidate script>
  (unset -> skipped; set but absent -> FAIL). RED: point the candidate at the live/.pre copy.
Hermetic (rule 41): a scratch git repo with core.abbrev pinned; BD_VERDICT_LINT is a stub that records the path it was
handed (or, for the rejecting stub, exits 3); evidence logs live outside /tmp because the checker refuses a receipt that
cites /tmp.
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_WORKER_DONE_FULLINDEX_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    os.environ.get("BD_TEST_O1698_WORKER_DONE_FULLINDEX") != "1" or not CANDIDATE,
    reason="candidate opt-in required")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


@pytest.fixture
def cut():
    assert Path(CANDIDATE).is_file(), "O1698_WORKER_DONE_CANDIDATE_MISSING"
    root = Path(tempfile.mkdtemp(prefix="o1698-wdf-", dir=Path.home()))   # not under /tmp: the checker refuses /tmp cites
    try:
        repo = root / "repo"
        repo.mkdir()
        _git(repo, "init", "-q")
        for k, v in (("user.email", "o1698@example.invalid"), ("user.name", "o1698"), ("core.abbrev", "7")):
            _git(repo, "config", k, v)
        (repo / "t.txt").write_text("a\n")
        _git(repo, "add", "t.txt")
        _git(repo, "commit", "-qm", "base")
        base = _git(repo, "rev-parse", "HEAD").decode().strip()
        (repo / "t.txt").write_text("a\nb\n")
        (repo / "new.txt").write_text("new\n")
        _git(repo, "add", "t.txt", "new.txt")
        called = root / "lint.called"
        lint = root / "lint"
        lint.write_text(f'#!/bin/sh\nprintf "%s\\n" "$1" >> "{called}"\nexit 0\n')
        lint.chmod(0o755)
        reject = root / "lint-reject"
        reject.write_text('#!/bin/sh\necho "lint fixture says no"\nexit 3\n')
        reject.chmod(0o755)
        neg = root / "neg.log"
        neg.write_text("FAIL [IDENTITY] fixture diagnostic\n")
        fix = root / "fixture.log"
        fix.write_text("1 passed\n")
        yield {"root": root, "repo": repo, "base": base, "lint": lint, "reject": reject, "called": called,
               "neg": neg, "fix": fix}
    finally:
        shutil.rmtree(str(root), ignore_errors=True)


def _hash(repo, base, *flags):
    return hashlib.sha256(_git(repo, "diff", "--cached", *flags, base)).hexdigest()


def _done(c, patch_line):
    tree = _git(c["repo"], "write-tree").decode().strip()
    body = (f"VERDICT: PATCH\nBASE: {c['base']}\nTREE: {tree}\n{patch_line}\nWRITTEN-AT: 2026-10-03T04:40:00Z\n"
            f"OWED-EDGES: none\nNEG-CONTROL: rc=1 diagnostic=[IDENTITY] log={c['neg']}\n"
            f"FIXTURE-RUN: rc=0 log={c['fix']}\nRECEIPT: tests pass=1 fail=0 skip=0\n")
    p = c["root"] / "DONE.md"
    p.write_text(body)
    return p


def _run(c, *args, lint="lint"):
    env = dict(os.environ, BD_VERDICT_LINT=str(c[lint]))
    return subprocess.run([sys.executable, CANDIDATE, *args], env=env, capture_output=True, text=True, timeout=60)


def _check(c, patch_line, lint="lint"):
    return _run(c, "--check", str(_done(c, patch_line)), str(c["repo"]), lint=lint)


def _linted(c):
    """Paths the accepting lint stub was handed, in order."""
    return c["called"].read_text().splitlines() if c["called"].exists() else []


def _legacy(c):
    return _hash(c["repo"], c["base"], "--binary", "--no-ext-diff", "--no-color")


def test_positive_control_the_fixture_abbreviates_and_full_index_does_not(cut):
    legacy = _git(cut["repo"], "diff", "--cached", "--binary", "--no-ext-diff", "--no-color", cut["base"]).decode()
    full = _git(cut["repo"], "diff", "--cached", "--full-index", cut["base"]).decode()
    assert re.search(r"^index [0-9a-f]{7}\.\.[0-9a-f]{7}", legacy, re.M)
    assert not re.search(r"^index [0-9a-f]{7}\.\.", full, re.M) and re.search(r"^index [0-9a-f]{40}\.\.[0-9a-f]{40}", full, re.M)
    assert _hash(cut["repo"], cut["base"], "--full-index") != _hash(
        cut["repo"], cut["base"], "--binary", "--no-ext-diff", "--no-color")


def test_full_index_patch_sha_is_accepted(cut):
    r = _check(cut, f"PATCH-SHA256: {_hash(cut['repo'], cut['base'], '--full-index')}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PATCH-MATCH: full-index" in r.stdout + r.stderr


def test_legacy_abbrev_patch_sha_is_accepted_with_a_warning(cut):
    legacy = _hash(cut["repo"], cut["base"], "--binary", "--no-ext-diff", "--no-color")
    r = _check(cut, f"PATCH-SHA256: {legacy}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "WARN: PATCH-MATCH legacy-abbrev; record full-index (rule 16/H606)" in r.stdout + r.stderr


def test_full_index_plus_matching_abbrev_field_is_accepted(cut):
    full = _hash(cut["repo"], cut["base"], "--full-index")
    legacy = _hash(cut["repo"], cut["base"], "--binary", "--no-ext-diff", "--no-color")
    r = _check(cut, f"PATCH-SHA256: {full}\nPATCH-SHA256-ABBREV: {legacy}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PATCH-MATCH: full-index" in r.stdout + r.stderr
    assert "WARN" not in r.stderr, r.stderr   # a correct ABBREV is not "stale"


def test_a_stale_abbrev_field_beside_a_right_full_index_warns_and_passes(cut):
    # O1738c: auto core.abbrev grows with object count, so a filed ABBREV drifts; full-index is the binding value.
    full = _hash(cut["repo"], cut["base"], "--full-index")
    r = _check(cut, f"PATCH-SHA256: {full}\nPATCH-SHA256-ABBREV: {'a' * 64}")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PATCH-MATCH: full-index" in r.stdout
    assert "WARN: PATCH-SHA256-ABBREV stale (core.abbrev drift); full-index matched, ABBREV ignored" in r.stderr


def test_a_wrong_abbrev_field_on_the_legacy_only_path_still_fails(cut):
    legacy = _hash(cut["repo"], cut["base"], "--binary", "--no-ext-diff", "--no-color")
    r = _check(cut, f"PATCH-SHA256: {legacy}\nPATCH-SHA256-ABBREV: {'a' * 64}")
    assert r.returncode == 1 and "[IDENTITY] PATCH-SHA256-ABBREV differs from the legacy recipe" in r.stderr, r.stderr


def test_skeleton_at_abbrev_7_then_check_at_abbrev_12_passes(cut):
    legacy7 = _hash(cut["repo"], cut["base"], "--binary", "--no-ext-diff", "--no-color")
    env = dict(os.environ, BD_DONE_BASE=cut["base"])
    r = subprocess.run([sys.executable, CANDIDATE, "--skeleton", str(cut["repo"])], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    body = (r.stdout.replace("OWED-EDGES: UNRECORDED", "OWED-EDGES: none")
            .replace("NEG-CONTROL: UNRECORDED", f"NEG-CONTROL: rc=1 diagnostic=[IDENTITY] log={cut['neg']}")
            .replace("FIXTURE-RUN: UNRECORDED", f"FIXTURE-RUN: rc=0 log={cut['fix']}")
            .replace("RECEIPT: UNRECORDED", "RECEIPT: tests pass=1 fail=0 skip=0"))
    p = cut["root"] / "DONE.md"
    p.write_text(body)
    _git(cut["repo"], "config", "core.abbrev", "12")
    # positive control: the abbrev change really moves the legacy hash, so an ABBREV-dependent check would fail here
    assert _hash(cut["repo"], cut["base"], "--binary", "--no-ext-diff", "--no-color") != legacy7
    r = _run(cut, "--check", str(p), str(cut["repo"]))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "PATCH-MATCH: full-index" in r.stdout and "WARN" not in r.stderr, r.stdout + r.stderr


def test_a_wrong_patch_sha_fails_identity(cut):
    r = _check(cut, f"PATCH-SHA256: {'b' * 64}")
    assert r.returncode == 1 and "[IDENTITY]" in r.stderr and "PATCH-SHA256" in r.stderr


def test_tree_is_still_exact(cut):
    full = _hash(cut["repo"], cut["base"], "--full-index")
    p = _done(cut, f"PATCH-SHA256: {full}")
    p.write_text(re.sub(r"^TREE: [0-9a-f]{40}$", "TREE: " + "c" * 40, p.read_text(), flags=re.M))
    r = _run(cut, "--check", str(p), str(cut["repo"]))
    assert r.returncode == 1 and "[IDENTITY] TREE" in r.stderr


def test_skeleton_prints_the_rule_16_value(cut):
    env = dict(os.environ, BD_DONE_BASE=cut["base"])
    r = subprocess.run([sys.executable, CANDIDATE, "--skeleton", str(cut["repo"])], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert f"PATCH-SHA256: {_hash(cut['repo'], cut['base'], '--full-index')}" in r.stdout.splitlines()
    assert not any(line.startswith("PATCH-SHA256-ABBREV") for line in r.stdout.splitlines()), r.stdout


def test_a_binary_file_hashes_the_rule_16_bytes(cut):
    (cut["repo"] / "bin.dat").write_bytes(b"a\0b\0c")
    _git(cut["repo"], "add", "bin.dat")
    r = _check(cut, f"PATCH-SHA256: {_hash(cut['repo'], cut['base'], '--full-index')}")
    assert r.returncode == 0 and "PATCH-MATCH: full-index" in r.stdout + r.stderr, r.stdout + r.stderr


# r4 (lens D3-r3 R1/R2): the check has two exits, the full-index early return and the legacy path. Each must hand the DONE
# to the authoritative linter and each must fail when that linter fails.
PATHS = {
    "full-index": lambda c: f"PATCH-SHA256: {_hash(c['repo'], c['base'], '--full-index')}",
    "full-index+stale-abbrev": lambda c: f"PATCH-SHA256: {_hash(c['repo'], c['base'], '--full-index')}\nPATCH-SHA256-ABBREV: {'a' * 64}",
    "legacy-patch-sha": lambda c: f"PATCH-SHA256: {_legacy(c)}",
    "legacy-abbrev-field": lambda c: f"PATCH-SHA256: {_legacy(c)}\nPATCH-SHA256-ABBREV: {_legacy(c)}",
}


@pytest.mark.parametrize("path", sorted(PATHS))
def test_a_rejecting_linter_fails_the_check_on_every_path(cut, path):
    line = PATHS[path](cut)
    ok = _check(cut, line)   # positive control: the same DONE passes when the linter accepts it
    assert ok.returncode == 0, ok.stdout + ok.stderr
    r = _check(cut, line, lint="reject")
    assert r.returncode == 1, f"LINT-FAILURE-DISCARDED on {path}: " + r.stdout + r.stderr
    assert "[VERDICT-LINT] rc=3 lint fixture says no" in r.stderr, r.stderr
    assert "PASS" not in r.stdout, r.stdout


@pytest.mark.parametrize("path", sorted(PATHS))
def test_the_linter_is_handed_the_done_file_on_every_path(cut, path):
    assert _linted(cut) == []   # control: nothing has called the stub yet
    r = _check(cut, PATHS[path](cut))
    assert r.returncode == 0, r.stdout + r.stderr
    assert _linted(cut) == [str(cut["root"] / "DONE.md")], f"LINT-NOT-CALLED-ONCE on {path}: {_linted(cut)}"


def test_the_linter_is_not_consulted_when_identity_already_failed(cut):
    r = _check(cut, f"PATCH-SHA256: {'b' * 64}", lint="reject")
    assert r.returncode == 1 and "[IDENTITY]" in r.stderr and "[VERDICT-LINT]" not in r.stderr, r.stderr
