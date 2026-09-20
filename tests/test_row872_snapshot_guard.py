"""Cut 872: VM snapshot rollback on catastrophic fault contract tests."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GUARD_BIN = REPO_ROOT / "toolchain" / "bin" / "bd-snapshot-guard"


def _load_guard_module():
    import importlib.machinery
    import importlib.util
    assert GUARD_BIN.is_file(), f"toolchain binary missing: {GUARD_BIN}"
    loader = importlib.machinery.SourceFileLoader("bd_snapshot_guard", str(GUARD_BIN))
    spec = importlib.util.spec_from_loader("bd_snapshot_guard", loader)
    assert spec and spec.loader, "could not load spec for bd-snapshot-guard"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bd_snapshot_guard"] = mod
    loader.exec_module(mod)
    return mod


def test_snapshot_rollback_triggered_on_simulated_system_corruption():
    """Verify that govc snapshot.revert to GOLDEN-READY is triggered when system corruption occurs."""
    guard = _load_guard_module()

    with patch.object(guard, "execute_in_vm") as mock_exec, \
         patch.object(guard, "check_system_health") as mock_health, \
         patch.object(guard, "revert_snapshot") as mock_revert:

        # Test execution causes catastrophic systemd/OS corruption
        mock_exec.return_value = {"ok": False, "exit_code": 139, "stderr": "kernel panic / systemd crash"}
        # Health check reports corruption
        mock_health.side_effect = [
            {"healthy": False, "status": "corrupted"},  # Post-execution health check
            {"healthy": True, "status": "clean"},       # Health check post-revert
        ]
        mock_revert.return_value = {"ok": True, "snapshot": "GOLDEN-READY"}

        res = guard.run_with_snapshot_guard(
            cmd=["destructive_suite.sh"],
            vm_name="test-runner-01",
            snapshot="GOLDEN-READY",
            govc_bin="/fake/govc",
        )

        assert res["ok"] is False
        assert res["corrupted"] is True
        assert res["reverted"] is True
        mock_revert.assert_called_once_with(
            vm_name="test-runner-01",
            snapshot="GOLDEN-READY",
            govc_bin="/fake/govc",
            govc_env=None,
        )


def test_confirmation_of_clean_vm_state_post_revert():
    """Verify that clean VM state is confirmed post-revert and reported in result."""
    guard = _load_guard_module()

    with patch.object(guard, "execute_in_vm") as mock_exec, \
         patch.object(guard, "check_system_health") as mock_health, \
         patch.object(guard, "revert_snapshot") as mock_revert:

        mock_exec.return_value = {"ok": False, "exit_code": 1, "stderr": "critical fault"}
        mock_health.side_effect = [
            {"healthy": False, "status": "corrupted"},
            {"healthy": True, "status": "clean_operational"},
        ]
        mock_revert.return_value = {"ok": True}

        res = guard.run_with_snapshot_guard(
            cmd=["fault_inducer.sh"],
            vm_name="test-runner-02",
            snapshot="GOLDEN-READY",
            govc_bin="/fake/govc",
        )

        assert res["reverted"] is True
        assert res["post_revert_state"]["healthy"] is True
        assert res["post_revert_state"]["status"] == "clean_operational"


def test_zero_host_data_loss(tmp_path):
    """Verify that hypervisor VM snapshot rollback causes zero mutation or data loss on host test5."""
    guard = _load_guard_module()

    host_data = tmp_path / "host_data"
    host_data.mkdir()
    critical_file = host_data / "active_worktree_file.py"
    critical_file.write_text("critical_host_code = 42\n")

    snapshot_before = {p: (p.stat().st_mtime, p.stat().st_size) for p in host_data.rglob("*")}

    with patch.object(guard, "execute_in_vm") as mock_exec, \
         patch.object(guard, "check_system_health") as mock_health, \
         patch.object(guard, "revert_snapshot") as mock_revert:

        mock_exec.return_value = {"ok": False, "exit_code": 255, "stderr": "system corrupted"}
        mock_health.side_effect = [
            {"healthy": False, "status": "corrupted"},
            {"healthy": True, "status": "clean"},
        ]
        mock_revert.return_value = {"ok": True}

        res = guard.run_with_snapshot_guard(
            cmd=["corrupt_vm.sh"],
            vm_name="test-runner-03",
            snapshot="GOLDEN-READY",
            host_data_paths=[str(host_data)],
            govc_bin="/fake/govc",
        )
        assert res["reverted"] is True

    snapshot_after = {p: (p.stat().st_mtime, p.stat().st_size) for p in host_data.rglob("*")}
    assert snapshot_before == snapshot_after, "Host data was mutated or lost during VM snapshot rollback!"


# ---- fixer (O928) controls for the correctness REFUTE E1/E2 -------------

def _run(guard, exec_res, healths, revert=None, argv=("--vm", "vm-x", "--", "suite.sh")):
    with patch.object(guard, "execute_in_vm", return_value=exec_res), \
         patch.object(guard, "check_system_health", side_effect=list(healths)), \
         patch.object(guard, "revert_snapshot", return_value=revert or {"ok": True}) as mock_revert, \
         patch.object(sys, "argv", ["bd-snapshot-guard", *argv]):
        rc = guard.main()
    return rc, mock_revert


def test_e1_guest_exit_zero_but_maintenance_state_exits_nonzero():
    """E1: guest exits 0, systemd reports maintenance -> revert, and the CLI
    must NOT exit 0 (corruption happened, even though it was recovered)."""
    guard = _load_guard_module()
    rc, mock_revert = _run(
        guard,
        {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
        [{"healthy": False, "status": "corrupted", "detail": "maintenance"},
         {"healthy": True, "status": "clean"}],
    )
    mock_revert.assert_called_once()
    assert rc == guard.EXIT_CORRUPTED_RECOVERED != 0


def test_e1_failed_revert_or_unhealthy_post_state_exits_recovery_failed():
    guard = _load_guard_module()
    rc, _ = _run(
        guard,
        {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
        [{"healthy": False, "status": "corrupted"}, {"healthy": True, "status": "clean"}],
        revert={"ok": False, "error": "snapshot.revert: not found"},
    )
    assert rc == guard.EXIT_RECOVERY_FAILED
    rc, _ = _run(
        guard,
        {"ok": False, "exit_code": 139, "stdout": "", "stderr": "segv"},
        [{"healthy": False, "status": "corrupted"}, {"healthy": False, "status": "corrupted"}],
        revert={"ok": True},
    )
    assert rc == guard.EXIT_RECOVERY_FAILED


def test_e1_guest_failure_code_is_preserved_when_recovered():
    """DO-NOT-TOUCH: a fatal guest code (139) survives a successful recovery."""
    guard = _load_guard_module()
    rc, mock_revert = _run(
        guard,
        {"ok": False, "exit_code": 139, "stdout": "", "stderr": "segv"},
        [{"healthy": False, "status": "corrupted"}, {"healthy": True, "status": "clean"}],
    )
    mock_revert.assert_called_once()
    assert rc == 139


@pytest.mark.parametrize("proc", [
    # bad guest credentials: govc fault, nothing from systemctl
    dict(returncode=1, stdout="", stderr="govc: ServerFaultCode: The guest authentication is invalid"),
    # systemctl missing in the guest
    dict(returncode=127, stdout="", stderr="sh: systemctl: not found"),
    # empty answer
    dict(returncode=0, stdout="", stderr=""),
])
def test_e2_observer_failure_is_unknown_not_corruption(proc):
    """E2: no systemd state word -> status 'unknown'; the workflow reports it
    (nonzero CLI exit) but never emits snapshot.revert."""
    guard = _load_guard_module()
    fake = MagicMock(**proc)
    with patch.object(guard.subprocess, "run", return_value=fake):
        health = guard.check_system_health("vm-x", govc_bin="/fake/govc")
    assert health == {"healthy": False, "status": "unknown", "detail": health["detail"]}
    assert health["status"] == "unknown"

    rc, mock_revert = _run(
        guard,
        {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
        [health],
    )
    mock_revert.assert_not_called()
    assert rc == guard.EXIT_HEALTH_UNKNOWN != 0


def test_e2_systemd_unhealthy_word_is_proven_corruption():
    guard = _load_guard_module()
    fake = MagicMock(returncode=1, stdout="maintenance\n", stderr="")
    with patch.object(guard.subprocess, "run", return_value=fake):
        health = guard.check_system_health("vm-x", govc_bin="/fake/govc")
    assert health["status"] == "corrupted" and health["healthy"] is False
    fake = MagicMock(returncode=1, stdout="degraded\n", stderr="")
    with patch.object(guard.subprocess, "run", return_value=fake):
        assert guard.check_system_health("vm-x", govc_bin="/fake/govc")["healthy"] is True


def test_healthy_success_still_exits_with_guest_code():
    guard = _load_guard_module()
    rc, mock_revert = _run(guard, {"ok": True, "exit_code": 0, "stdout": "ok\n", "stderr": ""},
                           [{"healthy": True, "status": "clean"}])
    mock_revert.assert_not_called()
    assert rc == 0
    rc, mock_revert = _run(guard, {"ok": False, "exit_code": 7, "stdout": "", "stderr": ""},
                           [{"healthy": True, "status": "clean"}])
    mock_revert.assert_not_called()
    assert rc == 7


# ---- fixer (O928) round 2: correctness REFUTE E1/E2/E3 ------------------

def test_r2_e1_health_probe_is_bounded_and_a_timeout_is_not_corruption():
    """E1: a guest that never answers (kernel panic, dead vmtoolsd) cannot
    hang the guard: subprocess.run is called WITH a timeout and the expiry
    is reported as status 'timeout' -- an observation, not a corruption
    finding (no revert authority by itself)."""
    guard = _load_guard_module()
    seen = {}

    def hang(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    with patch.object(guard.subprocess, "run", side_effect=hang):
        health = guard.check_system_health("vm-x", govc_bin="/fake/govc", timeout=7)
        alive = guard.check_guest_responsive("vm-x", govc_bin="/fake/govc", timeout=7)
    assert seen["timeout"] == 7 and health == {"healthy": False, "status": "timeout", "detail": "no answer in 7s"}
    assert alive["responsive"] is False
    # default bound is the module constant, never None (positive control)
    with patch.object(guard.subprocess, "run", side_effect=hang):
        guard.check_system_health("vm-x", govc_bin="/fake/govc")
    assert seen["timeout"] == guard.PROBE_TIMEOUT_S > 0


def _guarded(guard, exec_res, healths, baseline, **kw):
    with patch.object(guard, "execute_in_vm", return_value=exec_res), \
         patch.object(guard, "check_guest_responsive", return_value=baseline), \
         patch.object(guard, "check_system_health", side_effect=list(healths)) as mock_health, \
         patch.object(guard, "revert_snapshot", return_value={"ok": True}) as mock_revert, \
         patch.object(guard.time, "sleep") as mock_sleep:
        res = guard.run_with_snapshot_guard(cmd=["suite.sh"], vm_name="vm-x", govc_bin="/fake/govc", **kw)
    return res, mock_revert, mock_health, mock_sleep


def test_r2_e2_a_guest_that_answered_before_and_not_after_is_reverted():
    """E2: the run froze the guest -- it answered the pre-run positive control,
    then the health probe times out twice (one bounded retry). That is a
    catastrophic fault: revert, post-revert clean, exit CORRUPTED_RECOVERED."""
    guard = _load_guard_module()
    res, mock_revert, mock_health, mock_sleep = _guarded(
        guard, {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
        [{"healthy": False, "status": "timeout", "detail": "no answer in 30s"},
         {"healthy": False, "status": "unknown", "detail": "unreachable: agent gone"},
         {"healthy": True, "status": "clean"}],
        baseline={"responsive": True, "detail": ""})
    mock_revert.assert_called_once()
    mock_sleep.assert_called_once_with(guard.UNRESPONSIVE_RETRY_DELAY_S)
    assert res["corrupted"] and res["reverted"] and res["recovered"]
    assert res["health"]["status"] == "unresponsive" and res["exit_code"] == guard.EXIT_CORRUPTED_RECOVERED
    assert mock_health.call_count == 3


def test_r2_e2_a_guest_that_recovers_on_the_retry_is_not_reverted():
    """A transient probe failure (one timeout, then a clean answer) is not a
    freeze: no revert, the run's own exit code stands."""
    guard = _load_guard_module()
    res, mock_revert, mock_health, _ = _guarded(
        guard, {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
        [{"healthy": False, "status": "timeout", "detail": ""}, {"healthy": True, "status": "clean"}],
        baseline={"responsive": True, "detail": ""})
    mock_revert.assert_not_called()
    assert res["ok"] is True and res["exit_code"] == 0 and mock_health.call_count == 2


def test_r2_e2_without_a_baseline_an_unanswering_guest_is_unknown_not_reverted():
    """Negative control (FLEET_RULE 7): a guest that never answered -- not
    even before the run -- proves nothing about the run; unknown is not
    permission: no revert, EXIT_HEALTH_UNKNOWN."""
    guard = _load_guard_module()
    res, mock_revert, mock_health, mock_sleep = _guarded(
        guard, {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""},
        [{"healthy": False, "status": "timeout", "detail": "no answer in 30s"}],
        baseline={"responsive": False, "detail": "no answer in 30s"})
    mock_revert.assert_not_called()
    mock_sleep.assert_not_called()
    assert res["corrupted"] is False and res["health_unknown"] is True
    assert res["exit_code"] == guard.EXIT_HEALTH_UNKNOWN and mock_health.call_count == 1


def test_r2_e3_host_data_is_fingerprinted_and_a_change_under_the_guard_exits_6(tmp_path):
    """E3: criterion 3 is MEASURED. The guarded host path is fingerprinted
    before the run and after the revert; a byte written under the guard
    (here by the fake guest execution) is reported, fails the run and takes
    EXIT_HOST_DATA_CHANGED -- and an untouched path reports intact (control)."""
    guard = _load_guard_module()
    host = tmp_path / "host_data"; host.mkdir()
    keep = host / "worktree.py"; keep.write_text("critical = 42\n")
    healths = [{"healthy": False, "status": "corrupted", "detail": "maintenance"}, {"healthy": True, "status": "clean"}]

    def clobbering_exec(**kw):
        keep.write_text("critical = 0\n")           # same size, new mtime/content
        (host / "new.log").write_text("spill\n")     # a file that was not there
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
    with patch.object(guard, "execute_in_vm", side_effect=clobbering_exec), \
         patch.object(guard, "check_guest_responsive", return_value={"responsive": True, "detail": ""}), \
         patch.object(guard, "check_system_health", side_effect=list(healths)), \
         patch.object(guard, "revert_snapshot", return_value={"ok": True}):
        res = guard.run_with_snapshot_guard(cmd=["suite.sh"], vm_name="vm-x", govc_bin="/fake/govc",
                                            host_data_paths=[str(host)])
    assert res["reverted"] is True and res["host_data_intact"] is False
    assert res["host_data_changed"] == sorted([str(keep), str(host / "new.log")])
    assert res["ok"] is False and res["exit_code"] == guard.EXIT_HOST_DATA_CHANGED == 6

    res, *_ = _guarded(guard, {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}, healths,
                       baseline={"responsive": True, "detail": ""}, host_data_paths=[str(tmp_path / "host_data")])
    assert res["host_data_intact"] is True and res["host_data_changed"] == [] and res["exit_code"] == guard.EXIT_CORRUPTED_RECOVERED


def test_r2_e3_cli_takes_host_data_paths_and_reports_the_change(tmp_path, capsys):
    guard = _load_guard_module()
    f = tmp_path / "state.db"; f.write_bytes(b"abc")

    def clobbering_exec(**kw):
        f.write_bytes(b"abcd")
        return {"ok": True, "exit_code": 0, "stdout": "", "stderr": ""}
    with patch.object(guard, "execute_in_vm", side_effect=clobbering_exec), \
         patch.object(guard, "check_guest_responsive", return_value={"responsive": True, "detail": ""}), \
         patch.object(guard, "check_system_health", return_value={"healthy": True, "status": "clean"}), \
         patch.object(guard, "revert_snapshot") as mock_revert, \
         patch.object(sys, "argv", ["bd-snapshot-guard", "--vm", "vm-x", "--host-data-path", str(f), "--", "suite.sh"]):
        rc = guard.main()
    mock_revert.assert_not_called()
    assert rc == 6 and "HOST DATA CHANGED" in capsys.readouterr().err
