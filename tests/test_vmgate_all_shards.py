"""P2 vmgate-all-shards: the VM gate runs EVERY CI gate-suites shard, not only gates-*.

harness-work/P2-ROW-vmgate-all-shards.md (rule 34, a round-costing tool): bd-vm-gate.sh (H735) derived its shard list
with `grep -E '^gates-'`, so CI reds outside that set -- T140 application-safety (5d5d199f) + mutation-tools (45fee51e),
T141 application-safety (3b873cdd) -- passed the VM gate and each cost a re-push and a full CI round. The candidate runs
every name tools/ci_shards.py prints, starts them BEFORE own-tests and collects them after (so the extra shards overlap
own-tests instead of adding to them), and links MAIN's frontend/node_modules for the node-gated parity-graph shard.

The harness is deployed from bd-persist, not this repo: ``BD_VMGATE_ALL_SHARDS_CANDIDATE`` is the absolute path of the
candidate script. Hermetic: the gate's REMOTE body (the ssh heredoc) runs locally with ``bash -s`` against a fixture
git repo whose tools/ci_shards.py, toolchain stubs and tests are fakes; the VM paths are replaced through the
BD_VMGATE_MAIN / BD_VMGATE_ROOT seams (unset over ssh). No ssh, no VM, no train record.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_VMGATE_ALL_SHARDS_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

VENV = Path(sys.prefix)  # the running venv: has pytest + pytest-timeout

CI_SHARDS = """import sys
SHARDS = {SHARDS!r}
if sys.argv[1] == "names":
    print("\\n".join(SHARDS))
elif sys.argv[1] == "files":
    print(SHARDS[sys.argv[2]])
"""
STUB_OK = "import sys\nsys.exit(0)\n"
FLAKE = "import os, sys\ni = sys.argv.index('--')\nos.execv(sys.argv[i + 1], sys.argv[i + 1:])\n"
T_OK = "def test_ok():\n    assert True\n"
T_BAD = "def test_bad():\n    assert False, 'NON_GATES_SHARD_RED'\n"
# own-test: records when it ran; a shard test records when it started -- the overlap probe
T_OWN = (
    "import os, time\n"
    "def test_own():\n"
    "    t0 = time.time(); time.sleep(3)\n"
    "    open(os.environ['BD_T_STAMPS'] + '/own', 'w').write(f'{t0} {time.time()}')\n"
)
T_STAMP = (
    "import os, time\n"
    "def test_stamp():\n"
    "    t0 = time.time(); time.sleep(3)\n"
    "    open(os.environ['BD_T_STAMPS'] + '/shard', 'w').write(f'{t0} {time.time()}')\n"
)


def _git(root, *args):
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "-C",
            str(root),
            *args,
        ],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _remote_body() -> str:
    script = Path(CANDIDATE)
    assert script.is_file(), f"candidate missing: {CANDIDATE}"
    text = script.read_text()
    start = text.index("<<'REMOTE'\n") + len("<<'REMOTE'\n")
    return text[start : text.index("\nREMOTE\n", start) + 1]


def _gate(tmp_path, shards, own=T_OK, frontend=False):
    main, root, stamps = tmp_path / "main", tmp_path / "vmgate", tmp_path / "stamps"
    for d in (main / "tools", main / "toolchain" / "bin", main / "tests", stamps):
        d.mkdir(parents=True)
    (main / "tools" / "ci_shards.py").write_text(
        CI_SHARDS.replace("{SHARDS!r}", repr(shards))
    )
    for tool in ("bd-guardcheck", "bd-envscan", "bd-secrets", "bd-ratchet"):
        (main / "toolchain" / "bin" / tool).write_text(STUB_OK)
    (main / "toolchain" / "bin" / "bd-flake-classifier").write_text(FLAKE)
    for name, body in (
        ("test_ok.py", T_OK),
        ("test_bad.py", T_BAD),
        ("test_stamp.py", T_STAMP),
    ):
        (main / "tests" / name).write_text(body)
    if frontend:
        (main / "frontend").mkdir()
        (main / "frontend" / "package.json").write_text("{}\n")
    (main / ".gitignore").write_text("venv\nfrontend/node_modules\n")
    _git(tmp_path, "init", "-q", "-b", "main", str(main))
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "base")
    base = _git(main, "rev-parse", "HEAD")
    (main / "tests" / "test_own.py").write_text(own)
    _git(main, "add", "-A")
    _git(main, "commit", "-qm", "candidate")
    sha = _git(main, "rev-parse", "HEAD")
    (main / "venv").symlink_to(VENV)
    if frontend:
        (main / "frontend" / "node_modules").mkdir()
    env = dict(
        os.environ,
        BD_VMGATE_MAIN=str(main),
        BD_VMGATE_ROOT=str(root),
        BD_T_STAMPS=str(stamps),
    )
    res = subprocess.run(
        [
            "bash",
            "-s",
            "--",
            "train/1-fixture",
            sha,
            base,
            "fixture-host",
            sha[:8],
            "run-fixture",
        ],
        input=_remote_body(),
        text=True,
        capture_output=True,
        env=env,
        timeout=120,
        check=False,
    )
    return res, root / sha[:8], stamps


def test_a_red_non_gates_shard_fails_the_vm_gate(tmp_path):
    res, _wt, _st = _gate(
        tmp_path,
        {"gates-a": "tests/test_ok.py", "application-safety": "tests/test_bad.py"},
    )
    out = res.stdout
    assert "GATE shard-application-safety rc=1" in out, (
        f"VMGATE_NON_GATES_SHARD_SKIPPED: application-safety never ran on the VM gate\n{out}"
    )
    assert "VM-GATE: FAIL" in out and res.returncode == 1, out
    assert "GATE shard-gates-a rc=0" in out, out


def test_control_every_shard_green_passes(tmp_path):
    shards = {
        "gates-a": "tests/test_ok.py",
        "application-safety": "tests/test_ok.py",
        "mutation-tools": "tests/test_ok.py",
    }
    res, _wt, _st = _gate(tmp_path, shards)
    out = res.stdout
    for name in shards:
        assert f"GATE shard-{name} rc=0" in out, out
    assert "GATE ci-shards n=3 " in out, out
    assert "VM-GATE: PASS" in out and res.returncode == 0, out


def test_shards_overlap_own_tests(tmp_path):
    res, _wt, stamps = _gate(tmp_path, {"gates-a": "tests/test_stamp.py"}, own=T_OWN)
    assert "VM-GATE: PASS" in res.stdout, res.stdout
    own_start, own_end = map(float, (stamps / "own").read_text().split())
    shard_start, shard_end = map(float, (stamps / "shard").read_text().split())
    # the two 3 s runs must share time: neither may start after the other has ended
    assert shard_start < own_end and own_start < shard_end, (
        f"VMGATE_SHARDS_NOT_OVERLAPPING_OWNTESTS: own {own_start:.1f}-{own_end:.1f} "
        f"shard {shard_start:.1f}-{shard_end:.1f}"
    )


def test_control_a_red_gates_shard_still_fails(tmp_path):
    """Passes on the deployed script and the candidate: the gates-* contract is unchanged."""
    res, _wt, _st = _gate(tmp_path, {"gates-a": "tests/test_bad.py"})
    assert "GATE shard-gates-a rc=1" in res.stdout, res.stdout
    assert "VM-GATE: FAIL" in res.stdout and res.returncode == 1


def test_control_no_shard_derived_is_a_failure(tmp_path):
    res, _wt, _st = _gate(tmp_path, {})
    assert "GATE ci-shards rc=3 no " in res.stdout, res.stdout
    assert "VM-GATE: FAIL" in res.stdout and res.returncode == 1


def test_frontend_gets_mains_node_modules(tmp_path):
    """parity-graph's node gates need frontend/node_modules; the gate tree links MAIN's, as it does venv."""
    res, wt, _st = _gate(tmp_path, {"gates-a": "tests/test_ok.py"}, frontend=True)
    assert "VM-GATE: PASS" in res.stdout, res.stdout
    link = wt / "frontend" / "node_modules"
    assert (
        link.is_symlink()
        and link.resolve()
        == (tmp_path / "main" / "frontend" / "node_modules").resolve()
    )
