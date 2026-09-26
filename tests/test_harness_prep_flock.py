"""harness-prep-flock (T2, harness lane): one bd-review-prep.sh per id, and an interleaved MECHANICAL.md is refused.

Measured 2026-09-26 (PM, 14:40Z/15:19Z/16:18Z): bd-prep-one.sh (cron) and a hand run of bd-review-prep.sh on the same id
ran concurrently; both appended to one .review/MECHANICAL.md and the loser's EXIT trap wrote 'REFUSED: shards-939'
beside the winner's 'rc=0 50 passed', so boardgate refused a green cut. Two '## secret scan' headers is the fingerprint.

Candidate (integrator deploys; this test never reads the live harness):
  /home/mboyle/bd-persist/harness-work/FIX/harness-prep-flock/{bd-review-prep.sh,bd-boardgate.sh,bd-review-boardable,
  bd-prep-one.sh,bd-offer-sweep.sh,bd-prep-rescan.sh}
Opt in with BD_HARNESS_PREP_FLOCK_CANDIDATE=<that dir>. Unset -> skipped. Set but absent -> FAIL, never skip.
The hermetic bash battery (test_harness_prep_flock.sh, fixtures under mktemp, env seams only, no worktree touched):
  RED  on the .orig twins: 16 of 31 checks fail   GREEN on the candidates: 31 of 31 pass
  RED  on the R3 prep (bd-review-prep.sh.r3, kept beside the candidate): the 4 R4 checks fail, 27 pass
R2 (PM fix order 19:16Z): callers treat the prep's exit 7 PREP-BUSY as NON-terminal (bd-prep-one.sh / bd-offer-sweep.sh
write one SKIP-BUSY line, never PREP-REFUSED, retry next tick); interleaved = two or more '## secret scan' headers only,
a 0-header floor keeps its original UNKNOWN path.
R3 (PM fix order 19:58Z): the DISPATCHERS re-dispatch after SKIP-BUSY: bd-prep-rescan.sh counts SKIP-BUSY as closing the
PREP-STARTED window (--dry-run: PREP-STARTED+SKIP-BUSY -> NEW-ELIGIBLE, PREP-STARTED alone -> SKIP-INFLIGHT) and
bd-offer-sweep.sh's PREP-STARTED exclusion awk ignores a PREP-STARTED closed by a later SKIP-BUSY.
R4 (PM fix order 20:25Z, lens A5-A): the lock fd must never reach a child. bd-review-prep.sh takes the lock, then re-execs
itself as argv[0] `bd-review-prep-lockholder` (not matching the fleet's `^bash .*bd-review-prep.sh <id>` in-flight pattern)
which runs the real prep as a child with the fd closed, forwards TERM/INT/HUP, exits with its status. A grandchild that
outlives the prep (families-tool shim -> setsid daemon) therefore holds no lock and the next prep is not PREP-BUSY.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_HARNESS_PREP_FLOCK_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required (BD_HARNESS_PREP_FLOCK_CANDIDATE)")

SCRIPTS = (
    "bd-review-prep.sh", "bd-boardgate.sh", "bd-review-boardable", "bd-prep-one.sh", "bd-offer-sweep.sh", "bd-prep-rescan.sh",
)


def _battery(suffix: str) -> subprocess.CompletedProcess[str]:
    cand = Path(CANDIDATE)
    runner = cand / "test_harness_prep_flock.sh"
    assert runner.is_file(), f"candidate battery missing: {runner}"
    for s in SCRIPTS:
        p = cand / (s + suffix)
        assert p.is_file() and os.access(p, os.X_OK), f"candidate missing or not executable: {p}"
    env = {**os.environ, "BD_FLOCK_SUFFIX": suffix}
    return subprocess.run(["bash", str(runner), str(cand)], capture_output=True, text=True, env=env, timeout=600)


def test_candidate_green_one_prep_per_id_and_interleaved_mechanical_refused():
    res = _battery("")
    tail = res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""
    assert res.returncode == 0, f"rc={res.returncode}\n{res.stdout}\n{res.stderr}"
    assert tail.endswith(" 0 failed"), tail
    assert "PASS prep-B PREP-BUSY rc=7 while A holds" in res.stdout
    assert "PASS boardgate: 2 headers -> REFUSED MECHANICAL-INTERLEAVED rc=3" in res.stdout
    assert "PASS boardable: 2 headers -> MECHANICAL-INTERLEAVED, NOT boarding" in res.stdout
    assert "PASS boardgate: 0 headers is NOT interleaved -- original UNKNOWN gitleaks.json path (R2)" in res.stdout
    assert "PASS prep-one: held lock -> SKIP-BUSY line, no PREP-REFUSED" in res.stdout
    assert "PASS prep-one: next tick starts the prep again" in res.stdout
    assert "PASS rescan: PREP-STARTED + SKIP-BUSY -> NEW-ELIGIBLE (R3: busy id is re-dispatched)" in res.stdout
    assert "PASS rescan: PREP-STARTED alone -> SKIP-INFLIGHT" in res.stdout
    assert "PASS sweep: PREP-STARTED + SKIP-BUSY -> eligible (R3: retried next pass)" in res.stdout
    assert "PASS r4: the surviving grandchild holds NO lock fd" in res.stdout
    assert "PASS r4: next prep on the id is NOT PREP-BUSY while the grandchild lives (rc=0)" in res.stdout
    assert "PASS r4: exactly 1 process matches the in-flight pattern" in res.stdout
    assert "PASS r4: TERM to the holder frees the lock" in res.stdout


def test_negative_control_originals_are_red_on_the_same_battery():
    """The battery can say NO: the pre-fix twins (.orig) fail the three defect checks (rule 7, positive control)."""
    res = _battery(".orig")
    assert res.returncode == 1, f"rc={res.returncode}\n{res.stdout}\n{res.stderr}"
    assert "FAIL prep-B" in res.stdout
    assert "FAIL boardgate 2 headers" in res.stdout
    assert "FAIL boardable 2 headers" in res.stdout
    assert "FAIL prep-one held lock" in res.stdout
    assert "FAIL offer-sweep source" in res.stdout
    assert "FAIL rescan L1" in res.stdout
    assert "FAIL sweep S1" in res.stdout


def test_negative_control_r3_prep_leaks_the_lock_fd_to_a_surviving_grandchild():
    """R4 RED: the R3 prep (kept as bd-review-prep.sh.r3) fails exactly the four R4 checks on the same battery."""
    cand = Path(CANDIDATE)
    r3 = cand / "bd-review-prep.sh.r3"
    assert r3.is_file() and os.access(r3, os.X_OK), f"R3 twin missing: {r3}"
    env = {**os.environ, "BD_FLOCK_PREP": str(r3)}
    env.pop("BD_FLOCK_SUFFIX", None)
    res = subprocess.run(["bash", str(cand / "test_harness_prep_flock.sh"), str(cand)], capture_output=True, text=True, env=env, timeout=600)
    assert res.returncode == 1, f"rc={res.returncode}\n{res.stdout}\n{res.stderr}"
    fails = [l for l in res.stdout.splitlines() if l.startswith("FAIL ")]
    assert len(fails) == 4, fails
    assert all(l.startswith("FAIL r4 ") for l in fails), fails
    assert any("holds the lock fd" in l for l in fails), fails
    assert any(l.startswith("FAIL r4 next prep: rc=7 PREP-BUSY") for l in fails), fails


def test_candidate_source_carries_the_lock_and_the_fingerprint_check():
    cand = Path(CANDIDATE)
    prep = (cand / "bd-review-prep.sh").read_text(encoding="utf-8")
    assert 'PREP_LOCK="$PREP_LOCKD/review-prep-$ROW.lock"' in prep
    assert 'flock -n "$PREP_LFD"' in prep and "exit 7" in prep
    assert "exec -a bd-review-prep-lockholder bash -c" in prep, "R4: the lock must be held by the re-exec'd lockholder"
    assert '$fd>&- &' in prep, "R4: the prep child must be spawned with the lock fd closed"
    for s in ("bd-boardgate.sh", "bd-review-boardable"):
        src = (cand / s).read_text(encoding="utf-8")
        assert src.count("MECHANICAL-INTERLEAVED") >= 1, s
        assert '"${NSECRET:-0}" -ge 2' in src, f"{s}: interleaved must mean >= 2 headers (R2)"
    for s in ("bd-prep-one.sh", "bd-offer-sweep.sh"):
        src = (cand / s).read_text(encoding="utf-8")
        assert "SKIP-BUSY" in src and "-eq 7" in src, f"{s}: exit 7 must be non-terminal (R2)"
    rescan = (cand / "bd-prep-rescan.sh").read_text(encoding="utf-8")
    assert "SKIP-ALREADY-BRIEFED|SKIP-BUSY)$/" in rescan, "rescan: SKIP-BUSY must close the PREP-STARTED window (R3)"
    assert 'SKIP-BUSY [^ ]+' not in rescan, "rescan: SKIP-BUSY must NOT join the terminal $already set"
