"""Row 349: shared tool caches must be bound to the subject they serve.

Each regression below gives two subjects distinguishable bytes and then forces
the interleave which previously let the second subject overwrite or classify
the first.  The positive assertions matter: refusing both subjects is not a
valid repair for a cross-subject cache.
"""
from __future__ import annotations

import ast
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import zipfile


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_FULLSUITE = _REPO / "toolchain" / "bin" / "bd-fullsuite"
_VENV = _REPO / "toolchain" / "bin" / "bd-venv"
_ENVSCAN = _REPO / "toolchain" / "bin" / "bd-envscan"


def _load_python_tool(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def test_transform_control_loads_subjects_without_identity_assertions():
    """Mutation transform control: loadability is not an identity verdict."""
    _load_python_tool("row349_fullsuite_transform_control", _FULLSUITE)
    _load_python_tool("row349_envscan_transform_control", _ENVSCAN)
    checked = subprocess.run(
        ["bash", "-n", str(_VENV)], capture_output=True, text=True, timeout=10)
    assert checked.returncode == 0, checked.stderr


def _wait_for(predicate, description: str, timeout: float = 20.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {description}")


def _fullsuite_fixture(tree: Path) -> None:
    tests = tree / "tests"
    tests.mkdir()
    for name in ("test_alpha.py", "test_beta.py"):
        (tests / name).write_text("# row349 selection fixture\n")
    (tree / "run_tests.py").write_text(
        "import os, sys, time\n"
        "root = os.path.dirname(os.path.abspath(__file__))\n"
        "target = sys.argv[1]\n"
        "with open(os.path.join(root, 'events.log'), 'a') as out:\n"
        "    out.write('started ' + target + '\\n')\n"
        "    out.flush(); os.fsync(out.fileno())\n"
        "deadline = time.monotonic() + 15\n"
        "while not os.path.exists(os.path.join(root, 'release')):\n"
        "    if time.monotonic() >= deadline: raise RuntimeError('release absent')\n"
        "    time.sleep(0.02)\n"
        "print('  PASS  ' + os.path.basename(target))\n"
        "print('Total: 1 | Passed: 1 | Failed: 0 | Skipped: 0')\n"
        "with open(os.path.join(root, 'events.log'), 'a') as out:\n"
        "    out.write('finished ' + target + '\\n')\n"
        "    out.flush(); os.fsync(out.fileno())\n")


def _background_fullsuite(tree: Path, state: Path, selection: str):
    env = dict(os.environ)
    env.pop("BD_INSTALL_DIR", None)
    env["BD_DISABLE_KEEPALIVE"] = "1"
    result = subprocess.run(
        [
            sys.executable, str(_FULLSUITE), "--bg", "--work", str(tree),
            "--state-dir", str(state), "--only", selection, "--jobs", "1",
            "--timeout", "20", "--no-fix",
        ],
        cwd=_REPO, env=env, capture_output=True, text=True, timeout=20)
    match = re.search(r"detached pid (\d+).*log: (.+)", result.stdout)
    assert result.returncode == 0 and match, (
        f"background selection {selection!r} was not accepted: "
        f"rc={result.returncode}\n{result.stdout}{result.stderr}")
    return int(match.group(1)), Path(match.group(2).strip())


def _event_lines(tree: Path) -> list[str]:
    path = tree / "events.log"
    return path.read_text().splitlines() if path.exists() else []


def _pid_is_finished(pid: int) -> bool:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().split()
    except (FileNotFoundError, ProcessLookupError):
        return True
    return len(fields) > 2 and fields[2] == "Z"


def test_background_fullsuite_runs_publish_two_identity_bound_results(tmp_path):
    tree = tmp_path / "work"
    state = tmp_path / "state"
    tree.mkdir()
    state.mkdir()
    _fullsuite_fixture(tree)

    pids: list[int] = []
    try:
        alpha_pid, alpha_log = _background_fullsuite(tree, state, "alpha")
        beta_pid, beta_log = _background_fullsuite(tree, state, "beta")
        pids.extend((alpha_pid, beta_pid))
        assert alpha_pid != beta_pid, "precondition: launches need two processes"

        starts = _wait_for(
            lambda: [line for line in _event_lines(tree)
                     if line.startswith("started ")] if len([
                         line for line in _event_lines(tree)
                         if line.startswith("started ")]) == 2 else None,
            "both differently selected workers to be live together")
        assert set(starts) == {
            "started tests/test_alpha.py", "started tests/test_beta.py",
        }, f"precondition: selections were not distinguishable: {starts}"
        assert not any(line.startswith("finished ") for line in _event_lines(tree)), (
            "precondition: both workers must still be held before release")

        (tree / "release").write_text("release both subjects\n")
        finishes = _wait_for(
            lambda: [line for line in _event_lines(tree)
                     if line.startswith("finished ")] if len([
                         line for line in _event_lines(tree)
                         if line.startswith("finished ")]) == 2 else None,
            "both background selections to finish")
        assert set(finishes) == {
            "finished tests/test_alpha.py", "finished tests/test_beta.py",
        }

        _wait_for(lambda: all(_pid_is_finished(pid) for pid in pids),
                  "both detached suite processes to exit")
        result_paths = sorted(state.rglob("results.json"))
        surviving = []
        for path in result_paths:
            try:
                surviving.append(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError) as exc:
                surviving.append({"unreadable": str(exc), "path": str(path)})
        assert len(result_paths) == 2, (
            "two accepted background runs published only one surviving result "
            f"namespace: paths={result_paths} surviving={surviving}")
        summaries = [json.loads(path.read_text()) for path in result_paths]
        subjects = {
            tuple(row["file"] for row in summary["results"])
            for summary in summaries
        }
        assert subjects == {
            ("tests/test_alpha.py",), ("tests/test_beta.py",),
        }, f"the result namespaces did not retain both subjects: {summaries}"
        assert alpha_log != beta_log and alpha_log.exists() and beta_log.exists(), (
            "the accepted runs still share one destructive log namespace: "
            f"{alpha_log} vs {beta_log}")
        assert {path.parent for path in result_paths} == {
            alpha_log.parent, beta_log.parent,
        }, "each advertised run log and result must share the same run identity"

        for path, summary in zip(result_paths, summaries):
            run_id = path.parent.name
            assert summary["run_id"] == run_id, summary
            env = dict(os.environ)
            env.pop("BD_INSTALL_DIR", None)
            status = subprocess.run(
                [sys.executable, str(_FULLSUITE), "--status", "--run-id", run_id,
                 "--state-dir", str(state)],
                cwd=_REPO, env=env, capture_output=True, text=True, timeout=10)
            assert status.returncode == 0, status.stdout + status.stderr
            progress = json.loads(status.stdout)
            assert progress["run_id"] == run_id and progress["state"] == "done"

        protected = result_paths[0]
        before_reuse = protected.read_bytes()
        reused_id = protected.parent.name
        env = dict(os.environ)
        env.pop("BD_INSTALL_DIR", None)
        reuse = subprocess.run(
            [sys.executable, str(_FULLSUITE), "--run-id", reused_id,
             "--state-dir", str(state), "--work", str(tree), "--only", "alpha",
             "--jobs", "1", "--timeout", "20", "--no-fix"],
            cwd=_REPO, env=env, capture_output=True, text=True, timeout=10)
        assert reuse.returncode != 0 and "REFUSED" in (reuse.stdout + reuse.stderr), (
            "an explicit foreground writer was allowed to reuse a completed "
            f"background identity: {reuse.stdout}{reuse.stderr}")
        assert protected.read_bytes() == before_reuse, (
            "the refused identity reuse still changed the original run's bytes")
    finally:
        (tree / "release").touch(exist_ok=True)
        for pid in pids:
            proc = Path(f"/proc/{pid}")
            try:
                command = (proc / "cmdline").read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                continue
            # A fixture failure must not strand its owned detached subject.
            if (str(_FULLSUITE).encode() in command
                    and str(state).encode() in command):
                try:
                    os.kill(pid, 15)
                except ProcessLookupError:
                    pass


def test_fullsuite_json_publication_never_exposes_partial_bytes(
        tmp_path, monkeypatch):
    fullsuite = _load_python_tool("row349_fullsuite", _FULLSUITE)
    target = tmp_path / "progress.json"
    fullsuite._atomic_json_dump(target, {"generation": "old"})

    entered = threading.Event()
    release = threading.Event()
    real_dump = fullsuite.json.dump

    def paused_dump(value, output, *args, **kwargs):
        encoded = json.dumps(value, *args, **kwargs)
        midpoint = len(encoded) // 2
        output.write(encoded[:midpoint])
        output.flush()
        entered.set()
        assert release.wait(5), "test writer was never released"
        output.write(encoded[midpoint:])

    monkeypatch.setattr(fullsuite.json, "dump", paused_dump)
    errors: list[BaseException] = []

    def publish():
        try:
            fullsuite._atomic_json_dump(target, {"generation": "new"})
        except BaseException as exc:  # surfaced in the parent assertion below
            errors.append(exc)

    writer = threading.Thread(target=publish)
    writer.start()
    try:
        assert entered.wait(5), "precondition: writer never paused mid-JSON"
        assert json.loads(target.read_text()) == {"generation": "old"}, (
            "a status reader received the new generation's partial bytes")
    finally:
        release.set()
        writer.join(5)
    assert not writer.is_alive() and errors == [], errors
    assert json.loads(target.read_text()) == {"generation": "new"}
    monkeypatch.setattr(fullsuite.json, "dump", real_dump)

    source = _FULLSUITE.read_text()
    syntax = ast.parse(source, filename=str(_FULLSUITE))

    def calls(function_name: str, state_name: str) -> list[ast.Call]:
        found = []
        for node in ast.walk(syntax):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            name = (function.id if isinstance(function, ast.Name) else
                    function.attr if isinstance(function, ast.Attribute) else None)
            referenced = {
                child.id for child in ast.walk(node)
                if isinstance(child, ast.Name)
            }
            if name == function_name and state_name in referenced:
                found.append(node)
        return found

    progress_publishers = calls("_atomic_json_dump", "PROGRESS")
    result_publishers = calls("_atomic_json_dump", "RESULTS")
    assert len(progress_publishers) == 3 and len(result_publishers) == 1, (
        "the complete progress/results writer population must use the atomic "
        f"publisher: progress={len(progress_publishers)} results="
        f"{len(result_publishers)}")
    assert calls("dump", "PROGRESS") == [] and calls("dump", "RESULTS") == [], (
        "a result JSON writer bypasses the atomic publication boundary")


def _extraction_block(sandbox: Path) -> str:
    source = _VENV.read_text()
    start = source.index("# --- locate a cloak pack")
    end = source.index("\nvenv_backend_ok()", start)
    block = source[start:end]
    old = '_extract_cache="/tmp/bd_cloak_pack_extracted"'
    if old in block:
        # RED harness redirect from H13-12: preserve the defective identity but
        # confine its destructive target to this test's owned directory.
        assert block.count(old) == 1
        block = block.replace(old, f'_extract_cache="{sandbox / "shared-cache"}"')
    return block


def _make_pack(path: Path, marker: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("src/pip-wheels/control.whl", marker)
        archive.writestr("src/marker", marker)


def _fake_unzip(bin_dir: Path) -> None:
    tool = bin_dir / "unzip"
    tool.write_text(
        f"#!{sys.executable}\n"
        "import sys, zipfile\n"
        "archive = sys.argv[2]\n"
        "destination = sys.argv[4]\n"
        "with zipfile.ZipFile(archive) as zf: zf.extractall(destination)\n")
    tool.chmod(0o755)


def _extract_pack(block: str, cwd: Path, sandbox: Path, fake_bin: Path) -> Path:
    script = (
        "set -eu\n"
        "BD_CLOAK_PACK=\n"
        "eval \"$BD_ROW349_BLOCK\"\n"
        "PACK=\n"
        "found=$(find_cloak_pack || true)\n"
        "[ -n \"$PACK\" ] || PACK=$found\n"
        "printf '%s\\n' \"$PACK\"\n")
    env = dict(os.environ)
    env.update({
        "BD_ROW349_BLOCK": block,
        "PATH": f"{fake_bin}:{env['PATH']}",
        "HOME": str(sandbox / "home"),
        "UPLOADS": str(sandbox / "uploads"),
        "TMPDIR": str(sandbox / "tmp"),
    })
    result = subprocess.run(
        ["bash", "-c", script], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0 and result.stdout.strip(), (
        result.stdout + result.stderr)
    return Path(result.stdout.strip().splitlines()[-1])


def test_cloak_pack_extractions_keep_attempt_owned_bytes(tmp_path):
    sandbox = tmp_path / "owned"
    fake_bin = sandbox / "bin"
    for path in (sandbox, fake_bin, sandbox / "home", sandbox / "uploads",
                 sandbox / "tmp"):
        path.mkdir(parents=True, exist_ok=True)
    attempt_a = sandbox / "attempt-a"
    attempt_b = sandbox / "attempt-b"
    attempt_a.mkdir()
    attempt_b.mkdir()
    _make_pack(attempt_a / "bd_cloak_pack_A.zip", "A")
    _make_pack(attempt_b / "bd_cloak_pack_B.zip", "B")
    _fake_unzip(fake_bin)
    block = _extraction_block(sandbox)

    path_a = _extract_pack(block, attempt_a, sandbox, fake_bin)
    assert (path_a / "marker").read_text() == "A", (
        "precondition: pack A did not extract its own bytes")
    path_b = _extract_pack(block, attempt_b, sandbox, fake_bin)
    assert (path_b / "marker").read_text() == "B", (
        "precondition: pack B did not extract its own bytes")
    assert path_a != path_b, (
        f"two attempts received the same destructive cache path: {path_a}")
    assert (path_a / "marker").read_text() == "A", (
        "pack A's still-live returned path changed to pack B's bytes: "
        f"A_returned={path_a} B_returned={path_b}")


def _shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + 3
    return source[start:end]


def _start_lock_holder(function: str, venv: Path, acquired: Path, release: Path):
    script = (
        "set -eu\n"
        f"{function}\n"
        f"VENV={shlex.quote(str(venv))}\n"
        "acquire_venv_lock\n"
        f"printf '%s\\n' \"$_venv_lock_path\" > {shlex.quote(str(acquired))}\n"
        f"while [ ! -e {shlex.quote(str(release))} ]; do sleep 0.02; done\n")
    return subprocess.Popen(
        ["bash", "-c", script], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True)


def test_venv_provisioning_lock_serializes_only_the_same_target(tmp_path):
    source = _VENV.read_text()
    function = _shell_function(source, "acquire_venv_lock")
    assert source.count("\nacquire_venv_lock || {") == 1, (
        "the lock exists but the real provisioning path does not acquire it")
    same_venv = tmp_path / "same" / "venv"
    other_venv = tmp_path / "other" / "venv"
    first_acquired = tmp_path / "first-acquired"
    same_acquired = tmp_path / "same-acquired"
    other_acquired = tmp_path / "other-acquired"
    first_release = tmp_path / "release-first"
    followers_release = tmp_path / "release-followers"
    holders: list[subprocess.Popen[str]] = []
    try:
        first = _start_lock_holder(
            function, same_venv, first_acquired, first_release)
        holders.append(first)
        lock_path = Path(_wait_for(
            lambda: first_acquired.read_text().strip()
            if first_acquired.exists() else None,
            "the first same-venv lock acquisition"))
        assert lock_path.is_file(), "precondition: held lock file is absent"
        contended = subprocess.run(
            ["flock", "-n", str(lock_path), "true"],
            capture_output=True, text=True, timeout=5)
        assert contended.returncode != 0, (
            "precondition: the reported provisioning lock is not held")

        same = _start_lock_holder(
            function, same_venv, same_acquired, followers_release)
        other = _start_lock_holder(
            function, other_venv, other_acquired, followers_release)
        holders.extend((same, other))
        _wait_for(lambda: other_acquired.exists(),
                  "a different target venv to acquire independently")
        assert not same_acquired.exists(), (
            "a second provisioner entered the same target venv concurrently")

        first_release.touch()
        _wait_for(lambda: same_acquired.exists(),
                  "the same-target follower after lock release")
        assert first_acquired.read_text() == same_acquired.read_text(), (
            "same target venvs did not resolve to one lock identity")
        assert other_acquired.read_text() != same_acquired.read_text(), (
            "different target venvs were collapsed into one global lock")
    finally:
        first_release.touch(exist_ok=True)
        followers_release.touch(exist_ok=True)
        for holder in holders:
            try:
                holder.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                holder.terminate()
                holder.communicate(timeout=5)
        assert all(holder.returncode == 0 for holder in holders), [
            (holder.returncode, holder.stdout, holder.stderr) for holder in holders]


def _synthetic_envscan_tree(root: Path, key: str) -> None:
    tools = root / "tools"
    tools.mkdir(parents=True)
    (tools / "spa_population.py").write_text(f"KEY = {key!r}\n")
    (tools / "config_surface_inventory.py").write_text(
        "import spa_population\n"
        "def build(tree):\n"
        "    item = {'key': spa_population.KEY, 'kind': 'env_var', "
        "'runtime_tunable': True, 'gui_exposure': 'none', "
        "'source_file': 'synthetic.py'}\n"
        "    return {'items': [item], "
        "'counts': {'open_runtime_tunable': 1}}\n"
        "def _open_settings(items):\n"
        "    return [item['key'] for item in items "
        "if item['runtime_tunable'] and item['gui_exposure'] != 'full']\n")


def test_envscan_classifies_each_tree_with_that_trees_helpers(tmp_path):
    tree_a = tmp_path / "A"
    tree_b = tmp_path / "B"
    _synthetic_envscan_tree(tree_a, "A_ONLY")
    _synthetic_envscan_tree(tree_b, "B_ONLY")
    assert (tree_a / "tools" / "spa_population.py").read_text() != (
        tree_b / "tools" / "spa_population.py").read_text(), (
        "precondition: the two helper populations must be distinguishable")

    original_path = list(sys.path)
    names = ("config_surface_inventory", "spa_population", "report_core")
    original_modules = {name: sys.modules.get(name) for name in names}
    try:
        for name in names:
            sys.modules.pop(name, None)
        envscan = _load_python_tool("row349_envscan", _ENVSCAN)
        a_open, a_items, a_count = envscan._open_env(str(tree_a))
        b_open, b_items, b_count = envscan._open_env(str(tree_b))
    finally:
        sys.path[:] = original_path
        for name, module in original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    assert a_count == b_count == 1, (
        "precondition: each tree must classify one nonempty setting")
    assert set(a_items) == {"A_ONLY"} and a_open == ["A_ONLY"], (
        f"tree A did not receive its own helper bytes: {a_open}, {a_items}")
    assert set(b_items) == {"B_ONLY"} and b_open == ["B_ONLY"], (
        "tree B was classified with tree A's cached helper: "
        f"B_expected=['B_ONLY'] B_observed={b_open} items={b_items}")
