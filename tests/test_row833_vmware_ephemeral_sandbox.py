"""Cut 833: ESXi instant-clone ephemeral sandbox contract."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_BIN = REPO_ROOT / "toolchain" / "bin" / "bd-vm-sandbox"


def _load_sandbox_module():
    import importlib.machinery
    import importlib.util
    assert SANDBOX_BIN.is_file(), f"toolchain binary missing: {SANDBOX_BIN}"
    loader = importlib.machinery.SourceFileLoader("bd_vm_sandbox", str(SANDBOX_BIN))
    spec = importlib.util.spec_from_loader("bd_vm_sandbox", loader)
    assert spec and spec.loader, "could not load spec for bd-vm-sandbox"
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bd_vm_sandbox"] = mod
    loader.exec_module(mod)
    return mod


def test_govc_clone_execute_destroy_workflow():
    """Verify standard happy-path: clone -> execute -> destroy lifecycle."""
    sandbox = _load_sandbox_module()

    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:

        mock_clone.return_value = {"ok": True, "clone_name": "bd-sandbox-1234"}
        mock_exec.return_value = {"ok": True, "exit_code": 0, "stdout": "PASSED"}
        mock_destroy.return_value = {"ok": True}

        result = sandbox.run_sandbox_workflow(
            cmd=["pytest", "tests/destructive/test_disk_exhaust.py"],
            vm_template="test-template",
            snapshot="GOLDEN-READY",
            clone_name="bd-sandbox-1234",
            govc_bin="/fake/govc",
        )

        assert result["ok"] is True
        assert result["exit_code"] == 0
        mock_clone.assert_called_once_with(
            vm_template="test-template",
            snapshot="GOLDEN-READY",
            clone_name="bd-sandbox-1234",
            govc_bin="/fake/govc",
            govc_env=None,
        )
        mock_exec.assert_called_once_with(
            clone_name="bd-sandbox-1234",
            cmd=["pytest", "tests/destructive/test_disk_exhaust.py"],
            govc_bin="/fake/govc",
            timeout=sandbox.DEFAULT_EXEC_TIMEOUT_S,
            govc_env=None,
        )
        assert sandbox.DEFAULT_EXEC_TIMEOUT_S == 600
        mock_destroy.assert_called_once_with(
            clone_name="bd-sandbox-1234",
            govc_bin="/fake/govc",
            govc_env=None,
        )


def test_automatic_destruction_trap_on_test_failure_or_timeout():
    """Verify that destroy_vm is always invoked even when execution fails or times out."""
    sandbox = _load_sandbox_module()

    # Case 1: Test execution fails (non-zero returncode or exception)
    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:

        mock_clone.return_value = {"ok": True, "clone_name": "bd-sandbox-fail"}
        mock_exec.return_value = {"ok": False, "exit_code": 1, "stderr": "FAILED"}
        mock_destroy.return_value = {"ok": True}

        result = sandbox.run_sandbox_workflow(
            cmd=["pytest", "tests/destructive/test_failing.py"],
            clone_name="bd-sandbox-fail",
            govc_bin="/fake/govc",
        )

        assert result["ok"] is False
        assert result["exit_code"] == 1
        mock_destroy.assert_called_once_with(
            clone_name="bd-sandbox-fail",
            govc_bin="/fake/govc",
            govc_env=None,
        )

    # Case 2: Test execution raises TimeoutExpired
    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:

        mock_clone.return_value = {"ok": True, "clone_name": "bd-sandbox-timeout"}
        mock_exec.side_effect = subprocess.TimeoutExpired(cmd="pytest", timeout=30)
        mock_destroy.return_value = {"ok": True}

        result = sandbox.run_sandbox_workflow(
            cmd=["pytest", "tests/destructive/test_hanging.py"],
            clone_name="bd-sandbox-timeout",
            govc_bin="/fake/govc",
        )

        assert result["ok"] is False
        assert "timeout" in result.get("error", "").lower()
        mock_destroy.assert_called_once_with(
            clone_name="bd-sandbox-timeout",
            govc_bin="/fake/govc",
            govc_env=None,
        )


def test_zero_host_workspace_mutations_on_test5(tmp_path):
    """Verify that running sandbox workflow leaves the local host workspace clean."""
    sandbox = _load_sandbox_module()

    # Create dummy host workspace
    host_workspace = tmp_path / "workspace"
    host_workspace.mkdir()
    (host_workspace / "clean_file.txt").write_text("unmodified")

    snapshot_before = {p: (p.stat().st_mtime, p.stat().st_size) for p in host_workspace.rglob("*")}

    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:

        mock_clone.return_value = {"ok": True, "clone_name": "bd-sandbox-clean"}
        mock_exec.return_value = {"ok": True, "exit_code": 0, "stdout": "clean run"}
        mock_destroy.return_value = {"ok": True}

        res = sandbox.run_sandbox_workflow(
            cmd=["destructive-clean-probe"],
            clone_name="bd-sandbox-clean",
            host_workspace=str(host_workspace),
        )
        assert res["ok"] is True

    snapshot_after = {p: (p.stat().st_mtime, p.stat().st_size) for p in host_workspace.rglob("*")}
    assert snapshot_before == snapshot_after, "Host workspace mutated by sandbox execution!"


def test_cli_execution_with_stub_govc(tmp_path):
    """Verify that calling toolchain/bin/bd-vm-sandbox CLI executes govc lifecycle."""
    log_file = tmp_path / "govc_calls.log"
    fake_govc = tmp_path / "fake_govc"
    fake_govc.write_text(f"""#!/bin/sh
echo "$@" >> "{log_file}"
exit 0
""")
    fake_govc.chmod(0o755)

    res = subprocess.run(
        [sys.executable, str(SANDBOX_BIN), "--govc-bin", str(fake_govc), "--clone-name", "stub-vm", "pytest", "dummy_test.py"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    calls = log_file.read_text().splitlines()
    assert any("vm.clone" in line and "stub-vm" in line for line in calls)
    assert any("guest.run" in line and "stub-vm" in line for line in calls)
    assert any("vm.destroy" in line and "stub-vm" in line for line in calls)



# ---- fixer (O928) controls for the correctness REFUTE E1/E2 (HIGH) ---------

def test_e1_signal_before_clone_never_destroys_the_template():
    """SIGTERM arriving before the clone exists: nothing is owned, nothing is
    destroyed (the old code emitted vm.destroy <template>)."""
    import signal as _signal
    sandbox = _load_sandbox_module()
    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:

        def clone_then_signal(**kw):
            handler = _signal.getsignal(_signal.SIGTERM)
            with pytest.raises(SystemExit) as exc:
                handler(_signal.SIGTERM, None)      # the trap fires while cloning
            assert exc.value.code == 128 + _signal.SIGTERM
            return {"ok": True, "clone_name": kw["clone_name"]}

        mock_clone.side_effect = clone_then_signal
        mock_exec.return_value = {"ok": True, "exit_code": 0}
        mock_destroy.return_value = {"ok": True}
        res = sandbox.run_sandbox_workflow(cmd=["true"], vm_template="template", clone_name="template",
                                           govc_bin="/fake/govc")
        assert res["ok"] is False and res["exit_code"] == 2 and "template" in res["error"]
        mock_destroy.assert_not_called()

        res = sandbox.run_sandbox_workflow(cmd=["true"], vm_template="template", clone_name="bd-e1",
                                           govc_bin="/fake/govc")
        # the trap fired before ownership existed: no destroy from the trap; exactly ONE from the normal path
        assert res["ok"] is True
        mock_destroy.assert_called_once_with(clone_name="bd-e1", govc_bin="/fake/govc", govc_env=None)
        assert all(c.kwargs["clone_name"] != "template" for c in mock_destroy.call_args_list)


def test_e1_signal_during_execution_destroys_exactly_once():
    import signal as _signal
    sandbox = _load_sandbox_module()
    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:
        mock_clone.return_value = {"ok": True, "clone_name": "bd-e1b"}
        mock_destroy.return_value = {"ok": True}

        def run_then_signal(**kw):
            handler = _signal.getsignal(_signal.SIGINT)
            handler(_signal.SIGINT, None)   # raises SystemExit after cleanup

        mock_exec.side_effect = run_then_signal
        with pytest.raises(SystemExit) as exc:
            sandbox.run_sandbox_workflow(cmd=["true"], clone_name="bd-e1b", govc_bin="/fake/govc")
        assert exc.value.code == 128 + _signal.SIGINT
        mock_destroy.assert_called_once_with(clone_name="bd-e1b", govc_bin="/fake/govc", govc_env=None)


def test_e1_unresolved_clone_identity_destroys_nothing():
    sandbox = _load_sandbox_module()
    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:
        mock_clone.return_value = {"ok": True, "clone_name": "something-else"}
        res = sandbox.run_sandbox_workflow(cmd=["true"], clone_name="bd-e1c", govc_bin="/fake/govc")
        assert res["ok"] is False and res["exit_code"] == 3 and "unresolved" in res["error"]
        mock_exec.assert_not_called()
        mock_destroy.assert_not_called()


def test_e2_destroy_failure_is_propagated_after_a_successful_run(tmp_path):
    sandbox = _load_sandbox_module()
    with patch.object(sandbox, "clone_vm") as mock_clone, \
         patch.object(sandbox, "execute_in_vm") as mock_exec, \
         patch.object(sandbox, "destroy_vm") as mock_destroy:
        mock_clone.return_value = {"ok": True, "clone_name": "bd-e2"}
        mock_exec.return_value = {"ok": True, "exit_code": 0, "stdout": "all green\n"}
        mock_destroy.return_value = {"ok": False, "error": "vm.destroy: permission denied"}
        res = sandbox.run_sandbox_workflow(cmd=["true"], clone_name="bd-e2", govc_bin="/fake/govc")
        assert res["ok"] is False and res["exit_code"] == 3
        assert res["guest_exit_code"] == 0 and res["destroyed"] is False
        assert "NOT destroyed" in res["error"] and "permission denied" in res["destroy_error"]
        mock_destroy.assert_called_once()

    # CLI: the same case exits nonzero and names the surviving VM
    govc = tmp_path / "govc"
    govc.write_text("#!/bin/sh\ncase \"$1\" in vm.destroy) echo 'destroy refused' >&2; exit 1;; esac\nexit 0\n")
    govc.chmod(0o755)
    proc = subprocess.run([sys.executable, str(SANDBOX_BIN), "--govc-bin", str(govc), "--clone-name", "bd-e2-cli",
                           "--", "true"], capture_output=True, text=True)
    assert proc.returncode == 3, proc.stdout + proc.stderr
    assert "bd-e2-cli" in proc.stderr and "NOT destroyed" in proc.stderr
