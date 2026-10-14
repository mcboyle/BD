"""O3143 A1 ci-fleet-runners: the heavy jobs run on the bd-fleet runners only for this repo's own cut PRs.

gate-suites and frontend-vitest take their runner from ONE output, ci_docs_only.outputs.runner, written by the
"Choose the runner pool" step. fleet = [self-hosted, bd-fleet]; hosted = ubuntu-latest. The step is executed
here, as GitHub runs it (bash -e), over every event shape: only a same-repo pull request from a cut/* branch
goes to the fleet. Trains, pushes to main, other same-repo PRs, fork PRs (even from a branch named cut/*),
the nightly schedule and the Gitea mirror stay hosted (PM RULING ANSWER-pm-B2-B-A1-after-run-1822Z). A fork
must never reach a self-hosted runner, so the fork cases are the ones this file exists for.
gate-suites keeps version-at-land hosted; postgres-integration stays hosted (its service needs Docker).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

BD_GATE_SCOPE = "module"

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"
REPO = "mcboyle/BD"
FLEET_RUNS_ON = ("${{ needs.ci_docs_only.outputs.runner == 'fleet' && "
                 "fromJSON('[\"self-hosted\", \"bd-fleet\"]') || 'ubuntu-latest' }}")
GATE_RUNS_ON = ("${{ needs.ci_docs_only.outputs.runner == 'fleet' && matrix.name != 'version-at-land' && "
                "fromJSON('[\"self-hosted\", \"bd-fleet\"]') || 'ubuntu-latest' }}")
HEAVY = {"gate-suites", "frontend-vitest"}
POOL_ENV = {"EVENT": "${{ github.event_name }}", "SERVER_URL": "${{ github.server_url }}",
            "REPO": "${{ github.repository }}", "HEAD_REPO": "${{ github.event.pull_request.head.repo.full_name }}",
            "HEAD_REF": "${{ github.head_ref }}"}


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _pool_step() -> dict:
    steps = [s for s in _jobs()["ci_docs_only"]["steps"] if s.get("id") == "pool"]
    assert len(steps) == 1, ("POOL-STEP-COUNT", len(steps))
    return steps[0]


def _pool(tmp_path: Path, **env: str) -> str:
    out = tmp_path / "out"
    out.write_text("")
    full = {"PATH": "/usr/bin:/bin", "GITHUB_OUTPUT": str(out), "HEAD_REPO": "", "HEAD_REF": "", **env}
    r = subprocess.run(["bash", "-e", "-c", _pool_step()["run"]], env=full, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, ("POOL-STEP-RC", r.returncode, r.stderr[-300:])
    lines = [ln for ln in out.read_text().splitlines() if ln.startswith("runner=")]
    assert len(lines) == 1, ("POOL-OUTPUT", out.read_text())
    return lines[0].split("=", 1)[1]


GH = "https://github.com"
GITEA = "http://10.0.70.162:3000"


def _pr(head_repo, ref, **kw):
    return dict(EVENT="pull_request", SERVER_URL=GH, REPO=REPO, HEAD_REPO=head_repo, HEAD_REF=ref, **kw)


CASES = [
    ("cut-pr", _pr(REPO, "cut/fed-trust-r1-S55-B"), "fleet"),
    ("train-pr", _pr(REPO, "train/281k-20261014"), "hosted"),
    ("other-same-repo-pr", _pr(REPO, "ci/fleet-runners-r1"), "hosted"),
    ("cut-prefix-not-at-start", _pr(REPO, "train/cut/x"), "hosted"),
    ("bare-cut-slash", _pr(REPO, "cut/"), "hosted"),
    ("push-main", dict(EVENT="push", SERVER_URL=GH, REPO=REPO), "hosted"),
    ("fork-pr-cut-branch", _pr("someone/BD", "cut/evil"), "hosted"),
    ("fork-pr-same-name-other-owner", _pr("mcboyle-x/BD", "cut/evil"), "hosted"),
    ("pr-head-repo-deleted", _pr("", "cut/x"), "hosted"),
    ("schedule", dict(EVENT="schedule", SERVER_URL=GH, REPO=REPO), "hosted"),
    ("gitea-cut-pr", dict(EVENT="pull_request", SERVER_URL=GITEA, REPO="Mboyle/BD-ci", HEAD_REPO="Mboyle/BD-ci",
                          HEAD_REF="cut/x"), "hosted"),
    ("pull-request-target-cut", dict(_pr(REPO, "cut/x"), EVENT="pull_request_target"), "hosted"),
]


@pytest.mark.parametrize("name,env,want", CASES, ids=[c[0] for c in CASES])
def test_the_pool_step_sends_only_this_repos_cut_prs_to_the_fleet(tmp_path, name, env, want):
    got = _pool(tmp_path, **env)
    if want == "hosted" and got == "fleet":
        pytest.fail(f"FORK-OR-FOREIGN-ON-FLEET: {name} -> fleet")
    assert got == want, (name, got)


def test_the_pool_step_reads_the_event_through_env_only():
    step = _pool_step()
    assert "${{" not in step["run"], "POOL-RUN-INTERPOLATES: event fields must reach the script through env"
    assert step["env"] == POOL_ENV, ("POOL-ENV", step["env"])
    assert _jobs()["ci_docs_only"]["outputs"]["runner"] == "${{ steps.pool.outputs.runner }}"
    assert _jobs()["ci_docs_only"]["runs-on"] == "ubuntu-latest"


def test_only_the_heavy_jobs_can_reach_the_fleet():
    jobs = _jobs()
    fleet = {n for n, j in jobs.items() if j.get("runs-on") != "ubuntu-latest"}
    assert fleet == HEAVY, ("FLEET-JOBS", sorted(fleet))
    assert jobs["frontend-vitest"]["runs-on"] == FLEET_RUNS_ON, jobs["frontend-vitest"]["runs-on"]
    assert jobs["gate-suites"]["runs-on"] == GATE_RUNS_ON, ("VERSION-AT-LAND-NOT-HOSTED", jobs["gate-suites"]["runs-on"])
    shards = [m["name"] for m in jobs["gate-suites"]["strategy"]["matrix"]["include"]]
    assert "version-at-land" in shards                                   # the exclusion names a real shard
    for n in HEAVY:
        assert "ci_docs_only" in ([jobs[n]["needs"]] if isinstance(jobs[n]["needs"], str) else jobs[n]["needs"])
    assert jobs["postgres-integration"]["runs-on"] == "ubuntu-latest"     # services: need Docker; fleet has none
    assert "services" in jobs["postgres-integration"]


def test_fleet_jobs_get_a_fresh_python_and_serialise_apt_per_host():
    steps = _jobs()["gate-suites"]["steps"]
    names = [s.get("name") or s.get("uses") for s in steps]
    venv = names.index("Fresh Python environment (fleet runners only)")
    assert names.index("actions/setup-python@v5") < venv < names.index("Install dependencies")
    assert "runner.environment == 'self-hosted'" in steps[venv]["if"]     # follows the job's real runner
    assert '"$RUNNER_TEMP/venv/bin" >> "$GITHUB_PATH"' in steps[venv]["run"]
    for s in steps:
        run = s.get("run") or ""
        if "apt-get" in run or "--with-deps" in run:
            assert "flock -w 900 /tmp/bd-ci-apt.lock" in run, ("APT-NOT-SERIALISED", s.get("name"))
