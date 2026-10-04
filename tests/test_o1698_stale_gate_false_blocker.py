import os
from pathlib import Path
import subprocess

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_STALE_GATE_FALSE_BLOCKER_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
STAMP = "2026-10-03T08:00:00Z"
ADVISORY = f"stale gate RED: 13 failed on main {STAMP} (advisory; trains still land via CI)"
UNKNOWN = f"stale gate UNKNOWN (incomplete run {STAMP})"
LIVE_ALERT = "/home/mboyle/bd-persist/harness/bd-push-alert.sh"
ALERT_TWIN = "/home/mboyle/bd-push-alert.sh"
ALERT_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$ALERT_LOG"
"""


@pytest.fixture
def census(tmp_path):
    candidate = Path(CANDIDATE)
    assert candidate.is_absolute() and candidate.is_file() and os.access(candidate, os.X_OK), "candidate unavailable"
    persist = tmp_path / "persist"
    (persist / "queue").mkdir(parents=True)
    (persist / "CHECKIN.log").write_text("")
    report = persist / "MAIN-RED-STALE-GATE.md"
    alerts = tmp_path / "alerts"
    # Every push route logs to ALERT_LOG, whatever the script under test contains: the fixed live path and its
    # twin (functions), PATH lookup, a fake BD harness dir, and the persist-relative harness path.
    harness = tmp_path / "harness"
    for directory in (tmp_path / "bin", harness, persist / "harness"):
        directory.mkdir(exist_ok=True)
        stub = directory / "bd-push-alert.sh"
        stub.write_text(ALERT_STUB)
        stub.chmod(0o755)

    def write(*, verdict="RED", completed=True, stamp=STAMP, failed=13, summary=None, runtime_rc=1):
        summary = summary if summary is not None else f"= {failed} failed, 28153 passed, 834 skipped in 2438.92s ="
        terminal = f"{summary}\nBAND-ENV pytest-rc={runtime_rc}\n" if completed else "bd-stale-gate-cron.sh: line 72: 351233 Killed\n"
        report.write_text(f"VERDICT: {verdict}\nMAIN-RED-STALE-GATE: {stamp}, origin/main fixture\nFAILURE:\n{terminal}")

    def execute(*, dry_run=False, script=candidate):
        env = dict(os.environ, BD_CHECKIN_PERSIST=str(persist), BD_CHECKIN_SAY="0", BD_CHECKIN_DRY_RUN=str(int(dry_run)), ALERT_LOG=str(alerts),
                   PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", BD_HARNESS_DIR=str(harness), BD_ALERTS_LOG=str(alerts))
        wrapper = f'''tmux() {{ return 1; }}
function {LIVE_ALERT}() {{ "$BD_HARNESS_DIR/bd-push-alert.sh" "$@"; }}
function {ALERT_TWIN}() {{ "$BD_HARNESS_DIR/bd-push-alert.sh" "$@"; }}
. "$1"'''
        result = subprocess.run(["bash", "-c", wrapper, "checkin", str(script)], env=env, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, f"checkin rc={result.returncode}: {result.stderr}"
        return result.stdout

    write()
    return write, execute, persist, alerts


def test_completed_red_is_advisory_without_blocker_or_push(census):
    _, execute, persist, alerts = census
    output = execute()
    assert ADVISORY in output, "completed RED missing advisory diagnostic"
    assert "BLOCKER:" not in output, "advisory gate falsely blocks train"
    assert not alerts.exists(), "advisory gate emitted push without train refusal"
    assert (persist / "state" / "checkin.stale-gate.sha256").is_file()


def test_same_gate_result_is_announced_once(census):
    _, execute, _, alerts = census
    assert ADVISORY in execute(), "positive control did not emit initial advisory"
    output = execute()
    assert ADVISORY not in output, "unchanged gate result announced every checkin"
    assert "BLOCKER:" not in output
    assert not alerts.exists()


def test_new_gate_result_is_announced(census):
    write, execute, _, _ = census
    assert ADVISORY in execute()
    stamp = "2026-10-03T09:00:00Z"
    write(stamp=stamp, failed=2)
    expected = f"stale gate RED: 2 failed on main {stamp} (advisory; trains still land via CI)"
    assert expected in execute(), "new gate result suppressed by prior advisory state"
    assert expected not in execute()


def test_landed_train_does_not_turn_real_red_into_blocker(census):
    _, execute, persist, alerts = census
    (persist / "landing").mkdir()
    (persist / "landing" / "LANDED-TRAIN207-fixture-AUTO.md").write_text("VERDICT: LANDED\nFast-forwarded onto main 2026-10-03T08:30:00Z dry=0\n")
    output = execute()
    assert ADVISORY in output
    assert "BLOCKER:" not in output
    assert not alerts.exists()


def test_killed_run_is_unknown_without_push(census):
    write, execute, _, alerts = census
    write(completed=False)
    output = execute()
    assert UNKNOWN in output, "killed run lacks distinctive incomplete diagnostic"
    assert "BLOCKER:" not in output and ADVISORY not in output
    assert not alerts.exists()


def test_green_unchanged(census):
    write, execute, persist, alerts = census
    write(verdict="GREEN", completed=False)
    output = execute()
    assert "gate: VERDICT: GREEN" in output
    assert "BLOCKER:" not in output and "stale gate UNKNOWN" not in output
    assert not alerts.exists()
    assert not (persist / "state" / "checkin.stale-gate.sha256").exists()


def test_dry_run_keeps_advisory_state_untouched(census):
    _, execute, persist, alerts = census
    assert ADVISORY in execute(dry_run=True)
    assert not (persist / "state" / "checkin.stale-gate.sha256").exists()
    assert not alerts.exists()


@pytest.mark.parametrize("summary,runtime_rc,counts", [
    ("= 1 error in 3.10s =", 2, "0 failed, 1 error"),
    ("INTERNALERROR RuntimeError: collection crashed\n= no tests ran in 0.10s =", 3, "0 failed, 1 error"),
    ("INTERNALERROR> RuntimeError: session startup crashed", 3, "0 failed, 1 error"),
    ("= 3 failed in 2.00s =", 1, "3 failed"),
    ("= 2 failed, 3 errors in 2.00s =", 1, "2 failed, 3 errors"),
], ids=["collection-error", "internal-error-with-summary", "internal-error-without-summary", "failures-only", "mixed-failures-errors"])
def test_completed_red_summary_variants_are_advisory(census, summary, runtime_rc, counts):
    write, execute, _, alerts = census
    write(summary=summary, runtime_rc=runtime_rc)
    expected = f"stale gate RED: {counts} on main {STAMP} (advisory; trains still land via CI)"
    output = execute()
    assert expected in output, "completed pytest error/failure summary lost its RED advisory"
    assert "stale gate UNKNOWN" not in output, "completed pytest error mislabeled incomplete"
    assert "BLOCKER:" not in output and not alerts.exists()
    assert expected not in execute(), "completed pytest error advisory repeats on unchanged report"


def test_counts_in_failure_text_do_not_prove_completion(census):
    write, execute, _, alerts = census
    write(summary="ERROR RuntimeError: 9 failed, 1 error while starting collection")
    output = execute()
    assert UNKNOWN in output, "non-summary error text falsely establishes completion"
    assert "stale gate RED:" not in output and "BLOCKER:" not in output
    assert not alerts.exists()


def test_internal_error_without_completed_exit_is_unknown(census):
    write, execute, _, alerts = census
    write(summary="INTERNALERROR> RuntimeError: interrupted startup", runtime_rc=137)
    output = execute()
    assert UNKNOWN in output, "unfinished internal error borrowed completed crash status"
    assert "stale gate RED:" not in output and "BLOCKER:" not in output
    assert not alerts.exists()


@pytest.mark.parametrize("push", [
    f'{LIVE_ALERT} "$_m"',
    f'_p={LIVE_ALERT}; "$_p" "$_m"',
    f'{ALERT_TWIN} "$_m"',
    'bd-push-alert.sh "$_m"',
    '"$BD_HARNESS_DIR/bd-push-alert.sh" "$_m"',
    '"$P/harness/bd-push-alert.sh" "$_m"',
], ids=["live-path", "live-path-variable", "home-twin", "path-lookup", "harness-dir", "persist-harness"])
def test_alert_stub_records_every_push_route(census, tmp_path, push):
    _, execute, _, alerts = census
    pusher = tmp_path / "pusher.sh"
    pusher.write_text(f'P=$BD_CHECKIN_PERSIST; _m="stale gate RED"\n{push} 2>/dev/null || true\n')
    execute(script=pusher)
    assert alerts.exists() and alerts.read_text() == "stale gate RED\n", "push route escaped the alert stub"
