"""bd-guardcheck must fail CLOSED.

CLAUDE.md 0: "A gate that cannot see the thing it is asked about reports OK --
and that is worse than no gate."  bd-guardcheck was exactly that.  Run from the
repository root it printed::

    warn: no STATE.json found (pass --state or upload a version pack)
      bulk_downloader/extraction_core.py     FILE MISSING
      ... all 7 ...
    0 ok, 0 drifted, 7 missing.
    exit=0

Every one of those files exists.  The default ``--tree`` pointed at
``/home/claude/work`` (a path that does not exist in this repository) and
``if drift: return 1`` was the tool's ONLY non-zero path, so "missing" -- the
state where the gate can see nothing at all -- reported success.  CLAUDE.md
section 2 names bd-guardcheck as THE way to re-derive the guard SHAs, so the
most important gate in the release process certified a tree it never read.

These tests pin the three exit states:

  * 0  -- every guard resolved and matched
  * 1  -- at least one guard DRIFTED (this already worked)
  * 2  -- UNKNOWN: no guards source, a guard file missing, a guard with no pin,
          or a summary whose buckets do not sum.  Unknown is a third state and
          it FAILS.

and the anti-cry-wolf direction (CLAUDE.md 0, second half): on the real,
unmodified tree the tool must report 7 ok and exit 0.  A gate that fires on an
intact tree gets switched off, which is worse than the bug it replaced.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GUARDCHECK = REPO / "toolchain" / "bin" / "bd-guardcheck"
GUARDS_JSON = REPO / "guards.json"

# The 7 release guards, per CLAUDE.md section 2.  Spelled out here on purpose:
# this test's denominator must not be imported from the thing under test.
EXPECTED_GUARDS = [
    "bulk_downloader/extraction_core.py",
    "bulk_downloader/session_capture.py",
    "bulk_downloader/dom_capture.py",
    "bulk_downloader/dom_recorder.py",
    "bulk_downloader/capture_bodies.py",
    "tools/capture_session.py",
    "tools/build_release.py",
]

# Legacy STATE.json discovery locations baked into resolve_state().  If any of
# them exists on this host the "no guards source at all" case is not hermetic,
# so that one test skips rather than lying.
LEGACY_STATE_HINTS = [
    Path("/home/claude/nextsess/STATE.json"),
    Path("/home/claude/work_pack/STATE.json"),
    Path("/mnt/user-data/uploads"),
]


def run_guardcheck(*args, cwd=None):
    """Run bd-guardcheck; return (returncode, combined output)."""
    proc = subprocess.run(
        [sys.executable, str(GUARDCHECK), *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, proc.stdout + proc.stderr


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_tree(tmp_path, present=EXPECTED_GUARDS, guards_json=True, mutate=()):
    """Build a throwaway tree containing `present` guards, copied from the real
    repository so the hashes are genuine.  `mutate` names guards to tamper with
    after copying."""
    tree = tmp_path / "tree"
    manifest = {}
    for rel in EXPECTED_GUARDS:
        manifest[rel] = sha256_of(REPO / rel)
    for rel in present:
        dst = tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    tree.mkdir(parents=True, exist_ok=True)
    for rel in mutate:
        with open(tree / rel, "ab") as fh:
            fh.write(b"\n# TAMPER\n")
    if guards_json:
        (tree / "guards.json").write_text(
            json.dumps({"schema": "bd-guards/1", "guards": manifest}, indent=2)
        )
    return tree


def summary_line(out: str) -> str:
    for line in out.splitlines():
        if re.search(r"\bok\b.*\bdrifted\b.*\bmissing\b", line):
            return line.strip()
    raise AssertionError(f"no summary line in output:\n{out}")


# --------------------------------------------------------------------------
# 1. anti-cry-wolf: the real tree must be GREEN
# --------------------------------------------------------------------------

def test_guards_json_exists_at_repo_root():
    assert GUARDS_JSON.is_file(), (
        "guards.json must exist at the repo root as the single source of truth "
        "for the 7 guard SHAs"
    )


def test_guards_json_lists_exactly_the_seven_guards():
    data = json.loads(GUARDS_JSON.read_text())
    assert sorted(data["guards"]) == sorted(EXPECTED_GUARDS)


def test_guards_json_hashes_are_full_sha256_of_the_real_files():
    """The manifest must be derived from the files, not transcribed from the
    (truncated, and explicitly untrustworthy) table in CLAUDE.md."""
    data = json.loads(GUARDS_JSON.read_text())
    for rel, pinned in data["guards"].items():
        assert len(pinned) == 64, f"{rel}: pin is not a full sha256: {pinned!r}"
        assert pinned == sha256_of(REPO / rel), f"{rel}: guards.json pin != tree sha"


def test_intact_tree_reports_seven_ok_and_exits_zero():
    """THE anti-cry-wolf test.  No arguments, run from the repo root."""
    rc, out = run_guardcheck(cwd=REPO)
    assert rc == 0, f"intact tree must exit 0, got {rc}\n{out}"
    line = summary_line(out)
    assert re.search(r"\b7 ok\b", line), line
    assert re.search(r"\b0 drifted\b", line), line
    assert re.search(r"\b0 missing\b", line), line
    assert "MISSING" not in out, out
    assert "DRIFT" not in out, out


def test_intact_tree_is_green_with_explicit_tree_flag_too():
    rc, out = run_guardcheck("--tree", str(REPO), cwd="/")
    assert rc == 0, f"explicit --tree on intact repo must exit 0, got {rc}\n{out}"
    assert re.search(r"\b7 ok\b", summary_line(out)), out


# --------------------------------------------------------------------------
# 2. missing guard files -> non-zero (the headline bug: this exited 0)
# --------------------------------------------------------------------------

def test_all_guards_missing_is_non_zero(tmp_path):
    tree = make_tree(tmp_path, present=[])
    rc, out = run_guardcheck("--tree", str(tree), cwd=REPO)
    assert "7 missing" in summary_line(out), out
    assert rc != 0, (
        "7/7 guards unreadable is the gate seeing NOTHING; it must not exit 0\n" + out
    )


def test_one_guard_missing_is_non_zero(tmp_path):
    present = [g for g in EXPECTED_GUARDS if g != "tools/build_release.py"]
    tree = make_tree(tmp_path, present=present)
    rc, out = run_guardcheck("--tree", str(tree), cwd=REPO)
    assert rc != 0, f"a single missing guard must fail the gate\n{out}"
    assert "MISSING" in out, out


# --------------------------------------------------------------------------
# 3. no guards source at all -> exit 2, BD-GATE-UNRUNNABLE
# --------------------------------------------------------------------------

def test_explicit_missing_guards_manifest_is_unrunnable(tmp_path):
    tree = make_tree(tmp_path)
    rc, out = run_guardcheck(
        "--tree", str(tree), "--guards", str(tmp_path / "nope.json"), cwd=REPO
    )
    assert rc == 2, f"an explicitly-named absent guards manifest must exit 2, got {rc}\n{out}"
    assert "BD-GATE-UNRUNNABLE" in out, out


def test_no_guards_source_anywhere_is_unrunnable(tmp_path):
    if any(p.exists() for p in LEGACY_STATE_HINTS):
        pytest.skip("legacy STATE discovery locations exist on this host; not hermetic")
    tree = make_tree(tmp_path, guards_json=False)
    rc, out = run_guardcheck("--tree", str(tree), cwd=str(tmp_path))
    assert rc == 2, f"no guards source at all must exit 2, got {rc}\n{out}"
    assert "BD-GATE-UNRUNNABLE" in out, out


# --------------------------------------------------------------------------
# 4. drift -> non-zero (already worked; pinned so it stays working)
# --------------------------------------------------------------------------

def test_drift_is_non_zero(tmp_path):
    tree = make_tree(tmp_path, mutate=["bulk_downloader/dom_capture.py"])
    rc, out = run_guardcheck("--tree", str(tree), cwd=REPO)
    assert rc != 0, f"a tampered guard must fail the gate\n{out}"
    assert "DRIFT" in out, out
    assert "1 drifted" in summary_line(out), out


# --------------------------------------------------------------------------
# 5. a guard with no pin is UNKNOWN, and buckets must sum
# --------------------------------------------------------------------------

def test_unpinned_guard_is_unrunnable_not_ok(tmp_path):
    """--state mode: STATE pins only one guard.  The other six are present but
    unverifiable.  That is unknown, and unknown fails."""
    tree = make_tree(tmp_path, guards_json=False)
    state = tmp_path / "STATE.json"
    state.write_text(json.dumps({
        "guards_full_sha256": {
            "bulk_downloader/dom_capture.py": sha256_of(REPO / "bulk_downloader/dom_capture.py")
        }
    }))
    rc, out = run_guardcheck("--tree", str(tree), "--state", str(state), cwd=REPO)
    assert rc == 2, f"6 unpinned guards is unknown, must exit 2, got {rc}\n{out}"
    assert "BD-GATE-UNRUNNABLE" in out, out


@pytest.mark.parametrize(
    "present,mutate",
    [
        (EXPECTED_GUARDS, ()),
        (EXPECTED_GUARDS, ("bulk_downloader/dom_capture.py",)),
        (EXPECTED_GUARDS[:2], ("bulk_downloader/session_capture.py",)),
        ([], ()),
    ],
)
def test_summary_buckets_sum_to_the_guard_count(tmp_path, present, mutate):
    """A summary that does not account for every guard is a denominator lie.
    The pristine tool printed '0 ok, 0 drifted, 7 missing' for 7 guards in one
    case and, in --state mode with unpinned guards, printed buckets summing to
    less than the guard count."""
    tree = make_tree(tmp_path, present=present, mutate=mutate)
    _rc, out = run_guardcheck("--tree", str(tree), cwd=REPO)
    line = summary_line(out)
    counts = [int(n) for n in re.findall(r"(\d+)\s+[a-z]+", line)]
    assert counts, line
    assert sum(counts) == len(EXPECTED_GUARDS), (
        f"summary buckets {counts} sum to {sum(counts)}, expected "
        f"{len(EXPECTED_GUARDS)} guards -- summary line: {line}"
    )
