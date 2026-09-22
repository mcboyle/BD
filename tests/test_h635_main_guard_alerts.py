import os
from pathlib import Path
import subprocess

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_H635_MAIN_GUARD_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture
def site(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.com", "commit", "-qm", "fixture",
                    "--allow-empty"], check=True)
    persist = tmp_path / "persist"
    persist.mkdir()
    alerts = tmp_path / "alerts"
    say = tmp_path / "say"
    say.write_text('#!/bin/bash\nprintf "%s\\n" "$1" >> "$BD_TEST_ALERTS"\nexit "${BD_TEST_SAY_RC:-0}"\n')
    say.chmod(0o755)
    env = {**os.environ, "BD_MAIN_GUARD_M": str(repo),
           "BD_MAIN_GUARD_P": str(persist), "BD_MAIN_GUARD_SAY": str(say),
           "BD_TEST_ALERTS": str(alerts)}
    return repo, persist, alerts, env


def run_guard(site):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file()
    assert os.access(candidate, os.X_OK)
    result = subprocess.run(["bash", str(candidate)], env=site[3],
                            text=True, capture_output=True)
    return result


def calls(site):
    return site[2].read_text().splitlines() if site[2].exists() else []


def test_same_stray_alerts_once(site):
    repo, persist, _, _ = site
    stray = repo / "stray.orig"
    stray.write_bytes(b"")
    assert stray.is_file()
    assert run_guard(site).returncode == 0
    assert calls(site) == ["bd-integrator-A", "pm"]
    assert len(list((persist / "harness-work/MAIN-CHECKOUT-STRAYS").glob("*.untracked/stray.orig"))) == 1
    assert run_guard(site).returncode == 0
    assert calls(site) == ["bd-integrator-A", "pm"], "DUPLICATE-ALERT: same stray sent twice"
    assert stray.is_file(), "guard must preserve the stray"


def test_new_path_in_same_directory_alerts(site):
    repo = site[0]
    nested = repo / "nested"
    nested.mkdir()
    (nested / "first.orig").write_text("first")
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 2
    (nested / "second.orig").write_text("second")
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 4, "MISSED-ALERT: changed stray set"
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 4, "DUPLICATE-ALERT: changed set sent twice"


def test_clean_observation_rearms_same_path(site):
    stray = site[0] / "stray.orig"
    stray.write_text("first")
    assert run_guard(site).returncode == 0
    stray.unlink()
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 2
    stray.write_text("later")
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 4, "MISSED-ALERT: clean-to-dirty transition"


def test_failed_delivery_is_not_marked_seen(site):
    (site[0] / "stray.orig").write_text("stray")
    site[3]["BD_TEST_SAY_RC"] = "6"
    assert run_guard(site).returncode == 6
    assert len(calls(site)) == 2
    site[3]["BD_TEST_SAY_RC"] = "0"
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 4, "MISSED-ALERT: failure must not mark delivered"
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 4, "DUPLICATE-ALERT after successful delivery"


def test_allowlisted_path_stays_untouched(site):
    stray = site[0] / "operator.txt"
    stray.write_text("operator work")
    (site[1] / "MAIN-CHECKOUT-ALLOW").write_text("operator.txt\n")
    assert run_guard(site).returncode == 0
    assert calls(site) == []
    assert stray.read_text() == "operator work"
    (site[0] / "other.orig").write_text("stray")
    assert run_guard(site).returncode == 0
    assert len(calls(site)) == 2, "MISSED-ALERT: allowlist must not mask other files"


def test_concurrent_ticks_only_alert_once(site):
    (site[0] / "stray.orig").write_text("stray")
    jobs = [subprocess.Popen(["bash", CANDIDATE], env=site[3],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True) for _ in range(2)]
    for job in jobs:
        stdout, stderr = job.communicate(timeout=20)
        assert job.returncode == 0, stdout + stderr
    assert calls(site) == ["bd-integrator-A", "pm"], "DUPLICATE-ALERT: concurrent ticks"
