"""o1698-checks-before-lens: the pre-lens check runner and its bd-assign-lens.sh hook (harness candidates).

bd-prelens-checks.sh writes <review copy>/.review/PRECHECKS.md (precut, band, lint, farm) before a lens reads a
VERDICT: PATCH cut; bd-assign-lens.sh kicks it per routable cut and names the file in the NEXT dispatch, fail open.
Candidates live under harness-work/FIX/o1698-checks-before-lens/; the integrator installs them.

  BD_O1698_CHECKS_BEFORE_LENS_CANDIDATE=<abs path to the candidate bd-prelens-checks.sh>
  (the candidate router is its sibling bd-assign-lens.sh). Unset -> skipped; set but absent -> FAIL.

Hermetic: scratch git repos, stub precut/band/lint/farm/tmux/claim/say; nothing reaches ssh or a live seat.
"""
import os
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_CHECKS_BEFORE_LENS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

PRECUT_STUB = """#!/bin/bash
echo x >> "$STUB_DIR/precut.calls"
[ -n "${STUB_PRECUT_SLEEP:-}" ] && sleep "$STUB_PRECUT_SLEEP"
echo "worker-precut[precut]: rc=${STUB_PRECUT_RC:-0} subject=1111111111111111111111111111111111111111 status=COMPLETED"
[ "${STUB_PRECUT_RC:-0}" = 0 ] || echo "RESULT: NOT CUT-READY -- release trio: the version pin does not name 1.0.1"
exit "${STUB_PRECUT_RC:-0}"
"""
# stands in for bd-band-remote.sh <sha> <tests...>, which never runs locally: rc 64 = no capacity host
BAND_STUB = """#!/bin/bash
echo "$*" >> "$STUB_DIR/band.calls"
case "${STUB_BAND_RC:-0}" in
  0)  if [ "${STUB_BAND_ZERO:-0}" = 1 ]; then echo "no tests ran in 0.01s"
      elif [ "${STUB_BAND_ERROR:-0}" = 1 ]; then echo "ERROR tests/test_a.py::test_y - fixture"; echo "===== 12 passed, 1 error in 1.00s ====="
      else echo "===== 12 passed in 1.00s ====="; fi ;;
  1)  echo "FAILED tests/test_a.py::test_x - assert 0"; echo "===== 1 failed, 11 passed in 1.00s =====" ;;
  2)  echo "===== 12 passed in 1.00s =====" ;;
  64) echo "REMOTE-UNAVAILABLE: no capacity host" ;;
esac
exit "${STUB_BAND_RC:-0}"
"""
DERIVE_STUB = """#!/bin/bash
[ -n "${STUB_DERIVE_RC:-}" ] && exit "$STUB_DERIVE_RC"
[ "${STUB_DERIVE_EMPTY:-0}" = 1 ] || echo "  tests/test_a.py"
"""
FARM_STUB = """import os, sys
open(os.path.join(os.environ["STUB_DIR"], "farm.calls"), "a").write("x\\n")
sys.exit(0)
"""


def _sh(*args, cwd=None):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def _x(path, text):
    path.write_text(text)
    path.chmod(0o755)
    return path


@pytest.fixture
def cut(tmp_path):
    runner = Path(CANDIDATE)
    assert runner.is_file() and os.access(runner, os.X_OK), "O1698_CANDIDATE_MISSING"
    repo = tmp_path / "repo"
    repo.mkdir()
    _sh("git", "init", "-q", cwd=repo)
    _sh("git", "-c", "user.email=o@x.invalid", "-c", "user.name=o", "commit", "-q", "--allow-empty", "-m", "b", cwd=repo)
    (repo / "version.py").write_text('VERSION = "1.0.0"\n')
    _sh("git", "add", "version.py", cwd=repo)
    _sh("git", "-c", "user.email=o@x.invalid", "-c", "user.name=o", "commit", "-q", "-m", "base", cwd=repo)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    wt = tmp_path / "cut"
    _sh("git", "worktree", "add", "-q", "--detach", str(wt), base, cwd=repo)
    (wt / "version.py").write_text('VERSION = "1.0.1"\n')
    _sh("git", "add", "version.py", cwd=wt)
    (wt / "DONE.md").write_text(f"VERDICT: PATCH\nBASE: {base}\n")
    review = tmp_path / "review"
    (review / ".review").mkdir(parents=True)
    b = tmp_path / "bin"
    b.mkdir()
    env = dict(os.environ, STUB_DIR=str(tmp_path),
               BD_PRECHECKS_PRECUT=str(_x(b / "precut", PRECUT_STUB)),
               BD_PRECHECKS_BAND=str(_x(b / "band", BAND_STUB)), BD_PRECHECKS_PY="/bin/bash",
               BD_PRECHECKS_DERIVE=str(_x(b / "derive", DERIVE_STUB)),
               BD_PRECHECKS_LINT=str(_x(b / "lint", "#!/bin/bash\nexit 0\n")),
               BD_PRECHECKS_FARM_BIN=str(_x(b / "farm", FARM_STUB)),
               BD_PRECHECKS_LOCKS=str(tmp_path / "locks"), BD_PRECHECKS_LOGS=str(tmp_path / "logs"),
               BD_PRECHECKS_FARM_OUT=str(tmp_path / "farmout"), BD_PRECHECKS_REPO=str(repo))
    return {"tmp": tmp_path, "runner": runner, "wt": wt, "review": review, "env": env, "repo": repo,
            "out": review / ".review" / "PRECHECKS.md"}


def _run(c, **extra):
    env = dict(c["env"], **extra)
    r = subprocess.run([str(c["runner"]), "run", str(c["wt"]), str(c["review"])], env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return c["out"].read_text()


def _calls(c, name):
    p = c["tmp"] / f"{name}.calls"
    return len(p.read_text().splitlines()) if p.exists() else 0


def test_a_failing_precut_is_reported_fail_with_its_reason(cut):
    text = _run(cut, STUB_PRECUT_RC="1")
    assert "PRECUT: FAIL rc=1" in text
    assert "release trio" in text
    assert "SUMMARY: PRECUT FAIL" in text


def test_a_clean_cut_reads_pass_with_band_counts_and_its_tree(cut):
    text = _run(cut)
    tree = subprocess.run(["git", "write-tree"], cwd=cut["wt"], capture_output=True, text=True).stdout.strip()
    assert "PRECUT: PASS rc=0" in text
    assert "BAND: PASS n=12 pass=12 fail=0 files=1" in text
    sha = (cut["tmp"] / "band.calls").read_text().split()[0]
    committed = subprocess.run(["git", "rev-parse", sha + "^{tree}"], cwd=cut["wt"], capture_output=True, text=True)
    assert committed.stdout.strip() == tree
    assert f"TREE: {tree} " in text
    assert len(text.splitlines()) <= 40 and len(text.encode()) <= 4096


def test_remote_unavailable_is_not_run_never_pass(cut):
    text = _run(cut, STUB_PRECUT_RC="64")
    assert "PRECUT: NOT RUN (rc 64" in text and "PRECUT: PASS" not in text


def test_band_rc_64_is_not_run_never_local_never_pass(cut):
    text = _run(cut, STUB_BAND_RC="64")
    assert "BAND: NOT RUN (rc 64 remote unavailable; never local)" in text and "BAND PASS" not in text
    code = [ln for ln in cut["runner"].read_text().splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if "bd-worker-band" in ln or "pytest" in ln]


def test_an_empty_band_is_not_run_and_the_remote_is_not_called(cut):
    text = _run(cut, STUB_DERIVE_EMPTY="1")
    assert "BAND: NOT RUN (band empty" in text and "| BAND NOT |" in text
    assert _calls(cut, "band") == 0


def test_a_band_that_ran_no_test_is_not_run(cut):
    text = _run(cut, STUB_BAND_ZERO="1")
    assert "BAND: NOT RUN (rc=0, no test ran" in text and "BAND PASS" not in text


def test_a_failed_band_is_fail_never_pass(cut):
    text = _run(cut, STUB_BAND_RC="1")
    assert "BAND: FAIL rc=1 n=12 pass=11 fail=1" in text and "| BAND FAIL |" in text
    assert "BAND: PASS" not in text and "FAILED tests/test_a.py::test_x" in text


def test_a_lint_refusal_is_refused_never_pass(cut):
    _x(cut["tmp"] / "bin" / "lint", "#!/bin/bash\necho 'REFUSED: conflict marker'\nexit 1\n")
    text = _run(cut)
    assert "LINT: REFUSED (REFUSED: conflict marker)" in text and "| LINT REFUSED |" in text
    assert "LINT: PASS" not in text and "LINT PASS" not in text


def test_a_band_nonzero_rc_whose_summary_shows_only_passes_is_fail_never_pass(cut):
    """r9 (shape-D4 r8 F1, M8): the band's rc is a verdict of its own; a crashed/interrupted band whose last summary
    line counts only passes must not read PASS."""
    text = _run(cut, STUB_BAND_RC="2")
    assert "BAND: FAIL rc=2 n=12 pass=12 fail=0" in text and "| BAND FAIL |" in text
    assert "BAND: PASS" not in text


def test_a_band_error_with_rc_0_is_fail_never_pass(cut):
    """r9 (shape-D4 r8 N1, M6): an errored test is not a pass even when the band exits 0."""
    text = _run(cut, STUB_BAND_ERROR="1")
    assert "BAND: FAIL rc=0 n=13 pass=12 fail=0 error=1" in text and "| BAND FAIL |" in text
    assert "BAND: PASS" not in text and "ERROR tests/test_a.py::test_y" in text


def test_a_crashed_lint_is_unknown_never_pass(cut):
    """r9 (shape-D4 r8 F1, M5): lint rc other than 0/1 (crash, usage error) is UNKNOWN, never PASS."""
    _x(cut["tmp"] / "bin" / "lint", "#!/bin/bash\necho 'usage: lint <dir>' >&2\nexit 2\n")
    text = _run(cut)
    assert "LINT: UNKNOWN rc=2" in text and "| LINT UNKNOWN" in text
    assert "LINT: PASS" not in text and "LINT PASS" not in text


def test_a_done_without_base_is_precut_not_run_and_precut_is_never_called(cut):
    (cut["wt"] / "DONE.md").write_text("VERDICT: PATCH\n")
    text = _run(cut)
    assert "PRECUT: NOT RUN (DONE.md BASE" in text and "PRECUT: PASS" not in text
    assert _calls(cut, "precut") == 0


def test_a_failed_band_derive_is_named_not_called_empty(cut):
    text = _run(cut, STUB_DERIVE_RC="3")
    assert "BAND: NOT RUN (band derive failed rc=3" in text and "band empty" not in text
    assert _calls(cut, "band") == 0


def test_the_detached_run_does_not_hold_the_callers_cron_lock(cut):
    """r4: cron holds assign-lens.cron.lock (flock -n, no -o) around the router; a run that inherited the fd would block
    every later tick until precut+band finished."""
    import time
    lock = cut["tmp"] / "cron.lock"
    env = dict(cut["env"], STUB_PRECUT_SLEEP="6")
    subprocess.run(["flock", "-n", str(lock), str(cut["runner"]), "kick", str(cut["wt"]), str(cut["review"])],
                   env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=30, check=True)
    time.sleep(1)
    assert "PRECUT: PENDING" in cut["out"].read_text()          # positive control: the run is alive
    assert subprocess.run(["flock", "-n", str(lock), "true"]).returncode == 0
    for _ in range(60):
        if "LINT: PASS" in cut["out"].read_text():
            break
        time.sleep(0.2)


def test_the_router_candidate_keeps_the_fleet_halt_gate():
    """r3: a router cut before H1 installed would delete the FLEET-HALT dispatch check."""
    router = Path(CANDIDATE).with_name("bd-assign-lens.sh").read_text()
    assert any("bd-halt-check.sh" in ln and "dispatch" in ln and not ln.lstrip().startswith("#")
               for ln in router.splitlines())


def test_empty_farm_hosts_is_not_run_and_never_invokes_the_farm(cut):
    text = _run(cut, BD_PRECHECKS_FARM="1", BD_FARM_HOSTS="")
    assert "FARM: NOT RUN (BD_FARM_HOSTS empty)" in text
    assert _calls(cut, "farm") == 0


def test_the_same_tree_is_not_rerun(cut):
    _run(cut)
    n = _calls(cut, "precut")
    _run(cut)
    assert _calls(cut, "precut") == n


def test_a_tree_of_another_repo_is_never_sent_to_precut(cut):
    text = _run(cut, BD_PRECHECKS_REPO=str(cut["tmp"] / "not-the-repo"))
    assert "PRECUT: NOT RUN (cut dir is not a worktree of" in text
    assert _calls(cut, "precut") == 0


def test_the_router_kicks_and_names_prechecks_and_fails_open(tmp_path):
    router = Path(CANDIDATE).with_name("bd-assign-lens.sh")
    assert router.is_file(), "O1698_ROUTER_CANDIDATE_MISSING"
    b = tmp_path / "bin"
    b.mkdir()
    _x(b / "tmux", "case \"$1\" in ls) echo bd-review-correctness-B2-B;; "
                   "capture-pane) printf '\\033[2m\\342\\235\\257\\302\\240\\033[0m\\nstatus\\n';; esac\n")
    _x(b / "claim", '#!/bin/sh\ncase "$1" in holder) echo "NOT-HELD $2 any";; take) echo "CLAIMED $2";; *) :;; esac\nexit 0\n')
    _x(b / "say", f'#!/bin/sh\necho "$1 $2" >> "{tmp_path}/sent"; exit 0\n')
    (b / "objcheck").write_text('import sys\nprint("OBJECT-CHECK: CURRENT " + sys.argv[2])\n')
    _x(b / "prechecks", f'#!/bin/sh\necho "$1 $2 $3" >> "{tmp_path}/kicks"; exit 0\n')
    repo = tmp_path / "repo"
    (repo / "project-knowledge").mkdir(parents=True)
    _sh("git", "init", "-q", cwd=repo)
    (repo / "f").write_text("base\n")
    (repo / "project-knowledge" / "IMPROVEMENT_BACKLOG.md").write_text("| 901 | OPEN | open |\n")
    _sh("git", "add", "-A", cwd=repo)
    _sh("git", "-c", "user.email=o@x.invalid", "-c", "user.name=o", "commit", "-qm", "base", cwd=repo)
    _sh("git", "update-ref", "refs/remotes/origin/main", "HEAD", cwd=repo)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    cutdir = tmp_path / "cuts" / "row901-open"
    cutdir.mkdir(parents=True)
    (cutdir / "DONE.md").write_text(f"VERDICT: PATCH\nBASE: {head}\nopen row\n")
    rc = tmp_path / "review" / "row901-open-local"
    _sh("git", "clone", "-q", str(repo), str(rc))
    (rc / "f").write_text("changed\n")
    _sh("git", "add", "f", cwd=rc)
    (cutdir / "patch.diff").write_text(
        subprocess.run(["git", "diff", "--cached"], cwd=rc, capture_output=True, text=True).stdout)
    (rc / ".review").mkdir()
    (rc / ".review" / "BRIEF.md").write_text("brief\n")
    (rc / ".review" / "TIER.md").write_text("TIER: T2\n")
    (tmp_path / "trains").mkdir()

    def route(prechecks):
        for f in ("sent", "kicks", "batches.json"):
            (tmp_path / f).unlink(missing_ok=True)
        env = dict(os.environ, BD_ASSIGN_LENS_ROOTS=str(tmp_path / "cuts"),
                   BD_ASSIGN_LENS_REVIEW_ROOT=str(tmp_path / "review"), BD_ASSIGN_LENS_TMUX=str(b / "tmux"),
                   BD_ASSIGN_LENS_CLAIM=str(b / "claim"), BD_ASSIGN_LENS_SAY=str(b / "say"),
                   BD_ASSIGN_LENS_TRAINS=str(tmp_path / "trains"), BD_ASSIGN_LENS_REVIEW_LOG=str(tmp_path / "rl"),
                   BD_ASSIGN_LENS_REPO=str(repo), BD_REVIEW_OBJECT_CHECK=str(b / "objcheck"),
                   BD_ASSIGN_LENS_BATCH_STATE=str(tmp_path / "batches.json"),
                   BD_ASSIGN_LENS_LOG=str(tmp_path / "cron.log"), BD_ASSIGN_LENS_RETIRED=str(tmp_path / "ret.tsv"),
                   BD_ASSIGN_LENS_REFRESH_INBOX=str(tmp_path / "inbox"), BD_ASSIGN_LENS_PRECHECKS=prechecks)
        return subprocess.run([str(router)], env=env, capture_output=True, text=True, timeout=120).stdout

    out = route(str(b / "prechecks"))
    assert "ASSIGNED cut=row901-open" in out, out[-800:]
    assert f"kick {cutdir} {rc}" in (tmp_path / "kicks").read_text()
    assert "PRECHECKS: none yet (fail open)" in (tmp_path / "sent").read_text()
    (rc / ".review" / "PRECHECKS.md").write_text("SUMMARY: PRECUT PASS | BAND PASS | LINT PASS | FARM NOT\n")
    route(str(b / "prechecks"))
    assert "PRECHECKS: .review/PRECHECKS.md [PRECUT PASS | BAND PASS" in (tmp_path / "sent").read_text()
    (rc / ".review" / "PRECHECKS.md").unlink()
    assert "ASSIGNED cut=row901-open" in route(str(b / "absent-tool"))
