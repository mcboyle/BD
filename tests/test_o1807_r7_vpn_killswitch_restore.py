"""O1807 R7 -- vpn_kill_switch_probe.py precondition + restore regressions.

#16: check_ufw tested `"active" in first_line`, so `Status: inactive`
     passed the "ufw is active" precondition.
#17: restore_ufw_state ran `ufw --force reset` and replayed only
     `ufw show added`, which carries no default policies, so a host
     whose default outgoing policy was `deny` came back as `allow`
     while the restore reported success. A capture with no Default:
     line must refuse --apply before any firewall change.

ufw is faked via shutil.which / subprocess.run; no firewall is touched.
"""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

_SCRIPT_PATH = (Path(__file__).resolve().parent.parent
                / "tools" / "vpn_kill_switch_probe.py")

_VERBOSE = ("Status: active\nLogging: on (low)\n"
            "Default: deny (incoming), deny (outgoing), disabled (routed)\n"
            "New profiles: skip\n")
# Differs per direction and from the kill switch's own `deny outgoing`.
_VERBOSE_MIXED = ("Status: active\nLogging: on (low)\n"
                  "Default: allow (incoming), reject (outgoing), "
                  "allow (routed)\nNew profiles: skip\n")
_ADDED = ("Added user rules (see 'ufw status' for running firewall):\n"
          "ufw allow 22/tcp\n")


class _FakeRun:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _import_module():
    spec = importlib.util.spec_from_file_location(
        "vpn_kill_switch_probe_o1807_r7", _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check_ufw(monkeypatch, first_line: str) -> bool:
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ufw")
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda args, **kw: _FakeRun(first_line + "\n"))
    return mod.check_ufw()[0]


def test_check_ufw_accepts_active(monkeypatch):
    assert _check_ufw(monkeypatch, "Status: active") is True


def test_check_ufw_refuses_inactive(monkeypatch):
    assert _check_ufw(monkeypatch, "Status: inactive") is False


def _snapshot_then_restore(monkeypatch, verbose: str, default_rc: int = 0):
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ufw")
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args[1:]))
        if args[1:] == ["show", "added"]:
            return _FakeRun(_ADDED)
        if args[1:] == ["status", "verbose"]:
            return _FakeRun(verbose)
        if args[1] == "default":
            return _FakeRun(returncode=default_rc)
        return _FakeRun("Status: active\n")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    snap = mod.snapshot_ufw_state()
    calls.clear()
    ok, msg = mod.restore_ufw_state(snap)
    return calls, ok, msg


def test_restore_reapplies_default_policies(monkeypatch):
    calls, ok, msg = _snapshot_then_restore(monkeypatch, _VERBOSE)
    assert ok is True, msg
    assert ["allow", "22/tcp"] in calls
    defaults = [c for c in calls if c[:1] == ["default"]]
    assert len(defaults) == 2, calls
    assert ["default", "deny", "incoming"] in defaults
    assert ["default", "deny", "outgoing"] in defaults
    # Policies land before ufw is re-enabled.
    assert calls.index(["default", "deny", "outgoing"]) < calls.index(
        ["--force", "enable"])


def test_restore_without_captured_defaults_reports_failure(monkeypatch):
    calls, ok, msg = _snapshot_then_restore(monkeypatch, "Status: active\n")
    assert ok is False
    assert "default policies missing from snapshot" in msg
    assert ["allow", "22/tcp"] in calls
    assert calls[-1] == ["--force", "enable"]


def test_restore_reapplies_exact_captured_defaults(monkeypatch):
    calls, ok, msg = _snapshot_then_restore(monkeypatch, _VERBOSE_MIXED)
    assert ok is True, msg
    assert [c for c in calls if c[:1] == ["default"]] == [
        ["default", "allow", "incoming"],
        ["default", "reject", "outgoing"],
        ["default", "allow", "routed"],
    ]


def test_restore_reports_failed_default(monkeypatch):
    calls, ok, msg = _snapshot_then_restore(monkeypatch, _VERBOSE_MIXED,
                                            default_rc=1)
    assert ok is False
    assert "default allow incoming: exit=1" in msg


_SNAPSHOT_READS = [["status", "numbered"], ["status", "verbose"],
                   ["show", "added"], ["show", "listening"]]


def _run_apply(monkeypatch, tmp_path, verbose: str, answer: str):
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ufw")
    monkeypatch.setattr(mod, "check_root", lambda: (True, "root"))
    monkeypatch.setattr(mod, "check_ufw", lambda: (True, "Status: active"))
    monkeypatch.setattr(mod, "detect_vpn_interface",
                        lambda: ("wg0", "wireguard", "wg0 up"))
    monkeypatch.setattr(mod, "check_target_reachable",
                        lambda url: (True, "200"))
    monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args[1:]))
        if args[1:] == ["status", "verbose"]:
            return _FakeRun(verbose)
        if args[1:] == ["show", "added"]:
            return _FakeRun(_ADDED)
        return _FakeRun("")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    rc = mod.run_apply(
        argparse.Namespace(reason="o1807 r7", target="https://x.test"),
        tmp_path)
    log = tmp_path.joinpath(*mod._LOG_REL).read_text(encoding="utf-8")
    return rc, calls, log


@pytest.mark.parametrize("verbose", [
    "Status: inactive\n",
    "Status: active\nDefault: deny (incoming), disabled (routed)\n",
    "Status: active\nDefault: allow (outgoing), disabled (routed)\n",
])
def test_apply_refuses_snapshot_without_defaults(monkeypatch, tmp_path,
                                                 verbose):
    rc, calls, log = _run_apply(monkeypatch, tmp_path, verbose, "y")
    assert rc == 2
    # Only the snapshot's reads ran: no rule, default, reset or enable.
    assert calls == _SNAPSHOT_READS
    assert "SNAPSHOT_ERR" in log
    assert "default policies missing from snapshot" in log


@pytest.mark.parametrize("verbose, defaults", [
    (_VERBOSE_MIXED, [["default", "allow", "incoming"],
                      ["default", "reject", "outgoing"],
                      ["default", "allow", "routed"]]),
    ("Status: active\nLogging: on (low)\n"
     "Default: deny (incoming), allow (outgoing), disabled (routed)\n"
     "New profiles: skip\n",
     [["default", "deny", "incoming"], ["default", "allow", "outgoing"]]),
    (_VERBOSE, [["default", "deny", "incoming"],
                ["default", "deny", "outgoing"]]),
    ("Status: active\n"
     "Default: reject (incoming), allow (outgoing), deny (routed)\n",
     [["default", "reject", "incoming"], ["default", "allow", "outgoing"],
      ["default", "deny", "routed"]]),
    # No routed policy at all: only incoming + outgoing are required.
    ("Status: active\nDefault: deny (incoming), allow (outgoing)\n",
     [["default", "deny", "incoming"], ["default", "allow", "outgoing"]]),
], ids=["mixed", "ubuntu-default", "all-deny", "reject-incoming",
        "no-routed"])
def test_apply_accepts_snapshot_with_defaults(monkeypatch, tmp_path,
                                              verbose, defaults):
    # Operator declines step 1: the gate passed, nothing was applied,
    # and restore re-applies exactly the captured policies.
    rc, calls, log = _run_apply(monkeypatch, tmp_path, verbose, "n")
    assert rc == 0, log
    assert "SNAPSHOT_ERR" not in log
    assert "PROBE_END result=operator-aborted" in log
    assert calls == [*_SNAPSHOT_READS, ["--force", "reset"],
                     ["allow", "22/tcp"], *defaults, ["--force", "enable"]]
