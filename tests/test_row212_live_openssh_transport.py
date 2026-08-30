"""Accept bd-jobs' transport argv against real, isolated OpenSSH.

Row 212's remote controls replace ``ssh`` with a Python fake.  Row 217 made
that fake establish a marker before hanging, which proves the post-connect
deadline but still does not prove that OpenSSH accepts the argv or preserves
the remote command's streams.  These tests cross that remaining transport
boundary without reading or changing the operator's SSH configuration:

* one inetd-mode sshd serves exactly one loopback connection and then exits;
* sshd starts from ``/dev/null`` plus explicit temporary key/auth options;
* ssh starts from an exact temporary ``-F`` config with strict host checking;
* every host, client, and authorization key is generated under ``tmp_path``.

The unauthorized-key arm is the over-permission control.  It proves the
positive result came from the key named by this harness rather than an agent,
an operator, or a permissive system SSH configuration.
"""
from __future__ import annotations

import importlib.machinery
import os
import pwd
import shlex
import shutil
import socket
import subprocess
import threading
from pathlib import Path

BD_GATE_SCOPE = "module"

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "toolchain" / "bin" / "bd-jobs"
jobs = importlib.machinery.SourceFileLoader(
    "bd_jobs_row212_openssh", str(TOOL)).load_module()


def _openssh_binaries():
    ssh = shutil.which("ssh")
    sshd = shutil.which("sshd")
    ssh_keygen = shutil.which("ssh-keygen")
    assert ssh and sshd and ssh_keygen, (
        "live OpenSSH acceptance is UNKNOWN: ssh=%r sshd=%r ssh-keygen=%r"
        % (ssh, sshd, ssh_keygen))
    version = subprocess.run(
        [ssh, "-V"], capture_output=True, text=True, timeout=10)
    assert version.returncode == 0 and "OpenSSH" in version.stderr, (
        "the resolved ssh is not a proved OpenSSH client: %r"
        % (version.stdout + version.stderr,))
    return ssh, sshd, ssh_keygen


def _new_key(ssh_keygen, path):
    result = subprocess.run(
        [ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert path.is_file() and path.with_suffix(path.suffix + ".pub").is_file()


class _OneShotSshd:
    """Give one loopback socket to ``sshd -i``; no daemon needs killing."""

    def __init__(self, sshd, host_key, authorized_keys):
        self._sshd = sshd
        self._host_key = host_key
        self._authorized_keys = authorized_keys
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(1)
        self._listener.settimeout(20)
        self.address = self._listener.getsockname()
        self.accepted = []
        self.result = None
        self.error = None
        self._thread = None

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        if self._thread is None:
            self._listener.close()
        else:
            self.finish()

    def start(self):
        assert self._thread is None
        self._thread = threading.Thread(
            target=self._serve, name="row212-one-shot-sshd")
        self._thread.start()

    def _serve(self):
        try:
            connection, peer = self._listener.accept()
            self.accepted.append(peer)
            argv = [
                self._sshd, "-i", "-e", "-f", "/dev/null",
                "-h", str(self._host_key),
                "-o", "AuthorizedKeysFile=%s" % self._authorized_keys,
                "-o", "StrictModes=no",
                "-o", "UsePAM=no",
                "-o", "PasswordAuthentication=no",
                "-o", "KbdInteractiveAuthentication=no",
                "-o", "PubkeyAuthentication=yes",
                "-o", "AuthenticationMethods=publickey",
                "-o", "PermitEmptyPasswords=no",
                "-o", "LoginGraceTime=10",
                "-o", "ClientAliveInterval=5",
                "-o", "ClientAliveCountMax=1",
                "-o", "LogLevel=VERBOSE",
            ]
            process = subprocess.Popen(
                argv, stdin=connection, stdout=connection,
                stderr=subprocess.PIPE, text=True, close_fds=True)
            connection.close()
            stdout, stderr = process.communicate(timeout=25)
            self.result = (process.returncode, stdout, stderr)
        except BaseException as exc:  # surfaced by finish in the test thread
            self.error = exc
        finally:
            self._listener.close()

    def finish(self):
        assert self._thread is not None, "the one-shot sshd was never started"
        self._thread.join(timeout=30)
        assert not self._thread.is_alive(), (
            "one-shot sshd did not settle after its single connection")
        assert self.error is None, "one-shot sshd failed: %r" % (self.error,)
        assert len(self.accepted) == 1, (
            "expected exactly one live OpenSSH connection, got %r"
            % (self.accepted,))
        assert self.accepted[0][0] == "127.0.0.1", self.accepted
        return self.result


def _client_config(tmp_path, port, identity, host_public_key):
    known_hosts = tmp_path / "known_hosts"
    fields = host_public_key.read_text(encoding="ascii").split()
    assert len(fields) >= 2 and fields[0] == "ssh-ed25519", fields
    known_hosts.write_text(
        "[127.0.0.1]:%d %s %s\n" % (port, fields[0], fields[1]),
        encoding="ascii")

    config = tmp_path / "ssh_config"
    config.write_text(
        "Host row212-loopback\n"
        "  HostName 127.0.0.1\n"
        "  Port %d\n"
        "  User %s\n"
        "  IdentityFile %s\n"
        "  IdentitiesOnly yes\n"
        "  PasswordAuthentication no\n"
        "  KbdInteractiveAuthentication no\n"
        "  StrictHostKeyChecking yes\n"
        "  UserKnownHostsFile %s\n"
        "  GlobalKnownHostsFile /dev/null\n"
        "  LogLevel ERROR\n"
        % (port, pwd.getpwuid(os.getuid()).pw_name, identity, known_hosts),
        encoding="utf-8")
    config.chmod(0o600)
    return config


def _keys(tmp_path):
    ssh, sshd, ssh_keygen = _openssh_binaries()
    host_key = tmp_path / "host_ed25519"
    allowed_key = tmp_path / "allowed_ed25519"
    denied_key = tmp_path / "denied_ed25519"
    for key in (host_key, allowed_key, denied_key):
        _new_key(ssh_keygen, key)
    assert allowed_key.with_suffix(".pub").read_bytes() != (
        denied_key.with_suffix(".pub").read_bytes())
    authorized_keys = tmp_path / "authorized_keys"
    authorized_keys.write_bytes(allowed_key.with_suffix(".pub").read_bytes())
    authorized_keys.chmod(0o600)
    return ssh, sshd, host_key, allowed_key, denied_key, authorized_keys


def test_ordinary_remote_calls_keep_the_existing_operator_argv():
    assert jobs._ssh_argv("target.example", "true") == [
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "--",
        "target.example", "true",
    ]


def test_real_openssh_accepts_the_bd_jobs_transport(tmp_path, monkeypatch):
    ssh, sshd, host_key, allowed_key, _denied, authorized = _keys(tmp_path)
    monkeypatch.setenv("PATH", "%s:/bin" % Path(ssh).parent)
    monkeypatch.setenv("LC_ALL", "C")

    with _OneShotSshd(sshd, host_key, authorized) as server:
        assert server.address[0] == "127.0.0.1"
        config = _client_config(
            tmp_path, server.address[1], allowed_key,
            host_key.with_suffix(".pub"))
        argv = jobs._ssh_argv(
            "row212-loopback", "printf 'BD-OPENSSH-ACCEPTED\\n'",
            config=config)
        assert argv[0:3] == ["ssh", "-F", str(config)], argv

        server.start()
        result, complete, note = jobs._run_remote(
            argv, jobs._RemoteDeadline(total=20, reserve=5),
            phase="the live OpenSSH acceptance probe")
        sshd_result = server.finish()

    assert result.returncode == 0, result.stderr
    assert result.stdout == "BD-OPENSSH-ACCEPTED\n", result.stdout
    assert complete is True and note == ""
    # In inetd mode sshd reports 255 when the successful client disconnects;
    # the client status/output above is the command verdict.  The server log is
    # the independent authentication precondition, not a second exit verdict.
    assert sshd_result is not None, sshd_result
    assert "Accepted key" in sshd_result[2], sshd_result
    assert "Failed publickey" not in sshd_result[2], sshd_result


def test_real_openssh_rejects_an_unauthorized_key(tmp_path, monkeypatch):
    ssh, sshd, host_key, _allowed, denied_key, authorized = _keys(tmp_path)
    monkeypatch.setenv("PATH", "%s:/bin" % Path(ssh).parent)
    monkeypatch.setenv("LC_ALL", "C")
    command_marker = tmp_path / "unauthorized-command-ran"

    with _OneShotSshd(sshd, host_key, authorized) as server:
        config = _client_config(
            tmp_path, server.address[1], denied_key,
            host_key.with_suffix(".pub"))
        argv = jobs._ssh_argv(
            "row212-loopback",
            "printf reached > %s" % shlex.quote(str(command_marker)),
            config=config)

        server.start()
        result, complete, note = jobs._run_remote(
            argv, jobs._RemoteDeadline(total=20, reserve=5),
            phase="the unauthorized live OpenSSH control")
        sshd_result = server.finish()

    assert result.returncode == 255, (result.returncode, result.stderr)
    assert "Permission denied (publickey)" in result.stderr, result.stderr
    assert not command_marker.exists(), (
        "the unauthorized key reached the remote command")
    assert complete is True and note == ""
    assert sshd_result is not None, sshd_result
    assert "Failed publickey" in sshd_result[2], sshd_result
    assert "Accepted key" not in sshd_result[2], sshd_result
