#!/usr/bin/env python3
"""tools/esxi_vm_clone_harness.py -- Isolated ESXi / vCenter VM Clone Execution Harness.

Row 860: Hermetic VM clone harness for isolated test execution.
- Clones only approved test VMs / templates (e.g. spare1-12, test3/4/6/7, fixture templates).
- Strictly rejects gateways, infrastructure appliances, live test2 site, and operator host test5.
- Guarantees teardown on completion, failure, or exception via context manager and atexit.
- Supports remote command execution, artifact retrieval, and hermetic mock/fixture modes.
"""
from __future__ import annotations

import atexit
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class SecurityViolationError(ValueError):
    """Raised when an attempt is made to target a forbidden machine, gateway, or live pane."""
    pass


class DisallowedVMError(ValueError):
    """Raised when an attempt is made to target a VM that is not in the approved test allowlist."""
    pass


class VMCloneError(RuntimeError):
    """Raised when VM clone creation fails."""
    pass


class VMTeardownError(RuntimeError):
    """Raised when VM teardown fails."""
    pass


class VMCommandTimeoutError(TimeoutError):
    """Raised when a remote command inside the cloned VM times out."""
    pass


# Forbidden target names, IPs, and keywords (Gateways, Operator Panes, Live Sites, Infrastructure)
FORBIDDEN_TARGET_PATTERNS = [
    r"^10\.0\.70\.1$",              # Subnet Gateway
    r"^10\.0\.20\.1$",              # Management Gateway
    r"^10\.0\.20\.70$",             # vCenter Server Appliance
    r"^10\.0\.70\.95$",             # test2 (Fleet Rule 21: Live sites)
    r"^10\.0\.70\.164$",            # test5 (hub-mesh01, operator/daemon host)
    r"\bgateway\b",
    r"\brouter\b",
    r"\bpfsense\b",
    r"\btest2\b",                   # test2 live machine
    r"\btest5\b",                   # test5 seat host
    r"\bbd-capture-test2\b",
    r"\bvc04\b",                    # vCenter
    r"\bvcenter\b",
    r"\bnsx\b",                     # NSX appliance
    r"\bvrealize\b",
    r"\bskyline\b",
    r"\bvsan\b",
    r"\bvcls\b",
    r"\boperator\b",
    r"\blive-pane\b",
]

# Approved test VM patterns (spare nodes, test runner pools, hermetic test fixtures)
APPROVED_TARGET_PATTERNS = [
    r"^(/Boylenet Datacenter/vm/)?Linux/Spare/spare([1-9]|1[0-2])$",
    r"^spare([1-9]|1[0-2])$",
    r"^(/Boylenet Datacenter/vm/)?Linux/bd/test[3467]$",
    r"^test[3467]$",
    r"^fixture-.*$",
    r"^mock-.*$",
    r"^test-template-.*$",
]


# row860 fixer (E1 HIGH): vCenter credentials are NEVER a literal in source.
# GOVC_URL (and optionally GOVC_USERNAME/GOVC_PASSWORD) come from the
# process environment, else from the operator's credentials file below
# (KEY=VALUE lines, mode 0600, outside the repo -- the ILOM.txt convention of
# FLEET_RULE 52). Missing both is a configuration error, not a fallback.
GOVC_CREDENTIALS_FILE = Path.home() / ".bd-import" / "GOVC.env"
_GOVC_KEYS = ("GOVC_URL", "GOVC_USERNAME", "GOVC_PASSWORD", "GOVC_INSECURE")


def _read_govc_credentials_file(path: Path = GOVC_CREDENTIALS_FILE) -> Dict[str, str]:
    """KEY=VALUE lines for the GOVC_* keys; empty when the file is absent."""
    found: Dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in _GOVC_KEYS:
            found[key] = value.strip().strip('"').strip("'")
    return found


def _govc_env() -> Dict[str, str]:
    """The environment for a govc invocation: the process environment,
    completed from the credentials file, never from a literal. Raises
    VMCloneError when GOVC_URL is configured nowhere."""
    env = os.environ.copy()
    for key, value in _read_govc_credentials_file().items():
        env.setdefault(key, value)
    env.setdefault("GOVC_INSECURE", "1")
    if not env.get("GOVC_URL"):
        raise VMCloneError(
            "GOVC_URL is not configured: set it in the environment or in "
            f"{GOVC_CREDENTIALS_FILE} (KEY=VALUE); the harness carries no credential of its own."
        )
    return env

_FIXTURE_NAME_RE = re.compile(r"^(fixture-|mock-|test-template-)")


def _is_fixture_name(name: str) -> bool:
    return bool(_FIXTURE_NAME_RE.search(name))


# Global registry of active harnesses for guaranteed teardown on sudden process exit
_ACTIVE_HARNESSES: Set["ESXiVMCloneHarness"] = set()


def _atexit_cleanup() -> list[str]:
    """Ensure any active un-destroyed VM clone is cleaned up before process termination."""
    errors = []
    for harness in list(_ACTIVE_HARNESSES):
        try:
            if harness.is_active and not harness.is_destroyed:
                harness.destroy()
        except Exception as exc:
            errors.append(str(exc))
    return errors


atexit.register(_atexit_cleanup)


def validate_target(template_or_vm: str) -> bool:
    """Validate that target VM/template is safe, approved, and never a gateway or live host."""
    clean_target = template_or_vm.strip().lower()
    if not clean_target:
        raise DisallowedVMError("Target VM/template name cannot be empty.")

    # 1. Denylist check: reject any forbidden target
    for pattern in FORBIDDEN_TARGET_PATTERNS:
        if re.search(pattern, clean_target, re.IGNORECASE):
            raise SecurityViolationError(
                f"Security violation: target '{template_or_vm}' matches forbidden pattern '{pattern}'. "
                "Never target a gateway, infrastructure appliance, live site (test2), or operator host (test5)."
            )

    # 2. Allowlist check: must match an approved test target pattern
    matched = False
    for pattern in APPROVED_TARGET_PATTERNS:
        if re.search(pattern, template_or_vm, re.IGNORECASE):
            matched = True
            break

    if not matched:
        raise DisallowedVMError(
            f"Disallowed target: '{template_or_vm}' is not an approved test VM template. "
            "Approved targets: spare1-12, test3/4/6/7, fixture-*, mock-*."
        )

    return True


@dataclass
class CommandResult:
    command: str
    stdout: str
    stderr: str
    returncode: int
    duration: float


class ESXiVMCloneHarness:
    """Hermetic VM Clone Harness with guaranteed teardown and safety invariant enforcement."""

    def __init__(
        self,
        template: str,
        clone_name: str,
        snapshot: str = "",
        backend: str = "auto",
        timeout: float = 30.0,
        govc_bin: Optional[str] = None,
    ) -> None:
        self.template = template.strip()
        self.clone_name = clone_name.strip()
        self.snapshot = snapshot.strip()
        self.timeout = timeout
        self.govc_bin = govc_bin or "/home/mboyle/.local/bin/govc"

        validate_target(self.template)
        validate_target(self.clone_name)

        if backend == "auto":
            # row860 fixer (E2, FLEET_RULE 46: fail closed): only a fixture/
            # mock/test-template name selects the mock backend. A real target
            # with govc missing is an ERROR, never a local mock that runs the
            # "isolated VM build" on this host.
            if _is_fixture_name(self.template) and _is_fixture_name(self.clone_name):
                self.backend = "mock"
            elif not os.path.exists(self.govc_bin):
                raise VMCloneError(
                    f"govc not found at {self.govc_bin} and target {self.template!r} is not a "
                    "fixture; refusing to fall back to the mock backend for a real VM"
                )
            else:
                self.backend = "govc"
        elif backend in ("mock", "govc"):
            self.backend = backend
        else:
            raise ValueError(f"unknown backend {backend!r}")

        self.is_active = False
        self.is_destroyed = False
        self.created_at: Optional[float] = None
        self.duration: float = 0.0
        self.scratch_dir: Optional[Path] = None

    def clone(self) -> None:
        """Create the ephemeral instant clone and measure clone duration."""
        if self.is_active:
            return

        t0 = time.time()
        if self.backend == "mock":
            self.scratch_dir = Path(tempfile.mkdtemp(prefix=f"vm_clone_{self.clone_name}_"))
            # Populate mock VM guest structure
            (self.scratch_dir / "guest_root").mkdir(parents=True, exist_ok=True)
            (self.scratch_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            self.is_active = True
            self.duration = time.time() - t0
            _ACTIVE_HARNESSES.add(self)
            return

        # Real govc execution path
        cmd = [self.govc_bin, "vm.clone"]
        if self.snapshot:
            cmd.extend(["-snapshot", self.snapshot])
        cmd.extend(["-vm", self.template, self.clone_name])

        env = _govc_env()

        try:
            res = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if res.returncode != 0:
                raise VMCloneError(
                    f"govc vm.clone failed (rc={res.returncode}): {res.stderr.strip()}"
                )
            self.is_active = True
            self.duration = time.time() - t0
            _ACTIVE_HARNESSES.add(self)
        except subprocess.TimeoutExpired as exc:
            raise VMCloneError(f"govc vm.clone timed out after {self.timeout}s") from exc

    def run_command(self, cmd: str, timeout: Optional[float] = None) -> CommandResult:
        """Execute a command inside the cloned VM."""
        if not self.is_active or self.is_destroyed:
            raise VMCloneError("Cannot run command on an inactive or destroyed VM clone.")

        cmd_timeout = timeout or self.timeout
        t0 = time.time()

        if self.backend == "mock":
            # Run command hermetically inside mock guest root
            guest_dir = self.scratch_dir / "guest_root"
            try:
                res = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=str(guest_dir),
                    capture_output=True,
                    text=True,
                    timeout=cmd_timeout,
                )
                dur = time.time() - t0
                return CommandResult(
                    command=cmd,
                    stdout=res.stdout,
                    stderr=res.stderr,
                    returncode=res.returncode,
                    duration=dur,
                )
            except subprocess.TimeoutExpired as exc:
                raise VMCommandTimeoutError(
                    f"Command '{cmd}' timed out after {cmd_timeout}s"
                ) from exc

        # For live ESXi VM, govc guest command or ssh
        run_cmd = [self.govc_bin, "guest.run", "-vm", self.clone_name, "--"] + shlex.split(cmd)
        env = os.environ.copy()
        try:
            res = subprocess.run(
                run_cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=cmd_timeout,
            )
            dur = time.time() - t0
            return CommandResult(
                command=cmd,
                stdout=res.stdout,
                stderr=res.stderr,
                returncode=res.returncode,
                duration=dur,
            )
        except subprocess.TimeoutExpired as exc:
            raise VMCommandTimeoutError(
                f"Remote guest command '{cmd}' timed out after {cmd_timeout}s"
            ) from exc

    def retrieve_artifacts(self, remote_path: str, local_destination: str) -> List[str]:
        """Retrieve files or directories from the VM to a safe local path."""
        if not self.is_active or self.is_destroyed:
            raise VMCloneError("Cannot retrieve artifacts from an inactive or destroyed VM clone.")

        dest = Path(local_destination).resolve()
        dest.mkdir(parents=True, exist_ok=True)
        retrieved: List[str] = []

        if self.backend == "mock":
            # Retrieve from mock guest root
            guest_root = (self.scratch_dir / "guest_root").resolve()
            src_full = (guest_root / remote_path.lstrip("/")).resolve()
            # row860 fixer (E3): the mock guest is confined to guest_root --
            # a traversal ("../../etc/hostname") must not read the host.
            if src_full != guest_root and guest_root not in src_full.parents:
                raise SecurityViolationError(
                    f"artifact path {remote_path!r} escapes the mock guest root"
                )
            if not src_full.exists():
                return []
            if src_full.is_file():
                target = dest / src_full.name
                shutil.copy2(src_full, target)
                retrieved.append(str(target))
            elif src_full.is_dir():
                for item in src_full.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(src_full)
                        target = dest / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, target)
                        retrieved.append(str(target))
            return retrieved

        # Live govc guest.download or scp
        dl_cmd = [
            self.govc_bin,
            "guest.download",
            "-vm",
            self.clone_name,
            remote_path,
            str(dest),
        ]
        res = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=self.timeout)
        if res.returncode == 0:
            for root, _, files in os.walk(str(dest)):
                for f in files:
                    retrieved.append(os.path.join(root, f))
        return retrieved

    def destroy(self) -> bool:
        """Tear down the ephemeral instant clone, guaranteeing resource cleanup."""
        if self.is_destroyed:
            return True

        success = True
        try:
            if self.backend == "mock" and self.scratch_dir and self.scratch_dir.exists():
                shutil.rmtree(self.scratch_dir, ignore_errors=True)
            elif self.backend == "govc" and self.is_active:
                cmd = [self.govc_bin, "vm.destroy", self.clone_name]
                env = os.environ.copy()
                res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=self.timeout)
                if res.returncode != 0:
                    success = False
        except Exception:
            success = False
        finally:
            self.is_active = False
            self.is_destroyed = True
            _ACTIVE_HARNESSES.discard(self)

        return success

    def __enter__(self) -> "ESXiVMCloneHarness":
        self.clone()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        self.destroy()
        return False
