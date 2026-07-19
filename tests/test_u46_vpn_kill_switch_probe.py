"""U46 — vpn_kill_switch_probe.py contract tests.

T46 ships an active VPN kill-switch probe at tools/vpn_kill_switch_probe.py.
It mirrors the T51 regenerate_goldens.py shape: dry-run by default;
--apply requires --reason; mutating-or-state-touching modes have hard
preconditions (root, ufw present, exactly one VPN interface up); and
an append-only audit log captures every invocation.

These tests pin the contract that *doesn't* need root or a real VPN
to verify:

  * The script imports cleanly (no module-level side effects).
  * The CLI exists and has the documented mode flags.
  * Mode flags are mutually exclusive.
  * --apply without --reason refuses (exit 1, not 2).
  * Precondition guards exist as source-grep contracts (the actual
    refusal behaviour is OS-coupled and can't be exercised here).
  * find_repo_root works against the real repo.
  * append_audit_log writes to logs/vpn_kill_switch_probe.log.
  * The kill-switch plan is exactly three ufw commands and includes
    a loopback allow + a VPN-interface allow + a default-deny.
  * detect_vpn_interface handles all four cases (wireguard up,
    openvpn up, both up = ambiguous, neither up) via injected
    fixtures — the function itself shells out to `ip(8)`, so we
    monkey-patch shutil.which / subprocess.run.

Functional verification of --apply (real ufw mutation against a real
VPN) is operator-side on stash. The dry-run path provides the
in-suite confidence that the precondition + audit + plan plumbing is
right before the operator ever flips --apply.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "tools" / "vpn_kill_switch_probe.py"


def _import_module():
    """Import the script as a module so we can call its functions
    directly. The script is designed to be importable — `main()` is
    only called under `if __name__ == "__main__"`.
    """
    spec = importlib.util.spec_from_file_location(
        "vpn_kill_switch_probe", _SCRIPT_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Layout + import-clean ────────────────────────────────────────────


def test_script_exists_at_tools_path():
    assert _SCRIPT_PATH.is_file(), (
        f"tools/vpn_kill_switch_probe.py must exist at {_SCRIPT_PATH}"
    )


def test_script_is_executable_shebang():
    """First line is a Python shebang so `chmod +x; ./tools/...` works."""
    first = _SCRIPT_PATH.read_text(encoding="utf-8").splitlines()[0]
    assert first.startswith("#!"), f"missing shebang: {first!r}"
    assert "python" in first, f"shebang is not python: {first!r}"


def test_module_imports_cleanly():
    """No side effects at import. Importing must not run subprocesses,
    open audit log, or call ufw — only define functions.
    """
    mod = _import_module()
    # The module should expose its public surface as top-level names.
    for name in (
        "find_repo_root", "now_iso", "check_root", "check_ufw",
        "detect_vpn_interface", "check_target_reachable",
        "snapshot_ufw_state", "plan_kill_switch_rules",
        "apply_rule", "restore_ufw_state", "append_audit_log",
        "build_parser", "main",
    ):
        assert hasattr(mod, name), f"missing public name: {name}"


# ── Repo-root discovery ──────────────────────────────────────────────


def test_find_repo_root_finds_real_repo():
    mod = _import_module()
    found = mod.find_repo_root(_SCRIPT_PATH.parent)
    assert (found / "CHANGELOG.md").is_file()
    assert (found / "bulk_downloader").is_dir()


# ── CLI shape ────────────────────────────────────────────────────────


def test_cli_help_runs_clean():
    """`--help` exits 0 and does not require root/ufw."""
    r = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), "--help"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert r.returncode == 0, f"--help failed: {r.stderr}"
    # Confirm the documented flags are in the help text.
    for flag in ("--apply", "--reason", "--target", "--snapshot",
                 "--restore"):
        assert flag in r.stdout, f"--help missing {flag}: {r.stdout}"


def test_cli_apply_without_reason_exits_one():
    """The T51 contract pattern: --apply with empty --reason → exit 1."""
    mod = _import_module()
    rc = mod.main(["--apply"])
    assert rc == 1, f"expected exit 1 for missing --reason, got {rc}"


def test_cli_apply_with_blank_reason_exits_one():
    """Whitespace-only --reason is treated as missing."""
    mod = _import_module()
    rc = mod.main(["--apply", "--reason", "   "])
    assert rc == 1


def test_cli_modes_are_mutually_exclusive():
    """--apply + --snapshot together = exit 1 (usage error)."""
    mod = _import_module()
    rc = mod.main(["--apply", "--reason", "x", "--snapshot", "/tmp/x.json"])
    assert rc == 1, f"expected exit 1 for combined modes, got {rc}"

    rc = mod.main(["--snapshot", "/tmp/x.json", "--restore", "/tmp/y.json"])
    assert rc == 1


# ── Precondition guards exist as source-grep contracts ──────────────


def test_root_check_function_exists():
    mod = _import_module()
    assert callable(mod.check_root)
    # A non-root caller in the sandbox should refuse via check_root.
    ok, why = mod.check_root()
    if os.name != "posix":
        assert not ok and "POSIX" in why
    else:
        # In the test sandbox we are typically not root.
        # On a real deployment with `sudo`, this path passes.
        # Either way, the function returns (bool, str) — that's the contract.
        assert isinstance(ok, bool)
        assert isinstance(why, str)


def test_ufw_check_function_exists():
    mod = _import_module()
    assert callable(mod.check_ufw)
    ok, why = mod.check_ufw()
    assert isinstance(ok, bool)
    assert isinstance(why, str)


def test_target_check_function_exists():
    mod = _import_module()
    assert callable(mod.check_target_reachable)
    # Probing example.invalid (TLD reserved by RFC) should fail.
    ok, why = mod.check_target_reachable("https://example.invalid/")
    assert ok is False
    assert isinstance(why, str)


# ── detect_vpn_interface — mock the ip(8) shell-out ─────────────────


def _fake_ip_link_output(*entries: str) -> str:
    """Build a fake `ip -o link show` body from entries like
    'wg0:UP' or 'tun0:DOWN'.
    """
    lines = []
    for i, entry in enumerate(entries, start=2):
        name, flags = entry.split(":", 1)
        lines.append(
            f"{i}: {name}: <BROADCAST,MULTICAST,{flags}> mtu 1500 qdisc noqueue"
        )
    # Always include lo
    lines.insert(0, "1: lo: <LOOPBACK,UP,LOWER_UP> mtu 65536")
    return "\n".join(lines)


class _FakeRun:
    """Stand-in for subprocess.run that returns a canned ip-link output."""
    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = ""

    def __call__(self, *args, **kwargs):
        return self


def test_detect_vpn_wireguard_up(monkeypatch):
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        mod.subprocess, "run", _FakeRun(_fake_ip_link_output("wg0:UP")),
    )
    iface, kind, msg = mod.detect_vpn_interface()
    assert iface == "wg0"
    assert kind == "wireguard"
    assert "wireguard" in msg.lower()


def test_detect_vpn_openvpn_up(monkeypatch):
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        mod.subprocess, "run", _FakeRun(_fake_ip_link_output("tun0:UP")),
    )
    iface, kind, msg = mod.detect_vpn_interface()
    assert iface == "tun0"
    assert kind == "openvpn"
    assert "openvpn" in msg.lower()


def test_detect_vpn_both_up_is_ambiguous(monkeypatch):
    """Multi-provider host that has both backends up at once is
    ambiguous — the probe refuses to guess.
    """
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        mod.subprocess, "run",
        _FakeRun(_fake_ip_link_output("wg0:UP", "tun0:UP")),
    )
    iface, kind, msg = mod.detect_vpn_interface()
    assert iface is None
    assert kind is None
    assert "ambiguous" in msg.lower()


def test_detect_vpn_neither_up(monkeypatch):
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        mod.subprocess, "run",
        _FakeRun(_fake_ip_link_output("eth0:UP")),
    )
    iface, kind, msg = mod.detect_vpn_interface()
    assert iface is None
    assert kind is None
    assert "no vpn" in msg.lower() or "no usable" in msg.lower() or "no wg" in msg.lower()


def test_detect_vpn_down_interface_not_counted(monkeypatch):
    """A wg0 that exists but is DOWN must not be reported as up."""
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ip")
    monkeypatch.setattr(
        mod.subprocess, "run",
        _FakeRun(_fake_ip_link_output("wg0:DOWN")),
    )
    iface, _, _ = mod.detect_vpn_interface()
    assert iface is None


# ── Kill-switch plan ────────────────────────────────────────────────


def test_plan_kill_switch_is_three_commands():
    mod = _import_module()
    plan = mod.plan_kill_switch_rules("wg0")
    assert len(plan) == 3, f"expected 3 rules, got {len(plan)}: {plan}"


def test_plan_kill_switch_includes_loopback_allow():
    mod = _import_module()
    plan = mod.plan_kill_switch_rules("wg0")
    assert any("lo" in cmd for cmd in plan), (
        f"kill switch must allow loopback: {plan}"
    )


def test_plan_kill_switch_includes_vpn_iface_allow():
    mod = _import_module()
    plan = mod.plan_kill_switch_rules("wg0")
    assert any("wg0" in cmd for cmd in plan), (
        f"kill switch must allow VPN iface: {plan}"
    )
    plan2 = mod.plan_kill_switch_rules("tun0")
    assert any("tun0" in cmd for cmd in plan2)


def test_plan_kill_switch_includes_default_deny_outgoing():
    mod = _import_module()
    plan = mod.plan_kill_switch_rules("wg0")
    assert any("deny" in cmd and "outgoing" in cmd for cmd in plan), (
        f"kill switch must default-deny outgoing: {plan}"
    )


# ── Audit log ────────────────────────────────────────────────────────


def test_append_audit_log_creates_and_appends(tmp_path):
    mod = _import_module()
    mod.append_audit_log(tmp_path, "first line")
    mod.append_audit_log(tmp_path, "second line")
    log = tmp_path / "logs" / "vpn_kill_switch_probe.log"
    assert log.is_file()
    body = log.read_text(encoding="utf-8")
    assert "first line" in body
    assert "second line" in body
    assert body.count("\n") == 2


def test_now_iso_is_utc_z():
    """Audit-log timestamps are UTC with Z suffix — matches T51 / dev-tools
    convention. Mixing local time into a log is the pattern A2 warns
    about.
    """
    mod = _import_module()
    s = mod.now_iso()
    assert s.endswith("Z"), f"timestamp not UTC: {s}"
    # Parses as ISO 8601
    import datetime as dt
    parsed = dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
    assert parsed.year >= 2026


# ── Snapshot / restore (round-trip via fake ufw) ────────────────────


def test_snapshot_round_trip_via_fake_ufw(tmp_path, monkeypatch):
    """snapshot_ufw_state captures ufw output into a dict; the dict
    is JSON-able (so the --snapshot mode can write it to disk).
    """
    mod = _import_module()
    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/sbin/ufw")

    captured_args = []

    def fake_run(args, **kwargs):
        captured_args.append(args)
        # ufw status numbered / show added / show listening
        if "status" in args:
            return _FakeRun("Status: active\n\n     To        Action  From\n"
                            "[ 1] 22/tcp    ALLOW   Anywhere\n")
        if "added" in args:
            return _FakeRun("Added user rules (see 'ufw status' for "
                            "running firewall):\nufw allow 22/tcp\n")
        if "listening" in args:
            return _FakeRun("tcp:\n  22 * (sshd)\n    [ 1] allow 22/tcp\n")
        return _FakeRun("", returncode=1)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    snap = mod.snapshot_ufw_state()
    assert "status_numbered" in snap
    assert "show_added" in snap
    assert "show_listening" in snap
    assert "captured_at" in snap

    # JSON-roundtrip — the dict must be serialisable.
    blob = json.dumps(snap)
    restored = json.loads(blob)
    assert restored["show_added"] == snap["show_added"]


# ── Off-network ─────────────────────────────────────────────────────


def test_tool_is_off_network():
    """No network imports in the module — keeps the in-suite tests
    runnable in the sandbox. The only network access is via curl
    inside check_target_reachable, which is only invoked from the
    CLI paths that the suite doesn't exercise.
    """
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in ("import requests", "import httpx",
                      "import urllib3", "from urllib"):
        assert forbidden not in src, f"{forbidden} should not be imported"
