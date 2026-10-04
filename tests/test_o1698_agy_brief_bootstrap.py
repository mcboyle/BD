import importlib.util
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = Path(os.environ.get(
    "BD_O1698_AGY_BRIEF_BOOTSTRAP_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/FIX/o1698-agy-brief-bootstrap",
))


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    # Runtime path routing isolates every fleet dependency; the launcher runs intact.
    home = tmp_path / "operator"
    harness = home / "bd-persist/harness"
    harness.mkdir(parents=True)
    prompts = home / "bd-persist/role-prompts"
    prompts.mkdir()
    for role in ("worker", "review-correctness"):
        (prompts / f"agy-{role}.prompt").write_text("MISSION: isolated bootstrap fixture\n")
    (harness / "lib_limits.sh").write_text(
        "bd_ceiling_for(){ echo 150000; }; bd_turns_for(){ echo 20; }\n"
    )
    (harness / "lib_remote_seat.sh").write_text(
        "bd_take_host_arg(){ BD_HOST=''; BD_ARGV=(\"$@\"); }\n"
    )
    (home / "bd-persist/FLEET_RULE-FLOOR.md").write_text("isolated law fixture\n")
    agents = home / ".codex/agents"
    agents.mkdir(parents=True)
    for role in ("worker", "review-correctness"):
        (agents / f"bd-{role}.toml").write_text('developer_instructions = "MISSION: fixture"\n')
        (home / f".codex/bd-{role}.config.toml").write_text('model = "fixture"\n')
    say = home / "bd-say.sh"
    say.write_text("#!/bin/bash\nexit 99\n")
    say.chmod(0o755)
    claim = home / "bd-role-claim.sh"
    claim.write_text("#!/bin/bash\nexit 99\n")
    claim.chmod(0o755)
    (home / "bd-persist/ROLE-CARDINALITY.tsv").write_text(
        "worker\tMULTI\nreview-correctness\tMULTI\n"
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tmux = bindir / "tmux"
    tmux.write_text('#!/bin/bash\n[ "$1" = has-session ] && exit 1\necho LIVE_SEAT_FORBIDDEN >&2\nexit 99\n')
    tmux.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + ":" + os.environ["PATH"])
    monkeypatch.setenv("CODEX_HOME", str(home / ".codex"))
    monkeypatch.setenv("BD_CX_PREFIX_GATE", "off")
    monkeypatch.setenv("BD_CODEX_APPSERVER_SOCK", str(tmp_path / "absent.sock"))
    monkeypatch.setenv("BD_AGY_WORKDIR", str(tmp_path / "agy-work"))
    monkeypatch.setenv("BD_CX_WORKDIR", str(tmp_path / "cx-work"))
    (tmp_path / "cx-work").mkdir()
    for key in ("BD_REMOTE_SEAT_LIB", "BD_CX_MODEL", "BD_CX_EFFORT", "BD_CX_FEATURES", "BD_CX_SAY", "BD_HOST"):
        monkeypatch.delenv(key, raising=False)
    for name in ("bd-launch-agy.sh", "bd-launch-codex-role.sh"):
        (harness / name).write_text((CANDIDATE / name).read_text().replace("/home/mboyle", str(home)))
        (harness / name).chmod(0o755)
    return harness


def run_launcher(harness, pool, role="worker", *options):
    script = harness / ("bd-launch-agy.sh" if pool == "agy" else "bd-launch-codex-role.sh")
    args = ["bash", str(script), role, "isolated-proof"]
    if pool == "agy":
        args.append("flash")
    return subprocess.run([*args, *options, "--dry-run"], capture_output=True, text=True, timeout=10, check=False)


@pytest.mark.parametrize("pool", ["agy", "codex"])
@pytest.mark.parametrize("option,pointer,role", [
    ("--brief", "YOUR BRIEF: /tmp/brief with spaces.md", "worker"),
    ("--cut", "YOUR LENS OBJECT: /tmp/cut with spaces/.review/BRIEF.md", "review-correctness"),
])
def test_launcher_bootstrap_receives_object(sandbox, pool, option, pointer, role):
    value = "/tmp/brief with spaces.md" if option == "--brief" else "/tmp/cut with spaces"
    result = run_launcher(sandbox, pool, role, option, value)
    assert result.returncode == 0, f"BOOTSTRAP_OBJECT_DROPPED: {result.stderr}"
    assert result.stdout.splitlines().count(pointer) == 1, f"BOOTSTRAP_OBJECT_DROPPED: {result.stdout}"


@pytest.mark.parametrize("pool", ["agy", "codex"])
def test_no_object_retains_bootstrap_without_pointer(sandbox, pool):
    result = run_launcher(sandbox, pool)
    assert result.returncode == 0, result.stderr
    assert "DRY isolated-proof role=worker" in result.stdout
    assert "YOUR BRIEF:" not in result.stdout
    assert "YOUR LENS OBJECT:" not in result.stdout


@pytest.mark.parametrize("pool", ["agy", "codex"])
@pytest.mark.parametrize("option", ["--brief", "--cut"])
def test_missing_object_value_refused(sandbox, pool, option):
    result = run_launcher(sandbox, pool, "worker", option)
    assert result.returncode == 2
    assert f"{option} requires an absolute path" in result.stderr


@pytest.mark.parametrize("pool", ["agy", "codex"])
@pytest.mark.parametrize("kind", ["brief", "cut"])
def test_adapter_forwards_absolute_object_to_launcher(sandbox, pool, kind):
    spec = importlib.util.spec_from_file_location("bootstrap_operations", CANDIDATE / "operations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.HARNESS = sandbox
    args = SimpleNamespace(pool=pool, role="worker" if kind == "brief" else "review-correctness",
                           name="isolated-proof", model=None, brief=None, cut=None)
    setattr(args, kind, "relative object with spaces")
    route = module.launch_route(args)
    result = subprocess.run([*route, "--dry-run"], capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
    absolute = str(Path("relative object with spaces").resolve())
    pointer = f"YOUR BRIEF: {absolute}" if kind == "brief" else f"YOUR LENS OBJECT: {absolute}/.review/BRIEF.md"
    assert result.stdout.splitlines().count(pointer) == 1, "ADAPTER_OBJECT_DROPPED: " + result.stdout


@pytest.mark.parametrize("pool", ["agy", "codex"])
def test_written_bootstrap_contains_brief_before_launch(sandbox, pool, monkeypatch):
    # Stop on an isolated dependency before any seat can launch, after prompt publication.
    home = sandbox.parents[1]
    if pool == "agy":
        agy = home / "model-list-only"
        agy.write_text('#!/bin/bash\n[ "$1" = models ] || exit 99\nprintf "gemini-3.8-flash-high\\tfixture\\n"\n')
        agy.chmod(0o755)
        monkeypatch.setenv("BD_AGY_BIN", str(agy))
        script = sandbox / "bd-launch-agy.sh"
        args = ["bash", str(script), "worker", "isolated-proof", "flash"]
        published = home / "bd-persist/role-systemprompts/isolated-proof.systemprompt"
    else:
        monkeypatch.setenv("BD_CODEX", "/bin/false")
        script = sandbox / "bd-launch-codex-role.sh"
        args = ["bash", str(script), "worker", "isolated-proof"]
        published = Path(os.environ["BD_CX_WORKDIR"]) / ".bd-role-prompt-isolated-proof.md"
    result = subprocess.run([*args, "--brief", "/tmp/assigned brief.md"],
                            capture_output=True, text=True, timeout=10, check=False)
    assert result.returncode != 0, "ISOLATION_BOUNDARY_NOT_REACHED"
    assert published.is_file(), result.stderr
    assert published.read_text().splitlines().count("YOUR BRIEF: /tmp/assigned brief.md") == 1
