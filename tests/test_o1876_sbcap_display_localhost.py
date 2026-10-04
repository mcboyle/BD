"""O1876-5: bd-sbcap's display stack must not publish the desktop LAN-wide.

Base spawned ``x11vnc ... -nopw`` with no ``-localhost`` and ``websockify 6080`` with
no bind host, so the Xvfb :99 desktop was reachable from every interface with no
password. The candidate builds both command lines in one function and exposes them
through ``--print-display-cmds`` (exits before apt/venv/spawn), which these tests
drive. Hermetic: x11vnc/websockify/apt-get/sudo/ss on PATH are stubs that record
their argv; only ``x11vnc -storepasswd`` is answered, everything else exits 97.
The x11vnc stub logs argv and stdin to separate files, so the tests can tell a
password fed on stdin (required) from one placed in argv (/proc/<pid>/cmdline).
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
SBCAP = ROOT / "toolchain" / "bin" / "bd-sbcap"

# Mirrors real x11vnc: ``-storepasswd FILE`` reads password, verify and the
# write confirmation from stdin; ``-storepasswd PASS FILE`` takes it from argv.
_X11VNC_STUB = """#!/bin/bash
printf 'x11vnc %s\\n' "$*" >> "$SBCAP_STUB_TRACE"
if [ "$1" = "-storepasswd" ] && [ "$#" -eq 2 ]; then
  IFS= read -r _pw; IFS= read -r _verify; IFS= read -r _confirm
  printf '%s\\n' "$_pw" >> "$SBCAP_STUB_STDIN"
  [ -n "$_pw" ] && [ "$_pw" = "$_verify" ] && [ "$_confirm" = y ] || exit 1
  printf 'stub-rfbauth' > "$2"
  chmod 644 "$2"   # loose on purpose: bd-sbcap must tighten it to 0600
  exit 0
fi
if [ "$1" = "-storepasswd" ] && [ "$#" -eq 3 ]; then
  printf 'stub-rfbauth' > "$3"
  chmod 644 "$3"
  exit 0
fi
exit 97
"""
_REFUSING_STUB = """#!/bin/bash
printf '%s %s\\n' "$(basename "$0")" "$*" >> "$SBCAP_STUB_TRACE"
exit 97
"""
_SS_STUB = """#!/bin/bash
printf 'ss %s\\n' "$*" >> "$SBCAP_STUB_TRACE"
printf 'State Recv-Q Send-Q Local Address:Port Peer Address:Port\\n'
[ -n "${SBCAP_STUB_SS_LOCAL:-}" ] && printf 'LISTEN 0 100 %s 0.0.0.0:*\\n' "$SBCAP_STUB_SS_LOCAL"
exit 0
"""
# Real tools the pre-spawn part of bd-sbcap needs. PATH is stubs + these links only,
# so a real x11vnc/websockify installed on the test host can never be reached.
_REAL_TOOLS = (
    "python3",
    "git",
    "readlink",
    "dirname",
    "basename",
    "mkdir",
    "chmod",
    "mktemp",
    "rm",
    "mv",
    "awk",
    "grep",
    "sed",
    "cat",
    "env",
    "bash",
)


def _stub_env(tmp_path: Path, ss_local: str = "") -> tuple[dict[str, str], Path, Path]:
    tools = tmp_path / "tools"
    tools.mkdir(parents=True)
    for name in _REAL_TOOLS:
        real = shutil.which(name)
        assert real, f"{name} not on PATH"
        (tools / name).symlink_to(real)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in (
        ("x11vnc", _X11VNC_STUB),
        ("ss", _SS_STUB),
        ("websockify", _REFUSING_STUB),
        ("apt-get", _REFUSING_STUB),
        ("sudo", _REFUSING_STUB),
        ("setsid", _REFUSING_STUB),
    ):
        path = stubs / name
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
    trace = tmp_path / "stub-trace.log"
    trace.touch()
    (tmp_path / "stub-stdin.log").touch()
    root = tmp_path / "sbcap"
    env = dict(
        os.environ,
        LC_ALL="C",
        HOME=str(tmp_path),
        BD_SBCAP_ROOT=str(root),
        PATH=f"{stubs}{os.pathsep}{tools}",
        SBCAP_STUB_TRACE=str(trace),
        SBCAP_STUB_STDIN=str(tmp_path / "stub-stdin.log"),
        SBCAP_STUB_SS_LOCAL=ss_local,
    )
    return env, trace, root


def _run(
    env: dict[str, str], *args: str, timeout: int = 60
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SBCAP), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def _cmd(stdout: str, label: str) -> list[str]:
    lines = [ln for ln in stdout.splitlines() if ln.startswith(f"{label}:")]
    assert len(lines) == 1, (label, stdout)
    return shlex.split(lines[0].split(":", 1)[1])


def _stdin_passwords(tmp_path: Path) -> list[str]:
    return (tmp_path / "stub-stdin.log").read_text(encoding="utf-8").splitlines()


def _trace_tools(trace: Path) -> list[str]:
    return [
        ln.split(" ", 1)[0] for ln in trace.read_text(encoding="utf-8").splitlines()
    ]


def test_default_display_cmds_are_loopback_only_with_rfbauth(tmp_path: Path) -> None:
    env, trace, root = _stub_env(tmp_path)
    proc = _run(env, "--print-display-cmds")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, out
    passfile = root / "x11vnc.passwd"

    x11vnc = _cmd(proc.stdout, "X11VNC-CMD")
    assert x11vnc[0] == "x11vnc", x11vnc
    assert "-localhost" in x11vnc, x11vnc
    assert "-nopw" not in x11vnc, x11vnc
    assert x11vnc[x11vnc.index("-rfbauth") + 1] == str(passfile), x11vnc

    websockify = _cmd(proc.stdout, "WEBSOCKIFY-CMD")
    assert websockify[0] == "websockify", websockify
    assert "127.0.0.1:6080" in websockify, websockify
    assert "6080" not in websockify and "0.0.0.0:6080" not in websockify, websockify
    assert "WARN" not in out, out

    # The PATH is printed; the generated password never is.
    assert f"VNC-PASSFILE: {passfile}" in proc.stdout, proc.stdout
    stored = [
        ln.split()
        for ln in trace.read_text(encoding="utf-8").splitlines()
        if ln.startswith("x11vnc -storepasswd ")
    ]
    assert len(stored) == 1, trace.read_text(encoding="utf-8")
    passwords = _stdin_passwords(tmp_path)
    assert len(passwords) == 1, (passwords, stored)
    password = passwords[0]
    assert len(password) == 8, len(password)
    assert password not in out
    # Nothing but the passfile generation and the listener probe ran.
    assert set(_trace_tools(trace)) == {"x11vnc", "ss"}, trace.read_text(
        encoding="utf-8"
    )


def test_passfile_is_0600_and_reused(tmp_path: Path) -> None:
    env, trace, root = _stub_env(tmp_path)
    first = _run(env, "--print-display-cmds")
    assert first.returncode == 0, first.stdout + first.stderr
    passfile = root / "x11vnc.passwd"
    assert passfile.is_file() and not passfile.is_symlink()
    # The stub wrote it 0644; bd-sbcap must leave it 0600.
    assert stat.S_IMODE(passfile.stat().st_mode) == 0o600, oct(passfile.stat().st_mode)
    assert sorted(p.name for p in root.iterdir() if p.name.startswith(".x11vnc")) == []
    body = passfile.read_bytes()

    passfile.chmod(0o644)
    second = _run(env, "--print-display-cmds")
    assert second.returncode == 0, second.stdout + second.stderr
    assert passfile.read_bytes() == body  # created once, reused
    assert stat.S_IMODE(passfile.stat().st_mode) == 0o600
    stores = [
        ln
        for ln in trace.read_text(encoding="utf-8").splitlines()
        if ln.startswith("x11vnc -storepasswd ")
    ]
    assert len(stores) == 1, stores


def test_vnc_password_reaches_x11vnc_on_stdin_never_argv(tmp_path: Path) -> None:
    # F1 (r1 lens): -storepasswd PASS FILE put the password in x11vnc's argv,
    # readable by any local user via /proc/<pid>/cmdline while it runs.
    env, trace, root = _stub_env(tmp_path)
    proc = _run(env, "--print-display-cmds")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stored = [
        ln.split(" ")[1:]
        for ln in trace.read_text(encoding="utf-8").splitlines()
        if ln.startswith("x11vnc -storepasswd")
    ]
    passfile_tmp = [a for argv in stored for a in argv if a.endswith("/passwd")]
    assert len(stored) == 1 and len(passfile_tmp) == 1, stored
    assert stored[0] == ["-storepasswd", passfile_tmp[0]], (
        "F1-ARGV-PASSWORD: x11vnc -storepasswd argv carries more than the file",
        len(stored[0]),
    )
    passwords = _stdin_passwords(tmp_path)
    assert len(passwords) == 1 and len(passwords[0]) == 8, len(passwords)
    password = passwords[0]
    assert password not in trace.read_text(encoding="utf-8"), "F1-ARGV-PASSWORD"
    assert password not in proc.stdout + proc.stderr
    passfile = root / "x11vnc.passwd"
    assert stat.S_IMODE(passfile.stat().st_mode) == 0o600, oct(passfile.stat().st_mode)


def test_novnc_public_flag_binds_all_interfaces_with_warn(tmp_path: Path) -> None:
    env, _trace, _root = _stub_env(tmp_path)
    for args in (
        ("--novnc-public", "--print-display-cmds"),
        ("--print-display-cmds", "--novnc-public"),
    ):
        proc = _run(env, *args)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        websockify = _cmd(proc.stdout, "WEBSOCKIFY-CMD")
        assert "0.0.0.0:6080" in websockify, websockify
        assert "127.0.0.1:6080" not in websockify, websockify
        assert "WARN: --novnc-public" in proc.stderr, proc.stderr
        # Only the websockify bind opens up; VNC itself stays loopback + auth.
        x11vnc = _cmd(proc.stdout, "X11VNC-CMD")
        assert "-localhost" in x11vnc and "-rfbauth" in x11vnc and "-nopw" not in x11vnc


def test_existing_non_loopback_6080_listener_is_warned_not_touched(
    tmp_path: Path,
) -> None:
    env, trace, _root = _stub_env(tmp_path, ss_local="0.0.0.0:6080")
    proc = _run(env, "--print-display-cmds")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "WARN: existing listener on 0.0.0.0:6080 is not loopback-only" in proc.stderr
    assert set(_trace_tools(trace)) == {"x11vnc", "ss"}  # nothing stopped/killed

    env, _trace, _root = _stub_env(tmp_path / "loopback", ss_local="127.0.0.1:6080")
    quiet = _run(env, "--print-display-cmds")
    assert quiet.returncode == 0, quiet.stdout + quiet.stderr
    assert "WARN" not in quiet.stderr, quiet.stderr


def test_print_mode_fails_closed_without_x11vnc(tmp_path: Path) -> None:
    env, _trace, root = _stub_env(tmp_path)
    (tmp_path / "stubs" / "x11vnc").unlink()
    proc = _run(env, "--print-display-cmds")
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "cannot generate" in proc.stderr, proc.stderr
    assert not (root / "x11vnc.passwd").exists()
    assert "-nopw" not in proc.stdout


def test_unknown_args_still_exit_2(tmp_path: Path) -> None:
    env, trace, _root = _stub_env(tmp_path)
    for args in (
        ("--nope",),
        ("--print-display-cmds", "--nope"),
        ("--check", "--nope"),
        ("--novnc-public", "--bogus"),
    ):
        proc = _run(env, *args)
        assert proc.returncode == 2, (args, proc.stdout, proc.stderr)
        assert "unknown arg" in proc.stderr, proc.stderr
    assert trace.read_text(encoding="utf-8") == ""


def test_help_documents_the_new_flags() -> None:
    proc = _run(dict(os.environ, LC_ALL="C"), "--help", timeout=30)
    assert proc.returncode == 0
    assert "--print-display-cmds" in proc.stdout and "--novnc-public" in proc.stdout
    assert "set -u" not in proc.stdout  # help is the header only


def test_novnc_public_composes_with_check(tmp_path: Path) -> None:
    env = dict(
        os.environ,
        LC_ALL="C",
        HOME=str(tmp_path),
        BD_SBCAP_ROOT=str(tmp_path / "sbcap"),
    )
    proc = _run(env, "--check", "--novnc-public", timeout=180)
    assert proc.returncode in (0, 1), (proc.returncode, proc.stdout, proc.stderr)
    assert "== check done:" in proc.stdout, proc.stdout
