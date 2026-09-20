"""tests/test_esxi_vm_clone_harness.py -- Tests for Row 860 hermetic VM clone harness.

Covers:
- Fixture clone completes under 15s.
- Remote command and artifact retrieval path works.
- Teardown is guaranteed on both success and exception.
- Never targets a gateway or an operator-owned live pane (test2, test5, 10.0.70.1, 10.0.70.95, etc.).
- Rejects non-approved VMs while allowing approved test targets (spare1-12, test3/4/6/7, fixture-*).
"""
import os
import shutil
import tempfile
import time
import pytest

import importlib.util
import sys
from pathlib import Path

_HARNESS_PATH = Path(__file__).resolve().parents[1] / "tools" / "esxi_vm_clone_harness.py"
_spec = importlib.util.spec_from_file_location("esxi_vm_clone_harness", _HARNESS_PATH)
assert _spec and _spec.loader, f"Failed to load spec for {_HARNESS_PATH}"
_mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _mod
_spec.loader.exec_module(_mod)

ESXiVMCloneHarness = _mod.ESXiVMCloneHarness
SecurityViolationError = _mod.SecurityViolationError
DisallowedVMError = _mod.DisallowedVMError
VMCloneError = _mod.VMCloneError
validate_target = _mod.validate_target
_ACTIVE_HARNESSES = _mod._ACTIVE_HARNESSES
_atexit_cleanup = _mod._atexit_cleanup

BD_GATE_SCOPE = "module"


class TestESXiVMCloneHarness:
    """Test suite verifying safety invariants, teardown guarantee, and artifact handling."""

    def test_fixture_clone_completes_under_15_seconds(self) -> None:
        """Acceptance test: fixture clone completes in under 15 seconds."""
        harness = ESXiVMCloneHarness(
            template="fixture-template-01",
            clone_name="fixture-clone-01",
            backend="mock",
        )
        try:
            harness.clone()
            assert harness.is_active is True
            assert harness.is_destroyed is False
            assert harness.duration < 15.0, f"Clone took {harness.duration}s, expected < 15.0s"
        finally:
            harness.destroy()

    def test_remote_command_and_artifact_path(self) -> None:
        """Acceptance test: remote command execution and artifact retrieval."""
        temp_dest = tempfile.mkdtemp(prefix="test_art_dest_")
        try:
            with ESXiVMCloneHarness(
                template="fixture-template-runner",
                clone_name="fixture-clone-cmd",
                backend="mock",
            ) as vm:
                # 1. Run remote command inside VM guest
                res = vm.run_command("echo 'test artifact content' > artifact.txt && cat artifact.txt")
                assert res.returncode == 0
                assert "test artifact content" in res.stdout
                assert res.duration < 5.0

                # 2. Retrieve artifact from VM
                retrieved = vm.retrieve_artifacts("artifact.txt", temp_dest)
                assert len(retrieved) == 1
                assert os.path.exists(retrieved[0])

                with open(retrieved[0], "r", encoding="utf-8") as f:
                    content = f.read().strip()
                assert content == "test artifact content"
        finally:
            shutil.rmtree(temp_dest, ignore_errors=True)

    def test_teardown_guaranteed_on_success(self) -> None:
        """Teardown guarantee: resources are freed upon normal context manager exit."""
        harness = None
        with ESXiVMCloneHarness(
            template="mock-template",
            clone_name="mock-clone-success",
            backend="mock",
        ) as vm:
            harness = vm
            assert vm.is_active is True
            assert vm.is_destroyed is False

        assert harness is not None
        assert harness.is_active is False
        assert harness.is_destroyed is True
        assert harness not in _ACTIVE_HARNESSES

    def test_teardown_guaranteed_on_exception(self) -> None:
        """Teardown guarantee: resources are freed even when an unhandled exception occurs."""
        harness = None
        with pytest.raises(RuntimeError, match="simulated failure inside VM block"):
            with ESXiVMCloneHarness(
                template="mock-template",
                clone_name="mock-clone-fail",
                backend="mock",
            ) as vm:
                harness = vm
                assert vm.is_active is True
                raise RuntimeError("simulated failure inside VM block")

        assert harness is not None
        assert harness.is_active is False
        assert harness.is_destroyed is True
        assert harness not in _ACTIVE_HARNESSES

    @pytest.mark.parametrize(
        "target",
        [
            "10.0.70.1",
            "10.0.20.1",
            "gateway",
            "pfsense-router",
            "core-router-01",
        ],
    )
    def test_never_target_gateway(self, target: str) -> None:
        """Safety invariant: gateways and routers must never be targeted."""
        with pytest.raises(SecurityViolationError, match="forbidden pattern"):
            validate_target(target)

        with pytest.raises(SecurityViolationError):
            ESXiVMCloneHarness(template=target, clone_name="fixture-test")
        with pytest.raises(SecurityViolationError):
            ESXiVMCloneHarness(template="fixture-test", clone_name=target)

    @pytest.mark.parametrize(
        "target",
        [
            "test2",
            "10.0.70.95",
            "test5",
            "10.0.70.164",
            "bd-capture-test2",
            "live-pane-session",
            "operator-box",
            "10.0.20.70",
            "vc04",
            "vcenter.boylenet.themfboyles.com",
            "nsx-appliance",
        ],
    )
    def test_never_target_operator_live_pane_or_infrastructure(self, target: str) -> None:
        """Safety invariant: test2, test5, live panes, and vCenter appliances must be rejected."""
        with pytest.raises(SecurityViolationError, match="forbidden pattern"):
            validate_target(target)

        with pytest.raises(SecurityViolationError):
            ESXiVMCloneHarness(template=target, clone_name="fixture-test")
        with pytest.raises(SecurityViolationError):
            ESXiVMCloneHarness(template="fixture-test", clone_name=target)

    @pytest.mark.parametrize(
        "target",
        [
            "llama",
            "Bittorrent",
            "Stash",
            "Windows test box",
            "Linux/AI Pipeline/AI Inference",
            "random_vm_name",
        ],
    )
    def test_never_target_disallowed_vms(self, target: str) -> None:
        """Safety invariant: only approved test runner templates may be targeted."""
        with pytest.raises(DisallowedVMError, match="not an approved test VM template"):
            validate_target(target)

        with pytest.raises(DisallowedVMError):
            ESXiVMCloneHarness(template=target, clone_name="fixture-test")
        with pytest.raises(DisallowedVMError):
            ESXiVMCloneHarness(template="fixture-test", clone_name=target)

    @pytest.mark.parametrize(
        "target",
        [
            "spare1",
            "spare5",
            "spare12",
            "/Boylenet Datacenter/vm/Linux/Spare/spare7",
            "test3",
            "test4",
            "test6",
            "test7",
            "/Boylenet Datacenter/vm/Linux/bd/test3",
            "fixture-test-node",
            "mock-test-runner",
            "test-template-v1",
        ],
    )
    def test_approved_templates_allowed(self, target: str) -> None:
        """Verification: approved test VMs and fixture patterns pass validation."""
        assert validate_target(target) is True

    def test_atexit_cleanup_hook(self) -> None:
        """Verification: atexit hook cleans up un-destroyed active harnesses."""
        h = ESXiVMCloneHarness(
            template="fixture-template-atexit",
            clone_name="fixture-clone-atexit",
            backend="mock",
        )
        h.clone()
        assert h in _ACTIVE_HARNESSES
        assert h.is_active is True

        _atexit_cleanup()
        assert h.is_active is False
        assert h.is_destroyed is True
        assert h not in _ACTIVE_HARNESSES


# ── FIXER (row860 REFUTE E1 HIGH / E2 / E3) ──────────────────────────────

_govc_env = _mod._govc_env
_read_govc_credentials_file = _mod._read_govc_credentials_file


def test_no_credential_literal_in_source():
    """E1 HIGH: the harness source carries no URL-embedded credential and no
    default GOVC_URL at all."""
    src = _HARNESS_PATH.read_text(encoding="utf-8")
    assert "%40boylenet" not in src
    assert not any(line for line in src.splitlines()
                   if "GOVC_URL" in line and "https://" in line)
    assert 'setdefault("GOVC_URL"' not in src and "setdefault('GOVC_URL'" not in src


def test_govc_env_requires_a_configured_url(monkeypatch, tmp_path):
    monkeypatch.delenv("GOVC_URL", raising=False)
    monkeypatch.setattr(_mod, "GOVC_CREDENTIALS_FILE", tmp_path / "absent.env")
    monkeypatch.setattr(_mod, "_read_govc_credentials_file",
                        lambda path=tmp_path / "absent.env": _read_govc_credentials_file(path))
    with pytest.raises(VMCloneError, match="GOVC_URL is not configured"):
        _govc_env()


def test_govc_env_prefers_process_env_then_credentials_file(monkeypatch, tmp_path):
    cred = tmp_path / "GOVC.env"
    cred.write_text("# fixture\nGOVC_URL=https://file.invalid/sdk\nGOVC_USERNAME='fixture-user'\n")
    monkeypatch.setattr(_mod, "_read_govc_credentials_file",
                        lambda path=cred: _read_govc_credentials_file(path))
    monkeypatch.delenv("GOVC_URL", raising=False)
    env = _govc_env()
    assert env["GOVC_URL"] == "https://file.invalid/sdk" and env["GOVC_USERNAME"] == "fixture-user"
    assert env["GOVC_INSECURE"] == "1"
    monkeypatch.setenv("GOVC_URL", "https://env.invalid/sdk")
    assert _govc_env()["GOVC_URL"] == "https://env.invalid/sdk"


def test_auto_backend_never_mocks_a_real_target_when_govc_is_missing():
    """E2 (FLEET_RULE 46): a real target with govc absent is an error, not a
    local mock that runs the build on this host."""
    with pytest.raises(VMCloneError, match="refusing to fall back to the mock backend"):
        ESXiVMCloneHarness("spare1", "spare1", govc_bin="/nonexistent/govc")


def test_auto_backend_mocks_only_fixture_names():
    h = ESXiVMCloneHarness("fixture-template-01", "fixture-clone-01", govc_bin="/nonexistent/govc")
    assert h.backend == "mock"
    with pytest.raises(VMCloneError):
        ESXiVMCloneHarness("fixture-template-01", "spare2", govc_bin="/nonexistent/govc")


def test_unknown_backend_is_rejected():
    with pytest.raises(ValueError):
        ESXiVMCloneHarness("fixture-template-01", "fixture-clone-01", backend="local")


def test_retrieve_artifacts_is_confined_to_the_mock_guest_root(tmp_path):
    """E3: a traversal path never reads the host filesystem."""
    with ESXiVMCloneHarness("fixture-template-01", "fixture-clone-01", backend="mock") as h:
        with pytest.raises(SecurityViolationError, match="escapes the mock guest root"):
            h.retrieve_artifacts("../../../../../../etc/hostname", str(tmp_path))
        assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("target", ["spare0", "spare13", "spare99", "spare"])
def test_spare_pool_is_exactly_1_to_12(target):
    with pytest.raises((DisallowedVMError, SecurityViolationError)):
        validate_target(target)


@pytest.mark.parametrize("target", ["spare1", "spare12", "/Boylenet Datacenter/vm/Linux/Spare/spare7"])
def test_spare_pool_members_are_allowed(target):
    validate_target(target)
