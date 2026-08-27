"""capture.sh must supply a display before it runs the live suite.

L2 (headed-browser-launch) opens a VISIBLE Chromium -- headless=False is a
DANGER_MAP invariant, so the check exists to prove the interactive-login path
works on the deployment. Without an X server it WARNs, correctly: no display is
an environment fact, not a code defect.

The gap this closes is a handoff, not a bug in either script.
scripts/provision_test_host.sh already starts Xvfb and exports DISPLAY -- but
that export dies with the provisioner's process. capture.sh runs later, in a
different shell, and had ZERO references to DISPLAY, so L2 warned even on a
correctly provisioned box unless the operator happened to export DISPLAY by
hand. The capability was provisioned and then not handed over.

This is PROVISION, not seeding: it supplies a real X server so a real headed
browser really launches. Nothing about L2's assertion is weakened -- if the
browser cannot start, L2 still fails.
"""
from __future__ import annotations

import contextlib
import os
import re
import select
import shlex
import shutil
import subprocess
import fcntl
import signal
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_SH = REPO_ROOT / "capture.sh"
FRAGMENT = REPO_ROOT / "scripts" / "lib" / "system_deps.sh"

_LIVE_LANE = "live_tests.run"
_DISPLAY_CANDIDATES = tuple(range(70, 256))


@dataclass(frozen=True)
class _DisplayClaim:
    number: int
    fd: int
    path: Path
    device: int
    inode: int


@dataclass
class _OwnedDisplayProcess:
    number: int
    pid: int
    start_ticks: int
    pidfd: int


def _release_display_claim(claim: _DisplayClaim) -> None:
    """Release only the exact O_EXCL claim descriptor this test created."""
    try:
        current = claim.path.stat(follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (claim.device, claim.inode):
            raise AssertionError(
                "display claim identity changed; refusing pathname cleanup: "
                f"{claim.path}"
            )
        claim.path.unlink()
    finally:
        os.close(claim.fd)


def _claim_unused_display() -> _DisplayClaim:
    """Atomically claim one candidate before asking Xvfb to bind its sockets."""
    count = len(_DISPLAY_CANDIDATES)
    assert count > 0, "UNKNOWN: display candidate denominator is empty"
    offset = os.getpid() % count
    for index in range(count):
        number = _DISPLAY_CANDIDATES[(offset + index) % count]
        claim_path = Path(f"/tmp/bd-display-test-X{number}.claim")
        try:
            fd = os.open(
                claim_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o600,
            )
        except FileExistsError:
            continue
        info = os.fstat(fd)
        claim = _DisplayClaim(number, fd, claim_path, info.st_dev, info.st_ino)
        os.write(fd, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(fd)
        lock = Path(f"/tmp/.X{number}-lock")
        socket = Path(f"/tmp/.X11-unix/X{number}")
        probe = subprocess.run(
            ["xdpyinfo", "-display", f":{number}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if probe.returncode != 0 and not lock.exists() and not socket.exists():
            return claim
        _release_display_claim(claim)
    raise AssertionError(
        f"UNKNOWN: none of {count} atomically claimed display candidates was free"
    )


def _proc_identity(pid: int) -> tuple[int, str] | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
        fields = raw.rsplit(") ", 1)[1].split()
        return int(fields[19]), fields[0]
    except (OSError, IndexError, ValueError):
        return None


def _identity_from_receipt(receipt: Path, number: int) -> _OwnedDisplayProcess:
    try:
        fields = receipt.read_text(encoding="ascii").split()
    except OSError as exc:
        raise AssertionError(
            "ownership receipt missing: refusing to clean up an unowned display"
        ) from exc
    assert len(fields) == 2 and all(field.isdigit() for field in fields), (
        f"ownership receipt malformed: {fields!r}"
    )
    pid, start_ticks = map(int, fields)
    assert pid > 1 and start_ticks > 0, (
        f"ownership receipt has invalid identity: pid={pid} start={start_ticks}"
    )
    try:
        pidfd = os.pidfd_open(pid, 0)
    except (AttributeError, OSError) as exc:
        raise AssertionError(
            "UNKNOWN: ownership receipt could not be bound to a pidfd"
        ) from exc
    try:
        observed_identity = _proc_identity(pid)
        observed_start = observed_identity[0] if observed_identity else None
        comm = Path(f"/proc/{pid}/comm").read_text(encoding="ascii").strip()
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        assert observed_start == start_ticks, (
            "ownership receipt start-time mismatch: refusing a reusable PID "
            f"(pid={pid}, receipt={start_ticks}, observed={observed_start})"
        )
        assert comm == "Xvfb", (
            f"ownership receipt names {comm!r}, not the Xvfb this test started"
        )
        assert f":{number}".encode("ascii") in cmdline, (
            f"ownership receipt PID {pid} does not serve display :{number}"
        )
    except BaseException:
        os.close(pidfd)
        raise
    return _OwnedDisplayProcess(number, pid, start_ticks, pidfd)


def _owned_process_alive(owned: _OwnedDisplayProcess) -> bool:
    identity = _proc_identity(owned.pid)
    return bool(
        identity is not None
        and identity[0] == owned.start_ticks
        and identity[1] != "Z"
    )


def _terminate_owned_display(owned: _OwnedDisplayProcess) -> None:
    """Signal the non-reusable pidfd, never a PID recovered from an X lock."""
    try:
        if _owned_process_alive(owned):
            signal.pidfd_send_signal(owned.pidfd, signal.SIGTERM)
            poller = select.poll()
            poller.register(owned.pidfd, select.POLLIN)
            events = poller.poll(5000)
            assert events, (
                f"owned Xvfb pid {owned.pid} did not exit after SIGTERM"
            )
        assert not _owned_process_alive(owned), (
            f"owned Xvfb identity still alive after cleanup: pid={owned.pid}"
        )
    finally:
        os.close(owned.pidfd)
        owned.pidfd = -1


def _write_xvfb_wrapper(path: Path, real_xvfb: str) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "start_ticks=$(awk '{ print $22 }' /proc/$$/stat)\n"
        "tmp=${ROW300_XVFB_RECEIPT}.tmp.$$\n"
        "(umask 077; printf '%s %s\\n' \"$$\" \"$start_ticks\" >\"$tmp\")\n"
        "mv -- \"$tmp\" \"$ROW300_XVFB_RECEIPT\"\n"
        f"exec {shlex.quote(real_xvfb)} \"$@\"\n",
        encoding="ascii",
    )
    path.chmod(0o700)


@contextlib.contextmanager
def _owned_bd_start_display() -> Iterator[_OwnedDisplayProcess]:
    """Run the real helper on an atomic claim and retain its process identity."""
    real_xvfb = shutil.which("Xvfb")
    assert real_xvfb is not None, "precondition: Xvfb is required"
    claim = _claim_unused_display()
    owned: _OwnedDisplayProcess | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="bd-owned-display-") as raw_workspace:
            workspace = Path(raw_workspace)
            wrapper = workspace / "Xvfb"
            receipt = workspace / "ownership.receipt"
            try:
                _write_xvfb_wrapper(wrapper, real_xvfb)
                environment = os.environ.copy()
                environment["PATH"] = f"{workspace}:{environment['PATH']}"
                environment["ROW300_XVFB_RECEIPT"] = str(receipt)
                script = (
                    f'set -u; cd "{REPO_ROOT}"; . scripts/lib/system_deps.sh; '
                    f"bd_start_display :{claim.number}"
                )
                proc = subprocess.run(
                    ["bash", "-c", script],
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                owned = _identity_from_receipt(receipt, claim.number)
                assert proc.returncode == 0, (
                    f"bd_start_display failed: rc={proc.returncode} stderr={proc.stderr}"
                )
                assert proc.stdout.strip() == f":{claim.number}", (
                    f"expected the display on stdout, got {proc.stdout!r}"
                )
                assert _owned_process_alive(owned), (
                    "ownership precondition: helper-started Xvfb is not alive"
                )
                yield owned
            finally:
                # A timeout or assertion can occur after the wrapper published
                # its identity but before the normal receipt read. Bind and
                # terminate that exact identity while the receipt still exists.
                active_error = sys.exception()
                recovery_error: AssertionError | None = None
                if owned is None and receipt.exists():
                    try:
                        owned = _identity_from_receipt(receipt, claim.number)
                    except AssertionError as exc:
                        recovery_error = exc
                if owned is not None:
                    _terminate_owned_display(owned)
                if recovery_error is not None:
                    if active_error is None:
                        raise recovery_error
                    active_error.add_note(f"failure cleanup: {recovery_error}")
    finally:
        _release_display_claim(claim)


def _capture_source() -> str:
    return CAPTURE_SH.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Drop whole-line and trailing `#` comments.

    Prose must never satisfy or trip these gates: a comment mentioning DISPLAY
    is documentation, not provisioning. CLAUDE.md 0 counts an over-sensitive
    gate as a soundness bug too, so the stripper also keeps a `#` inside a
    quoted string from truncating a real command.
    """
    out = []
    for line in text.splitlines():
        cleaned, quote = [], None
        for ch in line:
            if quote:
                cleaned.append(ch)
                if ch == quote:
                    quote = None
                continue
            if ch in "'\"":
                quote = ch
                cleaned.append(ch)
                continue
            if ch == "#":
                break
            cleaned.append(ch)
        out.append("".join(cleaned))
    return "\n".join(out)


def test_capture_establishes_a_display_before_the_live_suite():
    """The display must be provisioned upstream of the live lane.

    Ordering is the subject: a display established after the live suite has
    already run provisions nothing. Compares CODE positions, so moving the
    block below the lane fails even though both strings still occur.
    """
    code = _strip_comments(_capture_source())

    display_at = code.find("bd_start_display")
    lane_at = code.find(_LIVE_LANE)

    assert display_at != -1, (
        "capture.sh never calls bd_start_display -- L2 will WARN on a "
        "provisioned box because the provisioner's DISPLAY export does not "
        "survive its own process"
    )
    assert lane_at != -1, "capture.sh no longer runs the live lane -- anchor is stale"
    assert display_at < lane_at, (
        f"bd_start_display is called at offset {display_at}, after the live "
        f"lane at {lane_at}; a display established after the checks have run "
        f"provisions nothing"
    )


def test_capture_exports_display_rather_than_only_computing_it():
    """A local variable does not reach the live suite's subprocess.

    bd_start_display echoes the display; only `export DISPLAY=` puts it in the
    environment the live checks inherit. Assigning without exporting would
    satisfy a naive token search while changing nothing.
    """
    code = _strip_comments(_capture_source())
    assert re.search(r"\bexport\s+DISPLAY=", code), (
        "capture.sh computes a display but never exports DISPLAY, so the live "
        "suite's subprocess does not inherit it"
    )


def test_capture_sources_the_shared_fragment_for_the_helper():
    """bd_start_display must come from the single source of truth.

    Re-implementing an Xvfb launch inline would reintroduce the duplicate this
    project already paid for once: three copies of the system-dependency logic
    that drift, with the copy nobody updated being the one the box runs.
    """
    code = _strip_comments(_capture_source())
    assert "scripts/lib/system_deps.sh" in code, (
        "capture.sh must source scripts/lib/system_deps.sh rather than "
        "re-implementing a display launch inline"
    )


def test_capture_does_not_fail_when_no_display_can_be_provided():
    """Absence of a display must stay a WARN, never a hard capture failure.

    A headless box with no Xvfb is a legitimate deployment. L2 already reports
    that honestly, and #31 established that a live WARN is informational. If
    capture.sh aborted when Xvfb was missing, this change would convert an
    honest warning into a broken capture -- strictly worse than the status quo.
    """
    code = _strip_comments(_capture_source())
    window = code[code.find("bd_start_display"):]
    window = window[:window.find(_LIVE_LANE)] if _LIVE_LANE in window else window
    assert not re.search(r"^\s*exit\s+[1-9]", window, re.M), (
        "the display block can exit non-zero; a missing display must degrade "
        "to a warning, not abort the capture"
    )


@pytest.mark.skipif(shutil.which("Xvfb") is None, reason="Xvfb not installed")
def test_bd_start_display_really_yields_a_usable_display():
    """Behavioural: the helper must produce a display something can connect to.

    A structural test proves capture.sh calls the helper; it cannot prove the
    helper works. This runs it for real on an unused display number and then
    confirms an independent client can open that display, so a helper that
    echoed a value without starting a server would fail here.
    """
    with _owned_bd_start_display() as owned:
        display_num = owned.number
        # Independent confirmation: the socket a client would connect to.
        assert Path(f"/tmp/.X11-unix/X{display_num}").exists(), (
            "bd_start_display returned success but no X socket exists -- it "
            "reported a display nothing is serving"
        )


def test_the_fragment_is_the_only_place_that_launches_xvfb():
    """Anti-drift: no consumer may spawn Xvfb behind the helper's back.

    Scans code, not comments, so documenting Xvfb stays free.
    """
    offenders = []
    for path in (CAPTURE_SH, REPO_ROOT / "scripts" / "provision_test_host.sh",
                 REPO_ROOT / "install_linux.sh"):
        if not path.exists():
            continue
        code = _strip_comments(path.read_text(encoding="utf-8"))
        if re.search(r"^\s*(setsid\s+)?Xvfb\s", code, re.M):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} launch Xvfb directly; the launch belongs in "
        f"{FRAGMENT.name} so idempotency and probing live in one place"
    )


def test_fake_xvfb_started_by_real_helper_does_not_inherit_capture_lock(tmp_path):
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    pid_file = tmp_path / "xvfb.pid"; fake = fake_bin / "Xvfb"
    fake.write_text("#!/bin/sh\necho $$ >\"$XVFB_PID_FILE\"\nexec /bin/sleep 30\n")
    fake.chmod(0o755); lock = tmp_path / "capture.lock"
    script = (
        'exec {owned}>"$CAPTURE_LOCK"; flock -n "$owned"; '
        'export BD_HEARTBEAT_CLOSE_FD="$owned"; '
        f'. "{FRAGMENT}"; _bd_display_active(){{ return 1; }}; '
        'sleep(){ command sleep 0.01; }; bd_start_display :86 >/dev/null 2>&1 || true'
    )
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
           "CAPTURE_LOCK": str(lock), "XVFB_PID_FILE": str(pid_file)}
    result = subprocess.run(["bash", "-c", script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    pid = int(pid_file.read_text().strip()); fd = os.open(lock, os.O_RDWR)
    try:
        os.kill(pid, 0)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd); os.kill(pid, signal.SIGTERM)


@pytest.mark.parametrize("without_setsid", [False, True], ids=["setsid", "fallback"])
def test_shared_display_helper_rejects_malicious_close_fd_without_eval(tmp_path, without_setsid):
    fake_bin = tmp_path / "bin"; fake_bin.mkdir()
    pid_file = tmp_path / "xvfb.pid"; pwned = tmp_path / "injected"
    fake = fake_bin / "Xvfb"
    fake.write_text("#!/bin/sh\necho $$ >\"$XVFB_PID_FILE\"\nexec /bin/sleep 30\n")
    fake.chmod(0o755)
    hide_setsid = ('command(){ if [ "$1" = -v ] && [ "$2" = setsid ]; then '
                   'return 1; fi; builtin command "$@"; }; ' if without_setsid else '')
    script = (f'. "{FRAGMENT}"; {hide_setsid}_bd_display_active(){{ return 1; }}; '
              'sleep(){ command sleep 0.01; }; bd_start_display :87 >/dev/null || true')
    malicious = '9>&-; touch "$PWNED" #'
    env = {**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}",
           "XVFB_PID_FILE": str(pid_file), "PWNED": str(pwned),
           "BD_HEARTBEAT_CLOSE_FD": malicious}
    result = subprocess.run(["bash", "-c", script], env=env,
                            capture_output=True, text=True, timeout=10)
    assert "invalid BD_HEARTBEAT_CLOSE_FD" in result.stderr
    assert not pwned.exists(), "environment value was evaluated as shell code"
    pid = int(pid_file.read_text().strip())
    try: os.kill(pid, 0)
    finally: os.kill(pid, signal.SIGTERM)
