"""The 2026-08-31 efficiency tools (ruling 41), tested at their REFUSALS.

Four tools land together and each exists because a check returned clean:

  bd-verdict-cache    -- must never store or serve anything but PASS, and must
                         miss when the tree or the experiment changes.
  bd-denom-preflight  -- must go RED on BOTH shapes that cost a full verify on
                         2026-08-31 (an unscoped test file; a new markdown doc).
  bd-running          -- must not count itself or its ancestors, and must still
                         be able to say YES. The anchored form it replaces
                         answered NO while four runs were live.
  bd-verify-cut       -- must refuse a second run against a worktree a LIVE run
                         already holds, and must reclaim a dead holder's lock.

Every assertion here is about a refusal or a positive control, because a tool
that can only ever say OK is the defect, not the fix.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

HOME = Path(os.environ.get("BD_HARNESS_HOME", str(Path.home())))
CACHE = HOME / "bd-verdict-cache"
PREFLIGHT = HOME / "bd-denom-preflight"
RUNNING = HOME / "bd-running"
VERIFY = HOME / "bd-verify-cut.sh"
REPO = Path("/home/mboyle/BulkDownloader")


def sh(*argv, env=None, **kw):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True, env=e, **kw)


# --------------------------------------------------------------------------
# bd-verdict-cache
# --------------------------------------------------------------------------
@pytest.fixture
def cache_env(tmp_path):
    return {"BD_VERDICT_CACHE_DIR": str(tmp_path / "vc")}


def key(env, *parts):
    r = sh(CACHE, "key", *parts, env=env)
    assert r.returncode == 0, r.stderr
    k = r.stdout.strip()
    assert len(k) == 64, f"key is not a sha256: {k!r}"
    return k


def test_only_pass_is_cacheable(cache_env):
    k = key(cache_env, "prepush", "TREE", "BASE", "material")
    for verdict in ("FAIL", "UNKNOWN", "RED", "pass"):
        r = sh(CACHE, "put", k, verdict, "tag", "/dev/null", "TREE", "BASE", "prepush", env=cache_env)
        assert r.returncode == 2, f"{verdict!r} was accepted -- a cache that can store a non-PASS can manufacture a green"
        assert "REFUSED" in r.stderr
    # and nothing was written, so the lookup still misses
    assert sh(CACHE, "get", k, "--expect-tree", "TREE", env=cache_env).returncode == 1


def test_a_hit_names_the_run_it_came_from(cache_env):
    k = key(cache_env, "prepush", "TREE", "BASE", "material")
    assert sh(CACHE, "put", k, "PASS", "v9999-a", "/tmp/x.log", "TREE", "BASE", "prepush", env=cache_env).returncode == 0
    r = sh(CACHE, "get", k, "--expect-tree", "TREE", env=cache_env)
    assert r.returncode == 0
    assert "tag=v9999-a" in r.stdout and "verdict=PASS" in r.stdout
    assert "utc=" in r.stdout


def test_a_different_tree_misses(cache_env):
    k = key(cache_env, "prepush", "TREE", "BASE", "material")
    sh(CACHE, "put", k, "PASS", "v1", "/tmp/x", "TREE", "BASE", "prepush", env=cache_env)
    assert sh(CACHE, "get", k, "--expect-tree", "OTHERTREE", env=cache_env).returncode == 1, \
        "a cache that serves a hit for a tree it was not measured on is a stale-checkout green"


def test_a_different_experiment_is_a_different_key(cache_env, tmp_path):
    tool = tmp_path / "gate.sh"
    tool.write_text("echo one\n")
    a = key(cache_env, "band", "TREE", "BASE", str(tool), "24")
    b = key(cache_env, "band", "TREE", "BASE", str(tool), "12")   # A5: worker count
    tool.write_text("echo two\n")
    c = key(cache_env, "band", "TREE", "BASE", str(tool), "24")   # the gate script changed
    d = key(cache_env, "band", "TREE", "OTHERBASE", str(tool), "24")
    assert len({a, b, c, d}) == 4, "the key does not separate distinct experiments"


def test_a_tampered_record_is_not_a_hit(cache_env, tmp_path):
    k = key(cache_env, "prepush", "TREE", "BASE", "m")
    sh(CACHE, "put", k, "PASS", "v1", "/tmp/x", "TREE", "BASE", "prepush", env=cache_env)
    rec = Path(cache_env["BD_VERDICT_CACHE_DIR"]) / k
    rec.write_text(rec.read_text().replace("verdict=PASS", "verdict=FAIL"))
    assert sh(CACHE, "get", k, "--expect-tree", "TREE", env=cache_env).returncode == 1


# --------------------------------------------------------------------------
# bd-running
# --------------------------------------------------------------------------
def test_bd_running_does_not_count_itself():
    r = sh(RUNNING, "bd-running")
    assert r.returncode == 1, (
        "the checker matched its own invocation -- this is the uniform-count-of-1 "
        "self-match that made every host look identical"
    )
    assert "0 live match" in r.stdout


def test_bd_running_can_still_say_yes():
    """The 2026-08-31 defect was the OTHER direction: it answered NO while four
    runs were live, because the anchored absolute-path pattern never matched a
    relative argv."""
    marker = "bd-running-selftest-marker"
    proc = subprocess.Popen(["bash", "-c", f"exec -a {marker} sleep 10"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            r = sh(RUNNING, marker)
            if r.returncode == 0:
                break
            time.sleep(0.1)
        assert r.returncode == 0, "a checker that can never say yes is not a checker"
        assert marker in r.stdout, "the matching line must be PRINTED, not just counted"
    finally:
        proc.kill()
        proc.wait()


def test_bd_running_matches_a_relative_argv():
    """A pattern anchored on the absolute path is exactly what failed."""
    marker = "bd-running-relative-marker.sh"
    proc = subprocess.Popen(["bash", "-c", f"exec -a 'bash {marker}' sleep 10"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 5
        while time.time() < deadline:
            r = sh(RUNNING, f"/home/mboyle/{marker}")
            if r.returncode == 0:
                break
            time.sleep(0.1)
        # the tool reduces an absolute path argument to its basename precisely
        # so that this case answers correctly.
        assert r.returncode == 0, "an absolute-path query still cannot see a relative argv"
    finally:
        proc.kill()
        proc.wait()


# --------------------------------------------------------------------------
# bd-verify-cut worktree lock
# --------------------------------------------------------------------------
def _holder(art: Path, worktree: Path, pid: int, start: str, tag: str) -> Path:
    import hashlib
    key = hashlib.sha256(str(worktree.resolve()).encode()).hexdigest()
    d = art / f".worktree-{key}.lock"
    d.mkdir(parents=True, exist_ok=True)
    (d / "holder").write_text(f"pid={pid}\nstart={start}\ntag={tag}\nworktree={worktree}\n")
    return d


def _proc_start(pid: int) -> str:
    return Path(f"/proc/{pid}/stat").read_text().split()[21]


def test_a_second_run_on_the_same_worktree_is_refused(tmp_path):
    """One worktree, two tags. The tag lock cannot see this; the worktree lock must."""
    art = tmp_path / "art"
    art.mkdir()
    live = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL)
    try:
        _holder(art, REPO, live.pid, _proc_start(live.pid), "v-other-tag")
        r = sh("bash", VERIFY, REPO, "v-selftest-refusal",
               env={"BD_VERIFY_CUT_ARTIFACT_DIR": str(art)})
        assert r.returncode == 2, f"expected UNKNOWN=2, got {r.returncode}\n{r.stdout}\n{r.stderr}"
        assert "ALREADY RUNNING against this worktree" in r.stdout
        assert str(live.pid) in r.stdout, "the refusal must NAME the holder"
        assert "v-other-tag" in r.stdout
    finally:
        live.kill()
        live.wait()


def test_a_dead_holder_is_reclaimed_not_a_permanent_wedge(tmp_path):
    """A crashed run must not lock the tree out forever -- but the reclaim has to
    be announced, and it must only happen when the PID is really gone."""
    art = tmp_path / "art"
    art.mkdir()
    dead = subprocess.Popen(["true"])
    dead.wait()
    _holder(art, REPO, dead.pid, "999999999", "v-dead-tag")
    r = sh("bash", VERIFY, REPO, "v-selftest-reclaim",
           env={"BD_VERIFY_CUT_ARTIFACT_DIR": str(art)})
    assert "reclaimed a stale worktree lock" in r.stdout, r.stdout[:2000]
    assert "ALREADY RUNNING" not in r.stdout


# --------------------------------------------------------------------------
# bd-denom-preflight
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scratch_wt(tmp_path_factory):
    wt = tmp_path_factory.mktemp("preflight") / "wt"
    subprocess.run(["git", "-C", str(REPO), "worktree", "add", "--quiet", "--detach",
                    str(wt), "origin/main"], check=True)
    (wt / "venv").symlink_to(REPO / "venv")
    try:
        (wt / "frontend" / "node_modules").symlink_to(REPO / "frontend" / "node_modules")
    except OSError:
        pass
    yield wt
    subprocess.run(["git", "-C", str(REPO), "worktree", "remove", "--force", str(wt)],
                   capture_output=True)


def test_preflight_is_green_on_a_clean_tree(scratch_wt):
    r = sh("bash", PREFLIGHT, scratch_wt)
    assert r.returncode == 0, r.stdout[-3000:]
    assert "PREFLIGHT OK" in r.stdout


@pytest.mark.parametrize("rel,body,expect", [
    ("tests/test_preflight_control.py", "def test_a():\n    assert True\n", "BD_GATE_SCOPE"),
])
def test_preflight_sees_the_incident_that_cost_a_full_verify(scratch_wt, rel, body, expect):
    """An unscoped test file: a repo-wide gate that CI would never run."""
    target = scratch_wt / rel
    target.write_text(body)
    subprocess.run(["git", "-C", str(scratch_wt), "add", "-f", rel], check=True)
    try:
        r = sh("bash", PREFLIGHT, scratch_wt)
        assert r.returncode == 1, f"preflight stayed green on {rel}\n{r.stdout[-3000:]}"
        assert "PREFLIGHT RED" in r.stdout
        assert expect in r.stdout, f"the failing assertion was not named:\n{r.stdout[-3000:]}"
    finally:
        subprocess.run(["git", "-C", str(scratch_wt), "rm", "-q", "--cached", rel], capture_output=True)
        target.unlink(missing_ok=True)


def test_a_new_document_is_no_longer_an_incident(scratch_wt):
    """This case USED to go RED, and that was the defect rather than the gate.

    Adding docs/repo/FLEET_TOPOLOGY.md turned a green candidate red on
    2026-08-31 because a hand-bumped total said 138. v3.66.1381 (row 531)
    replaced that total with a floor plus an independently derived corpus, so an
    ordinary new document must now pass. Keeping the old expectation here would
    have made this suite demand the chore back.
    """
    target = scratch_wt / "docs" / "preflight_new_document.md"
    target.write_text("# control\n\nplaceholder\n")
    subprocess.run(["git", "-C", str(scratch_wt), "add", "-f",
                    "docs/preflight_new_document.md"], check=True)
    try:
        r = sh("bash", PREFLIGHT, scratch_wt)
        assert r.returncode == 0, (
            "adding one ordinary document still fails the preflight; row 531's "
            f"floor is not doing its job\n{r.stdout[-3000:]}")
        assert "PREFLIGHT OK" in r.stdout
    finally:
        subprocess.run(["git", "-C", str(scratch_wt), "rm", "-q", "--cached",
                        "docs/preflight_new_document.md"], capture_output=True)
        target.unlink(missing_ok=True)


def test_preflight_sees_a_broken_mutant_anchor(scratch_wt):
    """The v3.66.1381 incident itself, which cost 13m48s plus a CI run.

    bd-anchorcheck answers this in 0.30 seconds and was not in the lane.
    """
    spec = scratch_wt / "tests" / "mutants" / "preflight_control_spec.json"
    spec.write_text(json.dumps({"subject": "control", "mutants": [{
        "label": "CTRL an anchor this tree does not carry",
        "file": "tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py",
        "old": "_EXPECTED_DECLARED_GATE_COUNT = 235",
        "new": "_EXPECTED_DECLARED_GATE_COUNT = 1",
        "direction": "regression",
        "catcher": ("tests/test_v3_66_939_ci_gate_shards_cover_every_gate.py"
                    "::test_declared_and_ci_executed_gate_denominators_are_exact"),
    }]}, indent=1))
    subprocess.run(["git", "-C", str(scratch_wt), "add", "-f",
                    "tests/mutants/preflight_control_spec.json"], check=True)
    try:
        r = sh("bash", PREFLIGHT, scratch_wt)
        assert r.returncode == 1, (
            "a mutant anchored on a line this tree does not have left the "
            f"preflight green\n{r.stdout[-3000:]}")
        assert "PREFLIGHT RED" in r.stdout
        assert "occurs 0 times" in r.stdout or "mutant anchors" in r.stdout
    finally:
        subprocess.run(["git", "-C", str(scratch_wt), "rm", "-q", "--cached",
                        "tests/mutants/preflight_control_spec.json"], capture_output=True)
        spec.unlink(missing_ok=True)
