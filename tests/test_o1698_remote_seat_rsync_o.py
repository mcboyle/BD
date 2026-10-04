import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
_CANDIDATE_ENV = os.environ.get("BD_O1698_REMOTE_SEAT_RSYNC_O_CANDIDATE", "")
pytestmark = pytest.mark.skipif(
    not _CANDIDATE_ENV, reason="candidate opt-in required"
)
CANDIDATE = Path(_CANDIDATE_ENV)


@pytest.fixture
def fixture_tree():
    rsync = shutil.which("rsync")
    assert rsync, "RSYNC-FIXTURE-UNAVAILABLE: rsync required"
    nobody = pwd.getpwnam("nobody")
    access = subprocess.run(
        ["sudo", "-n", "-u", "nobody", "id", "-u"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert access.returncode == 0, f"RSYNC-FIXTURE-UNAVAILABLE: {access.stderr}"
    assert int(access.stdout.strip()) == nobody.pw_uid != os.getuid()
    assert CANDIDATE.is_absolute(), f"CANDIDATE-NOT-ABSOLUTE: {CANDIDATE}"
    assert CANDIDATE.is_file(), f"CANDIDATE-ABSENT: {CANDIDATE}"
    assert os.access(CANDIDATE, os.R_OK), f"CANDIDATE-NOT-READABLE: {CANDIDATE}"
    root = Path(tempfile.mkdtemp(prefix="bd-rsync-O-", dir="/tmp"))
    root.chmod(0o755)
    source = root / "source" / "payload.txt"
    source.parent.mkdir(mode=0o755)
    source.write_text("absolute-path payload\n")
    source.chmod(0o644)
    destination = root / "destination"
    destination.mkdir(mode=0o777)
    destination.chmod(0o777)
    implied = destination / "tmp"
    implied.mkdir()
    implied.chmod(Path("/tmp").stat().st_mode & 0o7777)
    os.utime(implied, ns=(1_000_000_000, 1_000_000_000))
    assert implied.stat().st_uid != nobody.pw_uid
    assert implied.stat().st_mode & 0o002
    assert implied.stat().st_mtime_ns != Path("/tmp").stat().st_mtime_ns
    transport = root / "rsync-fixture"
    transport.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "Path(os.environ['RSYNC_ARGV']).write_text(json.dumps(args))\n"
        "assert args[-1] == 'fixture-host:/'\n"
        "index = args.index('-e')\n"
        "del args[index:index + 2]\n"
        "args[-1] = os.environ['RSYNC_DESTINATION'] + '/'\n"
        "result = subprocess.run(['sudo', '-n', '-u', 'nobody', "
        "os.environ['RSYNC_BINARY'], *args], check=False)\n"
        "Path(os.environ['RSYNC_RC']).write_text(str(result.returncode))\n"
        "sys.exit(result.returncode)\n"
    )
    transport.chmod(0o755)
    try:
        yield root, source, destination, transport, rsync
    finally:
        cleanup = subprocess.run(
            ["sudo", "-n", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert cleanup.returncode == 0, cleanup.stderr
        shutil.rmtree(root)


def run_prep(fixture_tree, candidate):
    root, source, destination, transport, rsync = fixture_tree
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env.update(
        BD_REMOTE_SHARED_ROOT=str(root / "shared"),
        BD_SEAT_HOSTS=str(root / "seat-hosts.tsv"),
        BD_REMOTE_RSYNC=str(transport),
        RSYNC_BINARY=rsync,
        RSYNC_DESTINATION=str(destination),
        RSYNC_ARGV=str(root / "argv.json"),
        RSYNC_RC=str(root / "rsync.rc"),
    )
    result = subprocess.run(
        [
            "bash", "-c",
            ('source "$1"\n'
            'bd_ssh() {\n'
            '  case "$2" in\n'
            '    true|sh|mkdir) return 0 ;;\n'
            '    cat) command cat "$3" ;;\n'
            '    tmux) return 1 ;;\n'
            '    *) return 97 ;;\n'
            '  esac\n'
            '}\n'
            'bd_remote_prep fixture-host rsync-fixture-seat "$2/work" bash "$3"\n'),
            "fixture", str(candidate), str(root), str(source),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    args = json.loads((root / "argv.json").read_text())
    assert args.count(str(source)) == 1
    assert args[-1] == "fixture-host:/"
    assert "--relative" in args
    return result, int((root / "rsync.rc").read_text())


def test_implied_parent_times_succeed_and_absolute_path_is_preserved(fixture_tree):
    root, source, destination, _, _ = fixture_tree
    result, rsync_rc = run_prep(fixture_tree, CANDIDATE)
    assert result.returncode == 0, (
        f"IMPLIED-PARENT-TIMES: prep={result.returncode} rsync={rsync_rc}\n"
        f"{result.stdout}{result.stderr}"
    )
    assert rsync_rc == 0
    landed = destination / source.relative_to("/")
    assert landed.read_bytes() == source.read_bytes()
    assert not (destination / source.name).exists()
    assert landed.stat().st_mtime_ns == source.stat().st_mtime_ns
    rows = (root / "seat-hosts.tsv").read_text().splitlines()
    assert len(rows) == 2
    assert rows[0].startswith("# seat\thost\tat\tby ")
    assert rows[1].startswith("rsync-fixture-seat\tfixture-host\t")
    assert "copied 1 of 1 path(s); shared-fs ok" in result.stdout


def test_base_control_reproduces_directory_times_failure(fixture_tree):
    root, source, destination, _, _ = fixture_tree
    baseline = CANDIDATE.with_name("lib_remote_seat.sh.pre")
    assert baseline.is_file(), f"BASELINE-ABSENT: {baseline}"
    result, rsync_rc = run_prep(fixture_tree, baseline)
    assert result.returncode == 6
    assert rsync_rc == 23
    assert "failed to set times" in result.stderr
    assert "Operation not permitted" in result.stderr
    assert str(destination / "tmp") in result.stderr
    assert "could not stage" in result.stdout
    assert (destination / source.relative_to("/")).read_bytes() == source.read_bytes()
    assert not (root / "seat-hosts.tsv").exists()


def test_real_file_permission_failure_still_refuses(fixture_tree):
    root, source, destination, _, _ = fixture_tree
    landed = destination / source.relative_to("/")
    landed.parent.mkdir(parents=True)
    landed.write_text("existing bytes must survive the refusal\n")
    landed.chmod(0o444)
    before = landed.read_bytes()
    result, rsync_rc = run_prep(fixture_tree, CANDIDATE)
    assert result.returncode == 6
    assert rsync_rc == 23
    assert "mkstemp" in result.stderr
    assert "Permission denied" in result.stderr
    assert "payload.txt" in result.stderr
    assert "could not stage" in result.stdout
    assert landed.read_bytes() == before
    assert not (root / "seat-hosts.tsv").exists()
