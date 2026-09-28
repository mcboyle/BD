"""BH wave 1 H-SSH-STDIN: harness candidates for findings BH-bd-grok-audit-1-004/005/006.

004 bd-corpus-mirror.sh and 005 bd-deploy-after-cuts.sh: an ssh inside a
``while read ... done < hosts`` loop without ``-n`` swallows the rest of the
hosts file, so later hosts are never visited and the script still reports
done.  006 bd-fullsuite-remote.sh: REF and W were expanded locally into the
remote shell text, so a metacharacter in either ran as a second remote
command beside ``rm -rf``.

The candidates live outside the repo.  Opt in with
BD_BH_WAVE1_SSH_STDIN_CANDIDATE=<dir holding the three candidate scripts>.
Every ssh/scp/rsync/git is a PATH stub; nothing leaves the host.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_BH_WAVE1_SSH_STDIN_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

# A stub ssh that behaves like the real one on stdin: without -n it drains
# stdin, which is exactly what eats the hosts file in the defect shape.
SSH_STUB = r"""#!/bin/bash
log=$STUB_LOG/ssh.log
drain=1; host=
args=("$@")
i=0
while [ $i -lt ${#args[@]} ]; do
  a=${args[$i]}
  case "$a" in
    -n) drain=0 ;;
    -o) i=$((i+1)) ;;
    -*) ;;
    *) host=$a; break ;;
  esac
  i=$((i+1))
done
cmd=${args[${#args[@]}-1]}
printf 'HOST=%s N=%s\n' "$host" "$((1-drain))" >> "$log"
[ "$drain" = 1 ] && cat > /dev/null
case "$host" in bad*) exit 255 ;; esac
if [ "${STUB_SSH_EXEC:-0}" = 1 ]; then exec bash -c "$cmd"; fi
case "$cmd" in
  *captures*) echo EMPTY ;;
  *'bd_capture-*.tar.gz'*) echo /tmp/bd_capture-stub.tar.gz ;;
  *capture.sh*) echo "capture rc=0" ;;
  *'systemctl is-active'*) echo active ;;
esac
exit 0
"""

SCP_STUB = r"""#!/bin/bash
printf 'SCP %s\n' "$*" >> "$STUB_LOG/scp.log"
dest=${!#}
: > "$dest"
"""

# git stub for the fullsuite remote script: one record per call, one arg per
# line, so a test can assert the exact argv boundaries.
GIT_STUB = r"""#!/bin/bash
{ echo "--CALL--"; printf '%s\n' "$@"; } >> "$STUB_LOG/git.log"
if [ "$1" = worktree ] && [ "$2" = add ]; then mkdir -p "${@: -2:1}"; fi
if [ "$1" = -C ] && [ "$3" = show ]; then echo '__version__ = "9.9.9"'; fi
if [ "$1" = -C ] && [ "$3" = rev-parse ]; then echo 0123abcd; fi
exit 0
"""


def _exe(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _script(name: str) -> Path:
    path = Path(CANDIDATE) / name
    # A supplied but missing/non-executable candidate is a failure, never a skip.
    assert path.is_file(), f"candidate missing: {path}"
    assert os.access(path, os.R_OK), f"candidate unreadable: {path}"
    return path


@pytest.fixture()
def stubenv(tmp_path: Path) -> dict[str, str]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    logs = tmp_path / "logs"
    logs.mkdir()
    _exe(bindir / "ssh", SSH_STUB)
    _exe(bindir / "scp", SCP_STUB)
    _exe(bindir / "git", GIT_STUB)
    _exe(
        bindir / "rsync",
        '#!/bin/bash\necho rsync-stub-called >> "$STUB_LOG/rsync.log"\nexit 1\n',
    )
    _exe(bindir / "timeout", '#!/bin/bash\nshift\nexec "$@"\n')
    env = {
        "PATH": f"{bindir}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "STUB_LOG": str(logs),
        "LANG": "C",
    }
    (tmp_path / "home").mkdir()
    return env


def _ssh_lines(env: dict[str, str]) -> list[str]:
    log = Path(env["STUB_LOG"]) / "ssh.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _hosts(tmp_path: Path, lines: list[str]) -> Path:
    hosts = tmp_path / "hosts"
    hosts.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
    # Confirm the fixture holds data before reading any zero/nonzero result.
    assert len(hosts.read_text(encoding="utf-8").splitlines()) == len(lines) > 0
    return hosts


# ---------------------------------------------------------------- 004 corpus mirror


def _run_mirror(
    tmp_path: Path, env: dict[str, str], hosts: Path
) -> tuple[subprocess.CompletedProcess[str], str]:
    log = tmp_path / "mirror.log"
    run_env = dict(
        env, HOSTS=str(hosts), LOG=str(log), DEST=str(tmp_path / "dest"), MIN_FREE_G="0"
    )
    proc = subprocess.run(
        ["bash", str(_script("bd-corpus-mirror.sh"))],
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return proc, log.read_text(encoding="utf-8") if log.exists() else ""


def test_corpus_mirror_probes_every_host(
    tmp_path: Path, stubenv: dict[str, str]
) -> None:
    hosts = _hosts(
        tmp_path, ["a 10.0.0.1", "b 10.0.0.2", "hub 10.0.0.9 local", "c 10.0.0.3"]
    )
    proc, log = _run_mirror(tmp_path, stubenv, hosts)
    assert proc.returncode == 0, (proc.stdout, proc.stderr, log)
    probed = [ln for ln in _ssh_lines(stubenv) if ln.startswith("HOST=")]
    assert [ln.split()[0] for ln in probed] == [
        "HOST=10.0.0.1",
        "HOST=10.0.0.2",
        "HOST=10.0.0.3",
    ]
    assert all(ln.endswith("N=1") for ln in probed), probed
    for name in ("a", "b", "c"):
        assert f"{name} EMPTY" in log
    assert "mirror pass done (3 hosts)" in log


def test_corpus_mirror_unreachable_host_is_unknown_and_loop_continues(
    tmp_path: Path, stubenv: dict[str, str]
) -> None:
    hosts = _hosts(tmp_path, ["a 10.0.0.1", "down bad-host", "c 10.0.0.3"])
    proc, log = _run_mirror(tmp_path, stubenv, hosts)
    assert proc.returncode == 0, log
    assert "down UNKNOWN -- unreachable" in log
    assert "c EMPTY" in log
    assert "mirror pass done (3 hosts)" in log


def test_corpus_mirror_missing_hosts_file_refuses(
    tmp_path: Path, stubenv: dict[str, str]
) -> None:
    proc, log = _run_mirror(tmp_path, stubenv, tmp_path / "no-such-hosts")
    assert proc.returncode == 3
    assert "REFUSING: hosts file" in log
    assert "mirror pass done" not in log
    assert _ssh_lines(stubenv) == []


# ---------------------------------------------------------------- 005 deploy-after-cuts capture


def _run_capture(
    tmp_path: Path, env: dict[str, str], hosts: Path
) -> tuple[subprocess.CompletedProcess[str], str, str]:
    log = tmp_path / "dac.log"
    digest = tmp_path / "DIGEST.md"
    run_env = dict(
        env,
        BD_DAC_PHASE="capture",
        BD_DAC_HOSTS=str(hosts),
        BD_DAC_LOG=str(log),
        BD_DAC_CAPDIR=str(tmp_path / "cap"),
        BD_DAC_DIGEST=str(digest),
    )
    proc = subprocess.run(
        ["bash", str(_script("bd-deploy-after-cuts.sh"))],
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return (
        proc,
        log.read_text(encoding="utf-8") if log.exists() else "",
        digest.read_text(encoding="utf-8") if digest.exists() else "",
    )


def test_deploy_capture_attempts_every_host(
    tmp_path: Path, stubenv: dict[str, str]
) -> None:
    hosts = _hosts(
        tmp_path, ["a 10.0.0.1", "hub 10.0.0.9 local", "b 10.0.0.2", "c 10.0.0.3"]
    )
    proc, log, digest = _run_capture(tmp_path, stubenv, hosts)
    assert proc.returncode == 0, (proc.stderr, log)
    for name, ip in (("a", "10.0.0.1"), ("b", "10.0.0.2"), ("c", "10.0.0.3")):
        assert f"CAPTURE {name} {ip} start" in log
        assert f"CAPTURE {name} pulled bd_capture-stub.tar.gz" in log
        assert (tmp_path / "cap" / f"{name}-bd_capture-stub.tar.gz").is_file()
    assert "CAPTURE hub" not in log
    lines = _ssh_lines(stubenv)
    assert lines and all(ln.endswith("N=1") for ln in lines), lines
    assert re.search(r"Z ALL DONE$", log, re.MULTILINE)
    assert "DEPLOY+CAPTURE done" in digest


def test_deploy_capture_failed_host_blocks_all_done(
    tmp_path: Path, stubenv: dict[str, str]
) -> None:
    hosts = _hosts(tmp_path, ["a 10.0.0.1", "down bad-host", "c 10.0.0.3"])
    proc, log, digest = _run_capture(tmp_path, stubenv, hosts)
    assert proc.returncode == 7, log
    assert "CAPTURE down FAILED rc=255" in log
    assert "CAPTURE c 10.0.0.3 start" in log  # later host still attempted
    assert "CAPTURE FAILED on 2 step(s) across 3 hosts; NOT ALL DONE" in log
    assert not re.search(r"Z ALL DONE$", log, re.MULTILINE)
    assert digest == ""


# ---------------------------------------------------------------- 006 fullsuite ref/W quoting


def _run_fullsuite(
    tmp_path: Path, env: dict[str, str], ref: str, workdir: Path
) -> tuple[subprocess.CompletedProcess[str], Path]:
    home = Path(env["HOME"])
    py = home / "BulkDownloader" / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    _exe(py, '#!/bin/bash\necho STUB-PYTEST "$@"\nexit 0\n')
    outdir = tmp_path / "out"
    run_env = dict(
        env,
        STUB_SSH_EXEC="1",
        BD_FULLSUITE_OUTDIR=str(outdir),
        BD_FULLSUITE_W=str(workdir),
    )
    proc = subprocess.run(
        ["bash", str(_script("bd-fullsuite-remote.sh")), "fakehost", ref],
        env=run_env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        cwd=tmp_path,
    )
    return proc, outdir


def _git_calls(env: dict[str, str]) -> list[list[str]]:
    log = Path(env["STUB_LOG"]) / "git.log"
    if not log.exists():
        return []
    calls: list[list[str]] = []
    for ln in log.read_text(encoding="utf-8").splitlines():
        if ln == "--CALL--":
            calls.append([])
        else:
            calls[-1].append(ln)
    return calls


def test_fullsuite_ref_and_workdir_arrive_as_single_arguments(
    tmp_path: Path, stubenv: dict[str, str]
) -> None:
    # W carries a space and a ';' -- the old double-quoted remote text split it
    # into two commands.  Quoted, it must reach git as one argument.
    workdir = tmp_path / "w dir;touch PWNED-W"
    proc, outdir = _run_fullsuite(tmp_path, stubenv, "origin/main", workdir)
    log = (outdir / "fullsuite-origin_main.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, log
    assert [
        "worktree",
        "add",
        "--quiet",
        "--detach",
        str(workdir),
        "origin/main",
    ] in _git_calls(stubenv)
    assert "STUB-PYTEST -m pytest tests/" in log
    assert "FULLSUITE_RC=0" in log
    assert not list(tmp_path.rglob("PWNED*"))
    assert all(ln.endswith("N=1") for ln in _ssh_lines(stubenv))


@pytest.mark.parametrize(
    "ref",
    [
        "origin/main; touch PWNED",
        "origin/main$(touch PWNED)",
        "-oProxyCommand=x",
        "a/../b",
    ],
)
def test_fullsuite_rejects_unsafe_ref_before_ssh(
    tmp_path: Path, stubenv: dict[str, str], ref: str
) -> None:
    proc, _ = _run_fullsuite(tmp_path, stubenv, ref, tmp_path / "w")
    assert proc.returncode == 2, (proc.stdout, proc.stderr)
    assert f"REFUSING ref '{ref}'" in proc.stderr
    assert _ssh_lines(stubenv) == []
    assert not list(tmp_path.rglob("PWNED*"))


def test_fullsuite_accepts_sha_ref(tmp_path: Path, stubenv: dict[str, str]) -> None:
    sha = "596c817f0308892812d1bacae3d9142f62904bec"
    proc, outdir = _run_fullsuite(tmp_path, stubenv, sha, tmp_path / "w")
    log = (outdir / f"fullsuite-{sha}.log").read_text(encoding="utf-8")
    assert proc.returncode == 0, log
    assert [
        "worktree",
        "add",
        "--quiet",
        "--detach",
        str(tmp_path / "w"),
        sha,
    ] in _git_calls(stubenv)
