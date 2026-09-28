"""O1479 SECRETSCAN: the GitHub secret scan must apply .gitleaks-baseline.json.

FINDING-SECRETSCAN-BASELINE-NOT-APPLIED (HIGH-process): gitleaks-action@v2 ran `gitleaks detect` without
--baseline-path (GITLEAKS_BASELINE_PATH is not an input it reads), so the full-history schedule scan (run
36405269730) failed on 42 findings that the committed baseline already covers. The fix runs the pinned binary
step on GitHub too. Structural tests pin the workflow; the behavioural tests run the workflow's own step script,
with a fixture repo and a local pinned gitleaks, for every event the step selects a range for.

Behavioural tests opt in with BD_SECRETSCAN_GITLEAKS=<path to the gitleaks 8.24.3 binary>.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
STEP = "Secret scan (fails on anything not baselined)"
GITLEAKS = os.environ.get("BD_SECRETSCAN_GITLEAKS", "")
# Assembled at runtime so this file itself carries no scannable secret.
FAKE_KEY = "api" + "_key" + ' = "' + "Zq7Rv2Kp9Lm4Xw8Nc3Bt6Hy1Fd5Gs0Ja" + '"\n'
OTHER_KEY = "api" + "_key" + ' = "' + "Wm3Tn8Qe1Yb6Uk4Ro9Pz2Ld7Vs5Hx0Cf" + '"\n'


def _steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["gates"]["steps"]


def _scan_step() -> dict:
    found = [s for s in _steps() if s.get("name") == STEP]
    assert len(found) == 1, f"O1479: expected one '{STEP}' step, found {len(found)}"
    return found[0]


def test_no_step_uses_gitleaks_action():
    users = [s.get("uses") for s in _steps() if "gitleaks-action" in str(s.get("uses", ""))]
    assert not users, f"O1479: gitleaks-action ignores the baseline yet still runs: {users}"


def test_scan_step_runs_everywhere_with_config_and_baseline():
    step = _scan_step()
    assert "if" not in step, f"O1479: scan step is conditional ({step.get('if')}); GitHub must run it too"
    run = step["run"]
    assert "--baseline-path .gitleaks-baseline.json" in run and "--config .gitleaks.toml" in run, run
    assert "--exit-code 1" in run and "--redact" in run, run
    assert (REPO / ".gitleaks-baseline.json").is_file() and (REPO / ".gitleaks.toml").is_file()


def test_single_scan_definition():
    runs = [s for s in _steps() if "gitleaks git" in str(s.get("run", "")) or "gitleaks" in str(s.get("uses", ""))]
    assert len(runs) == 1, f"O1479: {len(runs)} secret-scan steps; one definition keeps GitHub and Gitea equal"


# --- behaviour: the workflow's own script on a fixture repo ---------------------------------------------------

def _git(repo: Path, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null", LC_ALL="C")
    return subprocess.run(["git", "-C", str(repo), *args], env=env, capture_output=True, text=True, check=True,
                          timeout=60).stdout.strip()


@pytest.fixture
def scan(tmp_path):
    if not GITLEAKS:
        pytest.skip("gitleaks opt-in required (BD_SECRETSCAN_GITLEAKS)")
    binary = Path(GITLEAKS)
    assert binary.is_file() and os.access(binary, os.X_OK), f"O1479: {binary} is not an executable gitleaks"
    step = _scan_step()
    assert "run" in step, f"O1479: the scan step runs no script ({step.get('uses')}); nothing to execute"
    lines = [x for x in step["run"].splitlines() if not x.lstrip().startswith(("curl ", "tar "))]
    script = "\n".join(lines).replace("/tmp/gitleaks", str(binary))
    assert str(binary) in script and "--baseline-path" in script
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / ".gitleaks.toml").write_text("[extend]\nuseDefault = true\n")
    (repo / "old.py").write_text(FAKE_KEY)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "old secret, baselined")
    raw = tmp_path / "raw.json"  # redacted, as the committed baseline is
    subprocess.run([str(binary), "git", "--redact", "--config", str(repo / ".gitleaks.toml"), "--report-format", "json",
                    "--report-path", str(raw), "--exit-code", "0", str(repo)], capture_output=True, timeout=120,
                   check=True)
    assert raw.read_text().count('"RuleID"') == 1, "fixture must hold exactly one finding to baseline"
    (repo / ".gitleaks-baseline.json").write_text(raw.read_text())
    _git(repo, "add", ".gitleaks-baseline.json")
    _git(repo, "commit", "-q", "-m", "baseline")

    def run(event: str, **env_extra: str):
        env = {k: v for k, v in os.environ.items() if k != "GITEA_ACTIONS"}
        env.update({"EVENT": event, "PR_BASE": "", "PUSH_BEFORE": "", "LC_ALL": "C", **env_extra})
        return subprocess.run(["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True,
                              timeout=180, check=False)

    run.repo = repo
    return run


def _leaks(res) -> str:
    return (res.stdout + res.stderr)[-600:]


def test_schedule_full_history_suppresses_baselined(scan):
    res = scan("schedule")
    assert res.returncode == 0, f"O1479: baselined finding not suppressed on full history: {_leaks(res)}"
    assert "gitleaks range: full history" in res.stdout


def test_schedule_without_baseline_is_red(scan):
    """Positive control: the probe sees the finding when the baseline is not applied (the action's behaviour)."""
    (scan.repo / ".gitleaks-baseline.json").write_text("[]\n")
    res = scan("schedule")
    assert res.returncode == 1, f"control: empty baseline must fail: {_leaks(res)}"


@pytest.mark.parametrize("event", ["schedule", "pull_request", "push"])
def test_new_secret_still_fails(scan, event):
    base = _git(scan.repo, "rev-parse", "HEAD")
    (scan.repo / "new.py").write_text(OTHER_KEY)
    _git(scan.repo, "add", "new.py")
    _git(scan.repo, "commit", "-q", "-m", "new secret, not baselined")
    res = scan(event, PR_BASE=base, PUSH_BEFORE=base)
    assert res.returncode == 1, f"O1479 negative: new secret passed on {event}: {_leaks(res)}"
    if event != "schedule":
        assert f"gitleaks range: {base}..HEAD" in res.stdout, res.stdout


def test_push_with_unknown_before_scans_full_history(scan):
    res = scan("push", PUSH_BEFORE="0" * 40)
    assert res.returncode == 0 and "gitleaks range: full history" in res.stdout, _leaks(res)


@pytest.mark.parametrize("event", ["schedule", "push"])
def test_gitea_new_main_secret_still_fails(scan, tmp_path, event):
    origin = tmp_path / "origin.git"
    _git(scan.repo, "clone", "--bare", ".", str(origin))
    _git(scan.repo, "remote", "add", "origin", str(origin))
    base = _git(scan.repo, "rev-parse", "HEAD")
    (scan.repo / "new.py").write_text(OTHER_KEY)
    _git(scan.repo, "add", "new.py")
    _git(scan.repo, "commit", "-q", "-m", "new fake secret on main")
    _git(scan.repo, "push", "-q", "origin", "main")
    res = scan(event, GITEA_ACTIONS="true", PUSH_BEFORE=base)
    assert res.returncode == 1, f"new main secret passed on Gitea {event}"
    assert ("gitleaks range: full history" if event == "schedule" else f"gitleaks range: {base}..HEAD") in res.stdout


@pytest.mark.parametrize("base", ["", "0" * 40])
def test_pr_unknown_base_scans_full_history_and_refuses_new_secret(scan, base):
    (scan.repo / "new.py").write_text(OTHER_KEY)
    _git(scan.repo, "add", "new.py")
    _git(scan.repo, "commit", "-q", "-m", "new fake secret with missing PR metadata")
    res = scan("pull_request", PR_BASE=base)
    assert res.returncode == 1
    assert "gitleaks range: full history" in res.stdout
