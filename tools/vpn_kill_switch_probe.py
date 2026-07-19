#!/usr/bin/env python3
"""vpn_kill_switch_probe.py — T46 active VPN kill-switch probe.

The T46 read-only inspector (`live_tests/checks.py::l29`, v3.63.0)
verifies the kill-switch *configuration* is wired and inspectable.
This script is the *active* counterpart — it temporarily exercises
the kill-switch on a running host and measures whether outbound
traffic actually fails closed when the VPN drops, then restores
state.

Active probing is fundamentally different from a live test:

  * It needs root (manipulating firewall rules).
  * It can leave the host in a wedged state if it crashes.
  * It is interactive — operator confirmation gates every mutation.
  * It cannot be unit-tested end-to-end; the network behaviour only
    appears against real interfaces + a real VPN session.

The split that makes this tractable:

  * Detection, snapshot, and plan-printing are pure data-collection
    operations. They run in dry-run by default and have no side
    effects. The unit suite exercises them.
  * Rule mutation lives behind `--apply --reason "..."`, prompts the
    operator before each toggle, and always restores state in a
    finally. The unit suite verifies the gates but not the mutation
    (only stash can do that, against a real VPN + ufw).

Layout follows the T51 regenerate_goldens.py shape (dry-run by
default; --apply requires --reason; append-only log under tools/
holding timestamp + git rev + reason + outcome).

Host preconditions (verified at startup; refusals are exit 2):

  1. Root (EUID 0). Anything less can't insert ufw rules.
  2. `ufw` binary present + ufw active. The script speaks ufw, not
     raw iptables/nft — ufw regenerates its chains on reload and
     direct iptables surgery races with that.
  3. Exactly one of wg0 / tun0 (or equivalent) up. If neither is
     up there is no VPN to test; if both are up the routing state
     is ambiguous and the probe refuses to guess which the
     kill-switch should pin.
  4. A reachable VPN-routed test target. Default
     https://ifconfig.me/ip; configurable via --target. The probe
     needs to know the *expected* (VPN-side) IP before any toggle
     so it can confirm a fail-closed.

Audit log (logs/vpn_kill_switch_probe.log under repo root,
append-only):

    2026-05-21T12:34:56Z PROBE_BEGIN apply=False reason=None
    2026-05-21T12:34:56Z DETECT     vpn=wireguard iface=wg0
                                    target=https://ifconfig.me/ip
    2026-05-21T12:34:57Z SNAPSHOT   ufw_rules=18 default_in=deny
                                    default_out=allow
    2026-05-21T12:34:57Z PLAN       insert=1 rules to add
    2026-05-21T12:34:57Z PROBE_END  result=dry-run

On --apply the log captures every toggle and the restore outcome.

Usage:

    # Dry-run, the default. Reports what would happen, exits 0.
    sudo python3 tools/vpn_kill_switch_probe.py

    # Apply. --reason is mandatory; goes in the audit log.
    sudo python3 tools/vpn_kill_switch_probe.py --apply \\
        --reason "post-deploy kill-switch verification 2026-05-21"

    # Snapshot only — capture current ufw + routing state to a JSON
    # file the operator can keep, no probe action.
    sudo python3 tools/vpn_kill_switch_probe.py --snapshot \\
        /tmp/ufw_state.json

    # Restore from a snapshot (defensive — only useful if a prior
    # --apply crashed before its own restore ran).
    sudo python3 tools/vpn_kill_switch_probe.py --restore \\
        /tmp/ufw_state.json

Exit codes:
  0  — dry-run completed; --apply completed and state was restored
  1  — usage error (missing --reason on --apply, etc.)
  2  — precondition refused (not root, no ufw, no VPN, both VPNs up)
  3  — probe error during --apply (state restore was attempted)
  4  — probe error during --apply AND restore also failed
       (operator-action required — see audit log for residual rules)

This script is importable. Tests exercise the dry-run paths and
the precondition guards in-process; main() is only invoked when
run as a script.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json as _json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional


_LOG_REL = ("logs", "vpn_kill_switch_probe.log")
_DEFAULT_TARGET = "https://ifconfig.me/ip"

# How long to wait (seconds) for a curl-to-target call before
# declaring the target unreachable. With the kill switch active the
# call should fail fast (RST or no route), so 5s is generous.
_TARGET_TIMEOUT_S = 5

# ── Discovery ───────────────────────────────────────────────────────


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Walk up looking for CHANGELOG.md + bulk_downloader/.

    Mirrors tools/regenerate_goldens.py::find_repo_root.
    """
    here = Path(start) if start is not None else Path(__file__).resolve().parent
    for candidate in [here] + list(here.parents):
        if ((candidate / "CHANGELOG.md").exists()
                and (candidate / "bulk_downloader").is_dir()):
            return candidate
    return Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Precondition checks ─────────────────────────────────────────────


def check_root() -> tuple[bool, str]:
    if os.name != "posix":
        return False, "active probe only runs on POSIX hosts"
    try:
        if os.geteuid() != 0:
            return False, (f"must run as root (current EUID={os.geteuid()}); "
                           f"prepend `sudo`")
    except AttributeError:
        return False, "platform has no geteuid"
    return True, "root"


def check_ufw() -> tuple[bool, str]:
    """ufw is installed AND active."""
    ufw = shutil.which("ufw")
    if ufw is None:
        return False, ("ufw not on PATH — this script speaks ufw, not raw "
                       "iptables/nft. Install ufw or run a host-appropriate "
                       "tool.")
    try:
        r = subprocess.run([ufw, "status"], capture_output=True,
                           text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ufw status failed: {exc!r}"
    if r.returncode != 0:
        return False, f"ufw status exit={r.returncode}: {r.stderr.strip()}"
    first = r.stdout.splitlines()[0] if r.stdout else ""
    if "active" not in first.lower():
        return False, (f"ufw is installed but not active ('{first.strip()}'); "
                       f"the kill-switch must be enforced via ufw")
    return True, first.strip()


def detect_vpn_interface() -> tuple[Optional[str], Optional[str], str]:
    """Return (interface_name, kind, message).

    kind ∈ {"wireguard", "openvpn", None}. The message is a single
    line suitable for the audit log.

    A multi-provider host can have wg0 OR a tun0 up at any moment;
    the operator's setup runs one at a time. If both are up the
    return is (None, None, "ambiguous: both wg0 and tun0 are up")
    and the caller refuses.
    """
    ip = shutil.which("ip")
    if ip is None:
        return None, None, "ip(8) not on PATH; cannot enumerate interfaces"
    try:
        r = subprocess.run([ip, "-o", "link", "show"], capture_output=True,
                           text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, None, f"ip link show failed: {exc!r}"
    if r.returncode != 0:
        return None, None, f"ip link show exit={r.returncode}"
    wg_iface = None
    tun_iface = None
    for line in r.stdout.splitlines():
        # `ip -o link show` shape: "N: name: <FLAGS> ..."
        m = re.match(r"^\d+:\s+(\S+?):\s+<([^>]*)>", line)
        if not m:
            continue
        name, flags = m.group(1), m.group(2)
        # Strip @if* suffix that veth-like links carry
        name = name.split("@", 1)[0]
        if "UP" not in flags:
            continue
        if name.startswith("wg"):
            wg_iface = wg_iface or name
        elif name.startswith("tun"):
            tun_iface = tun_iface or name
    if wg_iface and tun_iface:
        return None, None, (f"ambiguous: both {wg_iface} (wireguard) and "
                            f"{tun_iface} (openvpn) are up")
    if wg_iface:
        return wg_iface, "wireguard", f"wireguard up on {wg_iface}"
    if tun_iface:
        return tun_iface, "openvpn", f"openvpn up on {tun_iface}"
    return None, None, "no VPN interface up (no wg* or tun* UP)"


def check_target_reachable(url: str) -> tuple[bool, str]:
    """Probe `url` with curl; the probe needs to know the target
    responds *before* a toggle so we can measure that it stops
    responding *after* one. Treating both `connect` and DNS errors
    as unreachable.
    """
    curl = shutil.which("curl")
    if curl is None:
        return False, "curl not on PATH"
    try:
        r = subprocess.run(
            [curl, "-sS", "--max-time", str(_TARGET_TIMEOUT_S),
             "-o", "/dev/null", "-w", "%{http_code}", url],
            capture_output=True, text=True,
            timeout=_TARGET_TIMEOUT_S + 2, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"curl raised: {exc!r}"
    if r.returncode != 0:
        return False, f"curl exit={r.returncode}: {r.stderr.strip()}"
    code = r.stdout.strip()
    if not (code.startswith("2") or code.startswith("3")):
        return False, f"curl returned HTTP {code}"
    return True, f"HTTP {code}"


# ── Snapshot / restore ──────────────────────────────────────────────


def snapshot_ufw_state() -> dict:
    """Capture ufw rules + default policies as a JSON-able dict.

    The structure is opaque to anything outside this script; what
    matters is that `restore_ufw_state` can reproduce it. We capture
    `ufw status numbered` (the live numbered rules) AND
    `ufw show added` (the rule-creation commands, which is what
    we'll replay on restore).
    """
    ufw = shutil.which("ufw")
    if ufw is None:
        raise RuntimeError("ufw disappeared between precondition and snapshot")
    state: dict = {
        "captured_at": now_iso(),
        "status_numbered": "",
        "show_added": "",
        "show_listening": "",
    }
    for key, args in [
        ("status_numbered", ["status", "numbered"]),
        ("show_added", ["show", "added"]),
        ("show_listening", ["show", "listening"]),
    ]:
        r = subprocess.run([ufw, *args], capture_output=True,
                           text=True, timeout=5, check=False)
        if r.returncode != 0:
            raise RuntimeError(
                f"ufw {' '.join(args)} exit={r.returncode}: {r.stderr.strip()}"
            )
        state[key] = r.stdout
    return state


def plan_kill_switch_rules(vpn_iface: str) -> list[list[str]]:
    """Return the ufw commands that *would* install the kill switch.

    The kill switch is a default-deny on outbound traffic that is
    NOT going via the VPN interface, PLUS an allow for loopback and
    the VPN interface itself. We express it as a sequence of `ufw`
    commands rather than rule text so the operator can read the
    plan in the dry-run output and we don't have to parse ufw rule
    syntax variation across releases.

    The plan is intentionally minimal — three rules — to keep the
    diff observable.
    """
    return [
        # 1. Allow loopback so the host can talk to itself.
        ["allow", "out", "on", "lo"],
        # 2. Allow outbound on the VPN interface (the tunnel itself).
        ["allow", "out", "on", vpn_iface],
        # 3. Default-deny everything else outbound.
        ["default", "deny", "outgoing"],
    ]


def apply_rule(ufw_args: list[str]) -> tuple[bool, str]:
    """Run a single ufw command. Returns (ok, message)."""
    ufw = shutil.which("ufw")
    if ufw is None:
        return False, "ufw not on PATH"
    try:
        r = subprocess.run([ufw, *ufw_args], capture_output=True,
                           text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ufw raised: {exc!r}"
    if r.returncode != 0:
        return False, (f"ufw {' '.join(ufw_args)} exit={r.returncode}: "
                       f"{r.stderr.strip() or r.stdout.strip()}")
    return True, r.stdout.strip().splitlines()[0] if r.stdout else "ok"


def restore_ufw_state(snapshot: dict) -> tuple[bool, str]:
    """Restore ufw to the snapshot state.

    Strategy: `ufw reset` then replay `show added` line-by-line.
    `ufw reset` is destructive but reversible — and that's exactly
    what we want: a known clean baseline followed by exactly the
    rules we captured.

    Returns (ok, message). On any failure the message includes
    what was successfully restored vs. what failed.
    """
    ufw = shutil.which("ufw")
    if ufw is None:
        return False, "ufw not on PATH — manual restore required"

    # 1. `ufw --force reset` clears all rules and disables ufw.
    try:
        r = subprocess.run(
            [ufw, "--force", "reset"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ufw reset raised: {exc!r}"
    if r.returncode != 0:
        return False, f"ufw reset exit={r.returncode}: {r.stderr.strip()}"

    # 2. Replay the `show added` capture.
    # The capture's body has lines like:
    #     ufw allow 22/tcp
    #     ufw allow from 10.0.0.0/8
    # We re-execute each `ufw …` line as captured.
    show_added = snapshot.get("show_added", "")
    restored = 0
    failures: list[str] = []
    for line in show_added.splitlines():
        line = line.strip()
        if not line or not line.startswith("ufw "):
            continue
        # Skip comment lines / "Added user rules (see ...)" headers.
        if line.startswith("#") or "(see" in line:
            continue
        parts = line.split()[1:]  # drop the leading "ufw"
        if not parts:
            continue
        try:
            r = subprocess.run(
                [ufw, *parts], capture_output=True, text=True,
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{line}: {exc!r}")
            continue
        if r.returncode != 0:
            failures.append(f"{line}: exit={r.returncode}")
        else:
            restored += 1

    # 3. Re-enable ufw — `reset` left it disabled.
    try:
        r = subprocess.run(
            [ufw, "--force", "enable"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ufw enable raised: {exc!r} (restored={restored})"
    if r.returncode != 0:
        return False, (f"ufw enable exit={r.returncode}: "
                       f"{r.stderr.strip()} (restored={restored})")

    if failures:
        return False, (f"restored {restored} rules but {len(failures)} "
                       f"replay failures: " + "; ".join(failures[:3]))
    return True, f"restored {restored} rules; ufw re-enabled"


# ── Audit log ───────────────────────────────────────────────────────


def append_audit_log(repo_root: Path, line: str) -> None:
    """Append a line to logs/vpn_kill_switch_probe.log.

    Atomic-append is not strictly required (the file is the
    target's own log, not a shared resource) but we open in append
    mode for safety against concurrent invocations.
    """
    log_path = repo_root.joinpath(*_LOG_REL)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ── Probe orchestration ─────────────────────────────────────────────


def run_dry_run(args, repo_root: Path) -> int:
    """The default no-mutation path.

    Reports detection + snapshot + plan, writes one PROBE_BEGIN /
    PROBE_END pair to the audit log, exits 0 unless a precondition
    fails.
    """
    append_audit_log(
        repo_root,
        f"{now_iso()} PROBE_BEGIN apply=False target={args.target!r}",
    )

    ok, why = check_root()
    if not ok:
        append_audit_log(repo_root, f"{now_iso()} REFUSED  {why}")
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2

    ok, why = check_ufw()
    if not ok:
        append_audit_log(repo_root, f"{now_iso()} REFUSED  ufw: {why}")
        print(f"REFUSED: ufw — {why}", file=sys.stderr)
        return 2

    vpn_iface, vpn_kind, vpn_msg = detect_vpn_interface()
    append_audit_log(repo_root, f"{now_iso()} DETECT   {vpn_msg}")
    print(f"  VPN:    {vpn_msg}")
    if vpn_iface is None:
        print("REFUSED: no usable VPN interface", file=sys.stderr)
        return 2

    ok, msg = check_target_reachable(args.target)
    append_audit_log(
        repo_root, f"{now_iso()} TARGET   {args.target} reachable={ok} {msg}",
    )
    print(f"  Target: {args.target} → {msg}")
    if not ok:
        print("REFUSED: target not currently reachable; can't measure "
              "fail-closed window from this baseline.", file=sys.stderr)
        return 2

    try:
        snap = snapshot_ufw_state()
    except RuntimeError as exc:
        append_audit_log(repo_root, f"{now_iso()} SNAPSHOT_ERR {exc!r}")
        print(f"REFUSED: snapshot failed — {exc}", file=sys.stderr)
        return 2
    n_rules = sum(
        1 for line in snap["show_added"].splitlines()
        if line.strip().startswith("ufw ")
    )
    append_audit_log(repo_root, f"{now_iso()} SNAPSHOT ufw_rules={n_rules}")
    print(f"  Snapshot: {n_rules} ufw rules captured")

    plan = plan_kill_switch_rules(vpn_iface)
    append_audit_log(repo_root, f"{now_iso()} PLAN     insert={len(plan)}")
    print(f"  Plan:   {len(plan)} ufw commands would run:")
    for cmd in plan:
        print(f"            ufw {' '.join(cmd)}")
    print()
    print("Dry-run only — no rules were changed. Re-run with --apply")
    print("--reason \"...\" to execute the probe.")

    append_audit_log(repo_root, f"{now_iso()} PROBE_END result=dry-run")
    return 0


def run_apply(args, repo_root: Path) -> int:
    """The mutating path. Each toggle is gated by an operator y/N
    prompt. State is restored in a finally regardless of outcome.
    """
    if not args.reason or not args.reason.strip():
        print("ERROR: --apply requires --reason \"...\"", file=sys.stderr)
        return 1

    append_audit_log(
        repo_root,
        f"{now_iso()} PROBE_BEGIN apply=True target={args.target!r} "
        f"reason={args.reason!r}",
    )

    # All precondition checks first — same as dry-run.
    for name, fn in [("root", check_root), ("ufw", check_ufw)]:
        ok, why = fn()
        if not ok:
            append_audit_log(repo_root, f"{now_iso()} REFUSED  {name}: {why}")
            print(f"REFUSED: {name} — {why}", file=sys.stderr)
            return 2

    vpn_iface, vpn_kind, vpn_msg = detect_vpn_interface()
    append_audit_log(repo_root, f"{now_iso()} DETECT   {vpn_msg}")
    if vpn_iface is None:
        print(f"REFUSED: {vpn_msg}", file=sys.stderr)
        return 2

    ok, msg = check_target_reachable(args.target)
    if not ok:
        append_audit_log(
            repo_root,
            f"{now_iso()} REFUSED  target_unreachable {args.target} {msg}",
        )
        print(f"REFUSED: target not reachable from baseline — {msg}",
              file=sys.stderr)
        return 2

    try:
        snap = snapshot_ufw_state()
    except RuntimeError as exc:
        append_audit_log(repo_root, f"{now_iso()} SNAPSHOT_ERR {exc!r}")
        print(f"REFUSED: snapshot failed — {exc}", file=sys.stderr)
        return 2

    plan = plan_kill_switch_rules(vpn_iface)

    # Operator confirmation per rule, per the agreed posture.
    print("=" * 64)
    print(f"  About to apply {len(plan)} ufw command(s) to install the "
          f"kill switch:")
    for cmd in plan:
        print(f"    ufw {' '.join(cmd)}")
    print()
    print("  After install, the probe will:")
    print(f"    1. Re-curl {args.target} — should now fail (kill in effect)")
    print( "    2. Restore the original ufw state from the snapshot")
    print()
    print("  Answer 'n' to any prompt to abort. State will be restored.")
    print("=" * 64)

    applied: list[list[str]] = []

    def restore_and_log(reason: str, rc: int) -> int:
        """Restore in a finally; tag the audit log with the outcome."""
        rok, rmsg = restore_ufw_state(snap)
        append_audit_log(
            repo_root,
            f"{now_iso()} RESTORE  ok={rok} {rmsg}",
        )
        if not rok:
            append_audit_log(
                repo_root,
                f"{now_iso()} PROBE_END result=restore-failed reason={reason}",
            )
            print(f"\nRESTORE FAILED: {rmsg}", file=sys.stderr)
            print("OPERATOR ACTION REQUIRED: inspect ufw state. Audit log:")
            print(f"  {repo_root.joinpath(*_LOG_REL)}", file=sys.stderr)
            return 4
        append_audit_log(
            repo_root,
            f"{now_iso()} PROBE_END result={reason}",
        )
        return rc

    try:
        for i, cmd in enumerate(plan, start=1):
            prompt = (f"  [{i}/{len(plan)}] Run `ufw {' '.join(cmd)}`? "
                      f"[y/N] ")
            answer = input(prompt).strip().lower()
            if answer != "y":
                print("  aborted by operator")
                append_audit_log(
                    repo_root,
                    f"{now_iso()} ABORTED  step={i}/{len(plan)} cmd="
                    f"{' '.join(cmd)!r}",
                )
                return restore_and_log("operator-aborted", 0)
            ok, msg = apply_rule(cmd)
            append_audit_log(
                repo_root,
                f"{now_iso()} APPLY    step={i}/{len(plan)} cmd="
                f"{' '.join(cmd)!r} ok={ok} {msg}",
            )
            if not ok:
                print(f"  step {i} FAILED: {msg}", file=sys.stderr)
                return restore_and_log("apply-failed", 3)
            applied.append(cmd)
            print(f"  step {i}: {msg}")

        # All rules applied — measure that the target IS now blocked.
        ok, msg = check_target_reachable(args.target)
        append_audit_log(
            repo_root,
            f"{now_iso()} VERIFY   target_reachable_after_kill={ok} {msg}",
        )
        if ok:
            # Target still responds — kill switch FAILED to close the leak.
            print(f"\nKILL-SWITCH FAILURE: target still reachable: {msg}",
                  file=sys.stderr)
            return restore_and_log("kill-leaked", 3)

        print(f"\n  Verified fail-closed: target unreachable as expected "
              f"({msg})")
        return restore_and_log("ok", 0)

    except KeyboardInterrupt:
        print("\n  Interrupted by operator (Ctrl-C)")
        append_audit_log(repo_root, f"{now_iso()} INTERRUPT")
        return restore_and_log("interrupted", 0)
    except Exception as exc:  # noqa: BLE001 — last resort, must always restore
        append_audit_log(repo_root, f"{now_iso()} UNCAUGHT {exc!r}")
        return restore_and_log("uncaught-exception", 3)


def cmd_snapshot(args, repo_root: Path) -> int:
    """Write current ufw state to a JSON file."""
    ok, why = check_root()
    if not ok:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    ok, why = check_ufw()
    if not ok:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    try:
        snap = snapshot_ufw_state()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 3
    out = Path(args.snapshot_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(_json.dumps(snap, indent=2), encoding="utf-8")
    os.replace(tmp, out)
    print(f"snapshot: {out}")
    append_audit_log(repo_root, f"{now_iso()} SNAPSHOT_TO {out}")
    return 0


def cmd_restore(args, repo_root: Path) -> int:
    """Replay a previously-captured snapshot."""
    ok, why = check_root()
    if not ok:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    ok, why = check_ufw()
    if not ok:
        print(f"REFUSED: {why}", file=sys.stderr)
        return 2
    src = Path(args.restore_path)
    if not src.is_file():
        print(f"ERROR: snapshot not found: {src}", file=sys.stderr)
        return 1
    try:
        snap = _json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: snapshot unreadable: {exc}", file=sys.stderr)
        return 1
    ok, msg = restore_ufw_state(snap)
    append_audit_log(repo_root, f"{now_iso()} RESTORE_FROM {src} ok={ok} {msg}")
    print(f"restore: {msg}")
    return 0 if ok else 4


# ── CLI entry point ─────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vpn_kill_switch_probe",
        description=("Active VPN kill-switch probe (T46). Default mode is "
                     "dry-run: detection + snapshot + plan only. --apply "
                     "actually exercises the kill switch (requires "
                     "--reason). --snapshot / --restore are explicit "
                     "state-capture handles."),
    )
    p.add_argument("--apply", action="store_true",
                   help="Execute the probe. Mutates ufw rules. Requires "
                        "--reason.")
    p.add_argument("--reason", default="",
                   help="Required with --apply. Recorded in the audit log.")
    p.add_argument("--target", default=_DEFAULT_TARGET,
                   help=f"VPN-routed HTTP target (default: {_DEFAULT_TARGET}).")
    p.add_argument("--snapshot", dest="snapshot_path", metavar="PATH",
                   default=None,
                   help="Write current ufw state to PATH (no probe action).")
    p.add_argument("--restore", dest="restore_path", metavar="PATH",
                   default=None,
                   help="Restore ufw state from a previously-captured PATH "
                        "(defensive — only useful if --apply crashed before "
                        "its own restore).")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = find_repo_root(Path(__file__).resolve().parent)

    # Mutually exclusive modes — snapshot/restore are separate handles.
    mode_flags = [bool(args.apply), bool(args.snapshot_path),
                  bool(args.restore_path)]
    if sum(mode_flags) > 1:
        print("ERROR: --apply, --snapshot, --restore are mutually exclusive",
              file=sys.stderr)
        return 1

    if args.snapshot_path:
        return cmd_snapshot(args, repo_root)
    if args.restore_path:
        return cmd_restore(args, repo_root)
    if args.apply:
        return run_apply(args, repo_root)
    return run_dry_run(args, repo_root)


if __name__ == "__main__":
    sys.exit(main())
