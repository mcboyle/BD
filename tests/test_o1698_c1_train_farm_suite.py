import os
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_O1698_C1_TRAIN_FARM_SUITE_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")
FARM = os.environ.get("BD_TRAIN_FARM_RUNNER", "")
ADVISORY = "ADVISORY: does not gate landing (O1703)"
SSH = '''#!/bin/bash
[ "$1" = -G ] && { echo "hostname 127.0.0.1"; exit 0; }
echo "$*" >> "$SSH_CALLS"
while [ $# -gt 0 ]; do case "$1" in -o) shift 2;; -*) shift;; *) break;; esac; done
shift
exec bash -c "$*"
'''


@pytest.fixture
def rig(tmp_path):
    assert Path(CANDIDATE).is_file(), "supplied candidate missing"
    assert os.access(CANDIDATE, os.X_OK), "supplied candidate not executable"
    assert Path(FARM).is_file(), "explicit native farm runner required"
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)

    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q")
    (repo / "tests/test_drift.py").write_text("def test_drift():\n    assert False, 'known environment drift'\n")
    git("add", "tests/test_drift.py")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", base)
    (repo / "tests/test_new.py").write_text("def test_new():\n    assert False, 'distinctive new regression'\n")
    git("add", "tests/test_new.py")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "scratch failing test")
    head = git("rev-parse", "HEAD")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "ssh": SSH,
        "vmstat": "#!/bin/sh\nprintf 'r b us sy id wa st\n0 0 0 0 100 0 0\n0 0 0 0 100 0 0\n'\n",
    }.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)
    proc = tmp_path / "proc"
    proc.mkdir()
    (proc / "loadavg").write_text("0.00 0.00 0.00 1/20 100\n")
    for i in range(1, 21):
        (proc / str(i)).mkdir()
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update({
        "LC_ALL": "C",
        "BD_CPU_FARM": "1", "BD_FARM_DENY": "", "BD_FARM_PY": sys.executable,
        "BD_FARM_REMOTE_BASE": str(tmp_path / "remote/bd-cpu-farm"),
        "BD_FARM_PGVECTOR_DSN": "", "BD_FARM_HOSTS_FILE": str(hosts),
        "BD_TRAIN_FARM_REPO": str(repo), "BD_TRAIN_FARM_PLAN": str(tmp_path / "plan"),
        "BD_TRAIN_FARM_PROC": str(proc), "BD_TRAIN_FARM_RUNNER": FARM,
        "BD_TRAIN_FARM_PY": sys.executable,
        "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
        "SSH_CALLS": str(tmp_path / "ssh-calls"),
    })
    return tmp_path, repo, base, head, env


def invoke(rig, *extra):
    root, repo, _base, head, env = rig
    if CANDIDATE.endswith(".py"):
        # Existing native per-sha farm is the pre-wrapper behavior, with its real CLI.
        out = root / "raw"
        command = [sys.executable, CANDIDATE, "run", "--sha", head, "--repo", str(repo),
                   "--hosts", "127.0.0.1", "--out", str(out), "--n", "1", "--reruns", "1", *extra]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120, check=False)
        return result, (out / "VERDICT.md").read_text()
    previous = set((root / "plan").glob("T999-FARM-*.md"))
    result = subprocess.run([CANDIDATE, "999", head, *extra], env=env,
                            capture_output=True, text=True, timeout=180, check=False)
    paths = list(set((root / "plan").glob("T999-FARM-*.md")) - previous)
    assert len(paths) == 1, (result.returncode, result.stdout, result.stderr, paths)
    return result, paths[0].read_text()


def test_advisory_line_and_new_regression(rig):
    result, text = invoke(rig)
    lines = text.splitlines()
    assert lines[0] == "FARM-VERDICT: FAIL", text
    assert lines[1] == ADVISORY, "MISSING_ADVISORY_SECOND_LINE"
    assert result.returncode == 0, "advisory must not gate landing"
    assert "NEW FAIL: tests/test_new.py::test_new" in text, "NEW_FAILURE_LOST"
    assert "KNOWN: FAIL tests/test_drift.py::test_drift" in text
    assert "HOST:" in text and "wall=" in text and "WALL-SECONDS:" in text
    assert "NOT JUDGED:" in text and "FLAKY" in text
    assert len(lines) <= 40
    root, repo, base, head, _env = rig
    assert (root / f"plan/farm-baseline-{base[:8]}/failing.txt").is_file()
    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == head
    assert subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain"], text=True) == ""


def test_explicit_known_baseline_never_hides_other_ids(rig):
    root = rig[0]
    baseline = root / "known.txt"
    baseline.write_text("FAIL tests/test_new.py::test_new\nFAIL tests/test_drift.py::test_drift\n")
    result, text = invoke(rig, "--baseline", str(baseline))
    assert result.returncode == 0 and text.startswith("FARM-VERDICT: PASS\n"), text
    assert "KNOWN: FAIL tests/test_new.py::test_new" in text
    assert "NEW FAIL: none" in text


def spaced_rig(rig):
    root, repo, base, _head, env = rig
    path = repo / "tests/test_space.py"
    path.write_text("import pytest\n@pytest.mark.parametrize('value', ['a b'])\n"
                    "def test_space(value):\n    assert False, 'spaced id regression'\n")
    subprocess.run(["git", "-C", str(repo), "add", "tests/test_space.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=fixture", "-c",
                    "user.email=fixture@invalid", "commit", "-qm", "scratch spaced failing id"], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    return root, repo, base, head, env


def test_new_failure_with_space_is_judged(rig):
    result, text = invoke(spaced_rig(rig))
    assert result.returncode == 0
    assert text.startswith("FARM-VERDICT: FAIL\n"), "SPACED_NEW_FAILURE_NOT_JUDGED"
    assert "NEW FAIL: tests/test_space.py::test_space[a b]" in text
    assert "head-invalid" not in text


def test_spaced_failure_in_baseline_is_known(rig):
    spaced = spaced_rig(rig)
    baseline = spaced[0] / "space-baseline.txt"
    baseline.write_text("FAIL tests/test_space.py::test_space[a b]\n"
                        "FAIL tests/test_new.py::test_new\nFAIL tests/test_drift.py::test_drift\n")
    result, text = invoke(spaced, "--baseline", str(baseline))
    assert result.returncode == 0
    assert text.startswith("FARM-VERDICT: PASS\n"), "SPACED_BASELINE_NOT_JUDGED"
    assert "KNOWN: FAIL tests/test_space.py::test_space[a b]" in text


@pytest.mark.parametrize("load,tasks,wa,reason", [
    (24, 20, 0, "load1"), (0, 8000, 0, "procs"), (0, 20, 21, "iowait"),
])
def test_hub_gate_precedes_any_ssh(rig, load, tasks, wa, reason):
    root = rig[0]
    (root / "proc/loadavg").write_text(f"{load} 0 0 1/{tasks} 1\n")
    for i in range(21, tasks + 1):
        (root / f"proc/{i}").mkdir()
    (root / "bin/vmstat").write_text(f"#!/bin/sh\nprintf 'r b us sy id wa st\\n0 0 0 0 100 {wa} 0\\n0 0 0 0 100 {wa} 0\\n'\n")
    result, text = invoke(rig)
    assert result.returncode == 0
    assert text.startswith("FARM-VERDICT: INCOMPLETE hub-gate\n"), "HUB_GATE_BYPASSED"
    assert reason in text
    assert not (root / "ssh-calls").exists(), "hub-gate contacted ssh"


@pytest.mark.parametrize("hosts,reason", [("", "no-hosts"), ("unapproved", "unapproved-host")])
def test_host_refusal_never_passes_or_contacts_ssh(rig, hosts, reason):
    result, text = invoke(rig, "--hosts", hosts)
    assert result.returncode == 0
    assert text.startswith(f"FARM-VERDICT: INCOMPLETE {reason}\n"), "HOST_ALLOWLIST_BYPASSED"
    assert not (rig[0] / "ssh-calls").exists()


def test_missing_verdict_is_incomplete(rig):
    root, env = rig[0], rig[4]
    broken = root / "broken.py"
    broken.write_text("raise SystemExit(0)\n")
    env["BD_TRAIN_FARM_RUNNER"] = str(broken)
    result, text = invoke(rig)
    assert result.returncode == 0
    assert text.startswith("FARM-VERDICT: INCOMPLETE baseline-invalid\n"), "MISSING_VERDICT_PASSED"


def test_cache_reuses_only_intact_baseline(rig):
    root, base = rig[0], rig[2]
    result, text = invoke(rig)
    assert result.returncode == 0
    initial = (root / "ssh-calls").read_text().count(f"bd-cpu-farm/{base[:8]}-")
    assert initial > 0
    result, text = invoke(rig)
    assert result.returncode == 0 and text.startswith("FARM-VERDICT: FAIL\n")
    assert (root / "ssh-calls").read_text().count(f"bd-cpu-farm/{base[:8]}-") == initial
    cache = root / f"plan/farm-baseline-{base[:8]}/failing.txt"
    cache.write_text(cache.read_text() + "FAIL tests/test_new.py::test_new\n")
    calls = (root / "ssh-calls").read_text()
    result, text = invoke(rig)
    assert text.startswith("FARM-VERDICT: INCOMPLETE baseline-invalid\n")
    assert "cached baseline ledger digest mismatch" in text
    assert (root / "ssh-calls").read_text() == calls


@pytest.mark.parametrize("ledger_line", ["PASS made-up-id\n", "FAIL \n", "FLAKY \n"])
def test_invalid_explicit_baseline_refuses_before_ssh(rig, ledger_line):
    root = rig[0]
    baseline = root / "invalid-baseline"
    baseline.write_text(ledger_line)
    result, text = invoke(rig, "--baseline", str(baseline))
    assert result.returncode == 0
    assert text.startswith("FARM-VERDICT: INCOMPLETE baseline-invalid\n")
    assert "invalid baseline failure-id ledger" in text
    assert not (root / "ssh-calls").exists()


def test_large_record_preserves_summary_and_full_ids(rig):
    root, env = rig[0], rig[4]
    formatter = root / "formatter-fixture.py"
    formatter.write_text('''import argparse
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument("run")
p.add_argument("--sha")
p.add_argument("--out")
a, _ = p.parse_known_args()
out = Path(a.out)
out.mkdir(exist_ok=True, parents=True)
ids = ["tests/test_long.py::test_" + "x" * 400 + str(i) for i in range(200)]
(out / "failing.txt").write_text("".join("FAIL " + i + "\\n" for i in ids))
(out / "VERDICT.md").write_text("FARM-VERDICT: FAIL\\nSHA: " + a.sha + " | bundle fixture\\nTESTS: 200 | failed 200\\n  fixture cores=1 files=1 rc=1 wall=1s junit ok\\nFLAKY (0):\\nNOT JUDGED: 0 file(s)\\n")
raise SystemExit(1)
''')
    env["BD_TRAIN_FARM_RUNNER"] = str(formatter)
    baseline = root / "empty-baseline"
    baseline.write_text("")
    result, text = invoke(rig, "--baseline", str(baseline))
    assert result.returncode == 0 and text.startswith("FARM-VERDICT: FAIL\n")
    assert len(text.encode()) <= 4096 and len(text.splitlines()) <= 40
    assert "NEW-FAILURES: 200" in text and "NOT JUDGED: 0" in text and "FLAKY (0):" in text
    details = Path(text.split("FULL: ", 1)[1].strip())
    full = details.read_text()
    assert full.count("NEW FAIL: tests/test_long.py::test_") == 200


def test_farm_record_cannot_satisfy_auto_merge_precheck(tmp_path):
    merge = Path(os.environ["BD_TRAIN_FARM_AUTO_MERGE"])
    assert merge.is_file()
    repo = tmp_path / "repo"
    repo.mkdir()

    git = lambda *args: subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-qb", "main")
    for name in ("bulk_downloader/__init__.py", "CHANGELOG.md", "tests/test_settings_center_slice4.py"):
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text('__version__ = "1.0.0"\n')
    git("add", "bulk_downloader/__init__.py", "CHANGELOG.md", "tests/test_settings_center_slice4.py")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    git("checkout", "-qb", "train/999")
    for name in ("bulk_downloader/__init__.py", "CHANGELOG.md", "tests/test_settings_center_slice4.py"):
        (repo / name).write_text('__version__ = "1.0.1"\n')
    git("add", "bulk_downloader/__init__.py", "CHANGELOG.md", "tests/test_settings_center_slice4.py")
    git("-c", "user.name=fixture", "-c", "user.email=fixture@invalid", "commit", "-qm", "head")
    head = git("rev-parse", "HEAD")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(origin)], check=True)
    git("remote", "add", "origin", str(origin))
    persist = tmp_path / "persist"
    plan = persist / "harness-work/PLAN-2040"
    plan.mkdir(parents=True)
    (plan / f"T999-FARM-{head[:8]}-20261003T0000Z.md").write_text("FARM-VERDICT: PASS\n" + ADVISORY + "\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f'''#!/bin/bash
case "$1 $2" in
"pr list") echo '999 train/999 main {head}';;
"api repos/"*) echo required-fixture;;
*) exit 0;;
esac
''')
    gh.chmod(0o755)
    halt = bin_dir / "halt"
    halt.write_text("#!/bin/bash\nexit 0\n")
    halt.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("BD_")}
    env.update({"LC_ALL": "C", "BD_AUTO_MERGE_REPO": str(repo), "BD_AUTO_MERGE_PERSIST": str(persist),
                "BD_AUTO_MERGE_PATH_PREFIX": str(bin_dir), "BD_AUTO_MERGE_DRY": "1",
                "BD_AUTO_MERGE_GATE": "vm", "BD_HALT_CHECK": str(halt)})
    result = subprocess.run(["bash", str(merge)], env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0 and "no-precut-record" in result.stdout, (result.stdout, result.stderr)
    blocked = list((persist / "landing").glob("AUTO-MERGE-BLOCKED-*.md"))
    assert len(blocked) == 1 and blocked[0].read_text().startswith("BLOCKED: no-precut-record")
    assert git("rev-parse", "origin/main") == base
