import json
import os
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path("/home/mboyle/bd-persist/harness/bd-band-remote.sh")
SHA = "a" * 40


@pytest.fixture()
def harness(tmp_path):
    fakebin = tmp_path / "bin"
    remote_home = tmp_path / "remote-home"
    state = tmp_path / "state"
    fakebin.mkdir()
    state.mkdir()
    (remote_home / "BulkDownloader" / "venv" / "bin").mkdir(parents=True)
    # THE HOST HAS A BARE MIRROR. Every real band host does -- they were created
    # on 2026-08-31 when repointing the fleet at the authoritative origin was
    # found to have silently broken remote dispatch: the candidate ref is pushed
    # into the host's own ~/bd.git and fetched from there BY PATH, because the
    # candidate SHA has never been pushed to GitHub. A simulated host without
    # one is a state the fleet no longer has.
    (remote_home / "bd.git").mkdir(parents=True)
    roles = tmp_path / "roles"
    roles.write_text("capacity bd2 10.0.0.2\n", encoding="utf-8")

    (fakebin / "git").write_text(
        r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path

args = sys.argv[1:]
state = Path(os.environ["FAKE_STATE"])
with (state / "git.jsonl").open("a") as f:
    f.write(json.dumps({"remote": os.environ.get("FAKE_INSIDE_REMOTE") == "1", "args": args}) + "\n")

if os.environ.get("FAKE_INSIDE_REMOTE") == "1":
    joined = " ".join(args)
    if " status " in " " + joined + " ":
        raise SystemExit(0)
    if " fetch " in " " + joined + " ":
        raise SystemExit(0)
    if "rev-parse" in args and "FETCH_HEAD^{commit}" in args:
        print(("b" * 40) if os.environ.get("FAKE_BAD_PROOF_IP") == os.environ.get("FAKE_ACTIVE_IP") else os.environ["FAKE_SHA"])
        raise SystemExit(0)
    if "rev-parse" in args and "HEAD^{commit}" in args:
        print(os.environ["FAKE_SHA"])
        raise SystemExit(0)
    if "checkout" in args:
        raise SystemExit(0)
    # THE BAND NOW RUNS IN ITS OWN WORKTREE, so the host checkout is never
    # moved and a serving host can lend cores without lending its tree.
    if "worktree" in args:
        if "add" in args:
            wt = Path(args[-2])
            wt.mkdir(parents=True, exist_ok=True)
            (wt / ".git").write_text("gitdir: fake\n")
            (state / "worktrees-added").open("a").write(str(wt) + "\n")
        elif "remove" in args:
            (state / "worktrees-removed").open("a").write(args[-1] + "\n")
        raise SystemExit(0)
    raise SystemExit(97)

if "cat-file" in args:
    raise SystemExit(0)
if "ls-remote" in args:
    remote, ref = args[-2], args[-1]
    ip = re.search(r"@([^/]+)", remote).group(1)
    marker = state / ("ref-" + ip)
    mode = os.environ.get("FAKE_REF_MODE", "absent")
    if mode == "conflict":
        print("f" * 40, ref)
    elif mode == "exact" or marker.exists():
        print(os.environ["FAKE_SHA"], ref)
    raise SystemExit(0)
if "push" in args:
    remote = next(a for a in args if a.startswith("ssh://"))
    ip = re.search(r"@([^/]+)", remote).group(1)
    (state / ("ref-" + ip)).write_text(os.environ["FAKE_SHA"])
    raise SystemExit(0)
raise SystemExit(98)
''',
        encoding="utf-8",
    )
    (fakebin / "ssh").write_text(
        r'''#!/usr/bin/env python3
import json, os, subprocess, sys
from pathlib import Path

args = sys.argv[1:]
state = Path(os.environ["FAKE_STATE"])
with (state / "ssh.jsonl").open("a") as f:
    f.write(json.dumps(args) + "\n")
host = next((a for a in args if "@" in a and not a.startswith("-") and not a.startswith("ssh://")), "")
ip = host.rsplit("@", 1)[-1]
if args[-1] == "true":
    raise SystemExit(255 if ip == os.environ.get("FAKE_DOWN_IP") else 0)
env = os.environ.copy()
env.update({"HOME": os.environ["FAKE_REMOTE_HOME"], "FAKE_INSIDE_REMOTE": "1", "FAKE_ACTIVE_IP": ip})
result = subprocess.run(args[-1], shell=True, executable="/bin/bash", stdin=sys.stdin.buffer, env=env)
raise SystemExit(result.returncode)
''',
        encoding="utf-8",
    )
    (fakebin / "ps").write_text(
        r'''#!/usr/bin/env python3
import os
ip = os.environ.get("FAKE_ACTIVE_IP")
if ip == os.environ.get("FAKE_BUSY_IP"):
    print("venv/bin/python -m pytest tests/test_busy.py")
elif ip == os.environ.get("FAKE_WRITER_IP"):
    print("codex exec worker")
''',
        encoding="utf-8",
    )
    remote_python = remote_home / "BulkDownloader" / "venv" / "bin" / "python"
    remote_python.write_text(
        r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with (Path(os.environ["FAKE_STATE"]) / "pytest.jsonl").open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
raise SystemExit(int(os.environ.get("FAKE_PYTEST_RC", "0")))
''',
        encoding="utf-8",
    )
    for path in (*fakebin.iterdir(), remote_python):
        path.chmod(0o700)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fakebin}:{env['PATH']}",
            "FAKE_STATE": str(state),
            "FAKE_REMOTE_HOME": str(remote_home),
            "FAKE_SHA": SHA,
            "BD_BAND_ROLES": str(roles),
            "BD_BAND_REPO": str(tmp_path / "repo"),
            "BD_BAND_LOG": str(tmp_path / "band.log"),
        }
    )
    return {"env": env, "roles": roles, "state": state, "tmp": tmp_path}


def run(harness, sha=SHA, *selectors, **updates):
    env = harness["env"].copy()
    env.update({k: str(v) for k, v in updates.items()})
    return subprocess.run(
        ["bash", str(SCRIPT), sha, *(selectors or ("tests/default.py",))],
        text=True,
        capture_output=True,
        env=env,
        timeout=10,
    )


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_creation_only_ref_and_selectors_are_exact_argv(harness):
    sentinel = harness["tmp"] / "INJECTED"
    first = "tests/a path.py::test_one"
    second = f"x'; touch {sentinel}; #"
    result = run(harness, SHA, first, second, FAKE_REF_MODE="absent")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not sentinel.exists()
    pytest_argv = records(harness["state"] / "pytest.jsonl")
    assert pytest_argv[0][:4] == ["-m", "pytest", first, second]
    local_git = [r["args"] for r in records(harness["state"] / "git.jsonl") if not r["remote"]]
    pushes = [a for a in local_git if "push" in a]
    assert len(pushes) == 1
    assert f"--force-with-lease=refs/heads/bd-band/{SHA}:" in pushes[0]
    assert all("refs/heads/main" not in " ".join(a) for a in local_git)


def test_pytest_failure_rc_is_not_laundered(harness):
    result = run(harness, SHA, "tests/red.py", FAKE_REF_MODE="exact", FAKE_PYTEST_RC=1)
    assert result.returncode == 1
    assert "REMOTE-UNAVAILABLE" not in result.stdout
    local_git = [r["args"] for r in records(harness["state"] / "git.jsonl") if not r["remote"]]
    assert not any("push" in a for a in local_git)


def test_conflicting_ref_is_never_pushed(harness):
    result = run(harness, SHA, "tests/a.py", FAKE_REF_MODE="conflict")
    assert result.returncode == 64
    local_git = [r["args"] for r in records(harness["state"] / "git.jsonl") if not r["remote"]]
    assert not any("push" in a for a in local_git)
    assert not records(harness["state"] / "pytest.jsonl")


def test_unavailable_first_host_advances_to_second(harness):
    harness["roles"].write_text(
        "capacity bd1 10.0.0.1\ncapacity bd2 10.0.0.2\n", encoding="utf-8"
    )
    # A WRITER ON THE HOST, not a running pytest: since the band moved into its
    # own worktree the lane no longer refuses a host merely for running tests --
    # that rule capped every 48-core box at one band.
    result = run(harness, SHA, "tests/a.py", FAKE_REF_MODE="exact", FAKE_WRITER_IP="10.0.0.1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(records(harness["state"] / "pytest.jsonl")) == 1
    assert "trying next" in result.stdout


def test_a_running_pytest_no_longer_makes_a_host_unavailable(harness):
    """The old admission rule refused any host with a pytest running, so a
    48-core box ran one band and idled 36 cores. Slots replaced it."""
    result = run(harness, SHA, "tests/a.py", FAKE_REF_MODE="exact", FAKE_BUSY_IP="10.0.0.2")
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(records(harness["state"] / "pytest.jsonl")) == 1


def test_the_band_runs_in_its_own_worktree_and_never_moves_the_checkout(harness):
    """A serving host may lend cores, never its tree."""
    result = run(harness, SHA, "tests/a.py", FAKE_REF_MODE="exact")
    assert result.returncode == 0, result.stdout + result.stderr
    remote_git = [r["args"] for r in records(harness["state"] / "git.jsonl") if r["remote"]]
    assert any("worktree" in a and "add" in a for a in remote_git), remote_git
    assert not any("checkout" in a for a in remote_git), (
        "the lane still moves the host checkout")
    added = (harness["state"] / "worktrees-added").read_text().split()
    removed = (harness["state"] / "worktrees-removed").read_text().split()
    assert added and added[0].endswith(SHA), added
    assert added[0] in removed, ("the worktree was not cleaned up", added, removed)


def test_all_slots_taken_makes_the_host_unavailable(harness, tmp_path):
    """Exhausting slots is UNAVAILABLE, never an invisible queue."""
    import fcntl
    lockdir = tmp_path / "locks"
    lockdir.mkdir()
    held = []
    for i in range(1, 5):
        fh = open(lockdir / f"bd-band.slot{i}.lock", "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fh)
    try:
        result = run(harness, SHA, "tests/a.py", FAKE_REF_MODE="exact",
                     BD_BAND_LOCKDIR=str(lockdir))
        assert result.returncode == 64, result.stdout + result.stderr
        assert not records(harness["state"] / "pytest.jsonl")
    finally:
        for fh in held:
            fh.close()


@pytest.mark.parametrize("bad", ["a" * 39, "A" * 40, "z" * 40, "a" * 40 + ";id"])
def test_invalid_sha_refuses_before_transport(harness, bad):
    result = run(harness, bad, "tests/a.py")
    assert result.returncode == 64
    assert not records(harness["state"] / "ssh.jsonl")
