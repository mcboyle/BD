"""Provisioning INSTALLS postgres when it is absent; it does not just refuse.

BACKLOG 97. `bd_mod3_pg_provision` configures PostgreSQL but never installed
it: with `pg_ctlcluster` missing it printed "postgresql-common absent" and
returned 1. That is true in the cloud image, where the package is baked in, and
FALSE on bare Ubuntu -- so on a freshly built host the step WARNed forever and
row 96's "both provisioning paths give a host the same capabilities" was closed
for this fleet only. Measured @1065: test5, test6 and test7 each needed
`apt-get install postgresql` by hand first.

The file's own header states the standard this fell short of: the failure worth
fixing is not the missing software, it is that a capture on the poorer host goes
GREEN BY SKIPPING what is absent. A provisioner that refuses to provision is the
same defect one level up.

WHAT THIS TEST CANNOT SEE, stated because the row closes PARTIAL on it: the
real `apt-get` path is never exercised here. Every arm below runs against a
stub, so this proves the DECISION -- when we install, with what arguments, and
what we do when it fails -- and proves nothing about whether the package
installs on a real bare host. That remains unverified until the next host is
built from scratch, and the row says so rather than reading as closed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Its subject is one shell function's decision path, not an invariant over the
# tree.
BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_LIB = _REPO / "scripts" / "lib" / "dev_capabilities.sh"

# Resolved BEFORE any PATH is replaced. The stub PATH below contains only our
# fakes, so a bare "bash" cannot be found -- the first run of this file failed
# with FileNotFoundError rather than testing anything.
_BASH = shutil.which("bash") or "/bin/bash"


def _stub(d: Path, name: str, body: str) -> None:
    # ABSOLUTE shebang, not `#!/usr/bin/env bash`. PATH is replaced with the
    # stub directory alone, so `env` cannot be resolved and an env-shebang stub
    # is silently unexecutable -- the stub never runs, its log is never written,
    # and the arm reads as "no install was attempted". Measured: that is exactly
    # how this file failed on its first run against the fixed source.
    p = d / name
    p.write_text(f"#!{_BASH}\n" + body + "\n", encoding="utf-8")
    p.chmod(0o755)


def _env(tmp_path: Path, *, pg_present: bool, apt_rc: int = 0,
         apt_creates_pg: bool = True) -> tuple[Path, Path]:
    """A PATH containing ONLY our stubs.

    PATH is replaced rather than prepended on purpose: this box has a real
    postgres installed by hand, so prepending would leave `command -v
    pg_ctlcluster` resolving the real one and the absent-postgres arm would
    never be taken. The test would pass while exercising the other branch --
    CLAUDE.md section 6's "a harness must assert it built the shape".
    """
    binv = tmp_path / "bin"
    binv.mkdir(exist_ok=True)
    log = tmp_path / "apt.log"

    # The row-285 persistence preflight runs before any package mutation. PATH
    # remains isolated from a real PostgreSQL install, but the filesystem tools
    # needed to prove the test HOME writable must still execute for real.
    for command in ("mkdir", "mktemp", "mv", "rm", "chmod"):
        real = shutil.which(command)
        assert real, f"fixture prerequisite is unavailable: {command}"
        _stub(binv, command, f'exec "{real}" "$@"')

    # psql must FAIL, or the function short-circuits on "already serving".
    _stub(binv, "psql", "exit 1")

    _stub(binv, "apt-get", f"""
echo "$@" >> {log}
if [ "{'yes' if apt_creates_pg else 'no'}" = "yes" ] && [ {apt_rc} -eq 0 ]; then
  case "$*" in *install*) printf '#!{_BASH}\\nexit 0\\n' > {binv}/pg_ctlcluster
                          chmod +x {binv}/pg_ctlcluster ;;
  esac
fi
exit {apt_rc}
""".strip())

    if pg_present:
        _stub(binv, "pg_ctlcluster", "exit 0")
        # The next gate after pg_ctlcluster: with no cluster listed the
        # function returns before touching apt, which is what we want for the
        # over-sensitivity arm.
        _stub(binv, "pg_lsclusters", "exit 0")

    return binv, log


def _run(binv: Path) -> subprocess.CompletedProcess:
    script = f'. "{_LIB}"\nSUDO=""\nbd_mod3_pg_provision\n'
    env = dict(os.environ)
    env["PATH"] = str(binv)
    env["SUDO"] = ""
    home = binv.parent / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return subprocess.run([_BASH, "-c", script], capture_output=True,
                          text=True, env=env)


def test_the_stub_environment_really_hides_pg_ctlcluster(tmp_path):
    """The precondition. If pg_ctlcluster still resolves, every arm below is
    testing the wrong branch and passing for the wrong reason."""
    binv, _ = _env(tmp_path, pg_present=False)

    probe = subprocess.run(
        [_BASH, "-c", "command -v pg_ctlcluster"],
        capture_output=True, text=True, env={**os.environ, "PATH": str(binv)})

    assert probe.returncode != 0, (
        f"pg_ctlcluster still resolves under the stub PATH ({probe.stdout!r}); "
        "the absent-postgres branch would never be taken")


def test_absent_postgres_triggers_an_install(tmp_path):
    binv, log = _env(tmp_path, pg_present=False)

    result = _run(binv)

    assert log.exists(), (
        "apt-get was never invoked -- provisioning still refuses instead of "
        f"installing. stdout={result.stdout!r} stderr={result.stderr!r}")
    calls = log.read_text(encoding="utf-8")
    assert "install" in calls and "postgresql" in calls, (
        f"apt-get ran but not as an install of postgresql: {calls!r}")


def test_it_does_not_install_when_postgres_is_already_present(tmp_path):
    """The over-sensitivity control. Installing on every provisioning run would
    be slow, noisy and a package operation nobody asked for."""
    binv, log = _env(tmp_path, pg_present=True)

    _run(binv)

    assert not log.exists(), (
        "apt-get ran even though pg_ctlcluster was already present: "
        f"{log.read_text(encoding='utf-8')!r}")


def test_a_failed_install_refuses_and_names_the_command(tmp_path):
    binv, log = _env(tmp_path, pg_present=False, apt_rc=100,
                     apt_creates_pg=False)

    result = _run(binv)

    # ASSERT THE SHAPE FIRST. Without this the arm passes on the OLD code for
    # the wrong reason: it refused too, and its message ("postgresql-common
    # absent") contains the word this test looks for. A test that cannot fail
    # has proven nothing -- CLAUDE.md section 2 rule 1.
    assert log.exists(), (
        "no install was attempted, so this is not the failed-install path")

    assert result.returncode != 0, (
        "a failed install reported success; the caller would then treat an "
        "absent capability as provisioned")

    # ASSERT THE REASON, NOT THE CODE -- CLAUDE.md section 10, and this exact
    # mutant escaped a returncode-only check. Every refusal in this function
    # returns 1, so dropping the `|| return 1` on the install still produces a
    # nonzero exit: the run simply falls through and refuses at the NEXT gate
    # instead. The code is identical and the cause is not.
    combined = result.stdout + result.stderr
    assert "install failed" in combined, (
        "the refusal did not come from the install step -- the run fell "
        f"through to a later check, which reports the wrong cause: {combined!r}")


def test_an_install_that_exits_zero_without_delivering_pg_ctlcluster_refuses(
        tmp_path):
    """An exit code is not evidence that the side effect happened.

    This file already records that lesson at @1064 -- `mod3_exit=0` with
    `env_file=ABSENT` -- so the install must be verified by asking for the
    binary, not by trusting apt's status.
    """
    binv, log = _env(tmp_path, pg_present=False, apt_rc=0,
                     apt_creates_pg=False)

    result = _run(binv)

    # Same reason as above: the old code refuses here too, without ever having
    # tried to install. Establish that the install happened before judging what
    # was done with its result.
    assert log.exists(), (
        "no install was attempted, so the 'apt said 0' path was never entered")

    assert result.returncode != 0, (
        "apt-get exited 0 without delivering pg_ctlcluster and provisioning "
        "continued anyway -- the exit code was taken as proof of the effect")

    # Again the reason, not the code. Without the verification the run falls
    # through to the cluster lookup and refuses there, which is nonzero and
    # blames the wrong thing -- the mutant that deletes this check escaped a
    # returncode-only assertion.
    combined = result.stdout + result.stderr
    assert "still absent" in combined, (
        "the refusal did not come from verifying the install; the run fell "
        f"through and reported an unrelated cause: {combined!r}")
