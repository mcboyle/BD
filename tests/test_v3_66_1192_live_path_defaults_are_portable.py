"""RED-first behavior controls for row 161's live-path phase."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from tools.code_intelligence.adapters import AdapterBudget, AdapterCase, AdapterContext
from tools.code_intelligence.oracle_adapters import CommandOracleAdapter


REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"


def _installed_layout(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path, Path]:
    home = tmp_path / "home"
    suite_bin = home / ".local" / "bin"
    link_bin = tmp_path / "usr-local-bin"
    installed_env = suite_bin / ".bdenv.sh"
    pointer = suite_bin / ".bd-work-tree"
    env = {key: value for key, value in os.environ.items() if not key.startswith("BD_")}
    env.update({
        "HOME": str(home),
        "BD_SUITE_BIN": str(suite_bin),
        "BD_SUITE_LINK_BIN": str(link_bin),
    })
    return env, suite_bin, link_bin, installed_env, pointer


def _foreign_checkout(tmp_path: Path) -> Path:
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    subprocess.run(["git", "init", "-q", str(foreign)], check=True)
    return foreign


def _install(
    env: dict[str, str], toolchain: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str((toolchain or REPO / "toolchain") / "install_bdsuite.sh")],
        cwd=REPO, env=env, text=True, capture_output=True,
    )


def _run_installed_bd(link_bin: Path, env: dict[str, str], cwd: Path):
    run_env = {key: value for key, value in env.items() if not key.startswith("BD_")}
    run_env["BD_ENV_NO_SERVICES"] = "1"
    return subprocess.run(
        [str(link_bin / "bd"), "/bin/sh", "-c", "printf '%s' \"$BD_WORK_TREE\""],
        cwd=cwd, env=run_env, text=True, capture_output=True, timeout=10,
    )


def _load_extensionless(name: str, path: Path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _tree_snapshot(*roots: Path) -> dict[str, tuple]:
    snapshot: dict[str, tuple] = {}
    for root in roots:
        if not root.exists() and not root.is_symlink():
            continue
        paths = [root]
        if root.is_dir() and not root.is_symlink():
            paths.extend(sorted(root.rglob("*")))
        for path in paths:
            key = f"{root.name}/{path.relative_to(root)}"
            mode = path.lstat().st_mode & 0o7777
            if path.is_symlink():
                snapshot[key] = ("link", mode, os.readlink(path))
            elif path.is_dir():
                snapshot[key] = ("dir", mode)
            else:
                snapshot[key] = ("file", mode, path.read_bytes())
    return snapshot


def _failure_shim(tmp_path: Path, command: str, fail_on: int = 1) -> Path:
    shim_dir = tmp_path / f"shim-{command}"
    shim_dir.mkdir()
    real = shutil.which(command)
    assert real
    counter = shim_dir / "count"
    script = shim_dir / command
    script.write_text(
        "#!/bin/sh\n"
        f"count_file={counter!s}\n"
        "n=0\n"
        "[ ! -f \"$count_file\" ] || n=$(sed -n '1p' \"$count_file\")\n"
        "n=$((n + 1))\n"
        "printf '%s\\n' \"$n\" > \"$count_file\"\n"
        f"[ \"$n\" -ne {fail_on} ] || exit 93\n"
        f"exec {real} \"$@\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return shim_dir


def _write_state_pack(path: Path, marker: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("STATE.json", json.dumps({"marker": marker}))


def test_work_tree_resolver_authority_table_fails_closed(tmp_path, monkeypatch):
    """Invalid installed authority cannot be laundered through an ambient Git cwd."""
    resolver = _load_extensionless("bd_work_tree_table", BIN / "_bd_work_tree.py")
    foreign = _foreign_checkout(tmp_path)
    monkeypatch.chdir(foreign)
    monkeypatch.delenv("BD_WORK_TREE", raising=False)

    assert resolver.resolve_work_tree(BIN / "_bd_work_tree.py", explicit=str(REPO)) == REPO.resolve()
    with pytest.raises(resolver.WorkTreeResolutionError):
        resolver.resolve_work_tree(BIN / "_bd_work_tree.py", explicit="")

    installed = tmp_path / "installed"
    installed.mkdir()
    tool = installed / "bd-tool"
    tool.write_text("tool\n", encoding="utf-8")
    pointer = installed / ".bd-work-tree"
    pointer.write_text(str(REPO.resolve()) + "\n", encoding="utf-8")
    assert resolver.resolve_work_tree(tool) == REPO.resolve()

    invalid_values = [
        b"",
        b"\xff",
        b"   \n",
        (str(REPO.resolve()) + "\nextra\n").encode(),
        (" " + str(REPO.resolve()) + "\n").encode(),
        (str(REPO / "README.md") + "\n").encode(),
        (str(tmp_path / "not-git") + "\n").encode(),
    ]
    (tmp_path / "not-git").mkdir()
    for value in invalid_values:
        pointer.write_bytes(value)
        with pytest.raises(resolver.WorkTreeResolutionError):
            resolver.resolve_work_tree(tool)

    pointer.unlink()
    with pytest.raises(resolver.WorkTreeResolutionError):
        resolver.resolve_work_tree(tool)

    target = installed / "pointer-target"
    target.write_text(str(REPO.resolve()) + "\n", encoding="utf-8")
    pointer.symlink_to(target)
    with pytest.raises(resolver.WorkTreeResolutionError):
        resolver.resolve_work_tree(tool)

    pointer.unlink()
    pointer.write_text(str(REPO.resolve()) + "\n", encoding="utf-8")
    hardlink = installed / "pointer-hardlink"
    os.link(pointer, hardlink)
    with pytest.raises(resolver.WorkTreeResolutionError):
        resolver.resolve_work_tree(tool)


def test_installed_public_tools_share_pointer_authority(tmp_path):
    """Public symlinks derive their checkout from the installed transaction."""
    env, suite_bin, link_bin, _installed_env, pointer = _installed_layout(tmp_path)
    installed = _install(env)
    assert installed.returncode == 0, installed.stderr
    foreign = _foreign_checkout(tmp_path)

    run_env = {key: value for key, value in env.items() if not key.startswith("BD_")}
    run_env["BD_ENV_NO_SERVICES"] = "1"
    wedge = subprocess.run(
        [str(link_bin / "bd-wedge-hunt"), "--help"], cwd=foreign, env=run_env,
        text=True, capture_output=True, timeout=10,
    )
    assert wedge.returncode == 0, wedge.stdout + wedge.stderr
    assert "BD-HUNT-UNRUNNABLE" not in wedge.stdout + wedge.stderr

    status = subprocess.run(
        [str(link_bin / "bd-status")], cwd=foreign, env=run_env,
        text=True, capture_output=True, timeout=20,
    )
    assert status.returncode == 0, status.stdout + status.stderr
    assert str(REPO / "bulk_downloader") in status.stdout

    venv = subprocess.run(
        [str(link_bin / "bd-venv"), "--help"], cwd=foreign, env=run_env,
        text=True, capture_output=True, timeout=10,
    )
    assert venv.returncode == 0, venv.stdout + venv.stderr

    state = subprocess.run(
        [str(link_bin / "bd-state"), "--help"], cwd=foreign, env=run_env,
        text=True, capture_output=True, timeout=10,
    )
    assert state.returncode == 0, state.stdout + state.stderr

    trace = tmp_path / "reindex-cwds"
    fake_python = tmp_path / "fake-python"
    fake_python.write_text(
        "#!/bin/sh\n"
        "[ \"${1:-}\" != -c ] || exit 0\n"
        "printf '%s\\n' \"$PWD\" >> \"$BD_REINDEX_TRACE\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    reindex_env = {
        **run_env,
        "BD_VENV_PY": str(fake_python),
        "BD_REINDEX_TRACE": str(trace),
    }
    reindex = subprocess.run(
        [str(link_bin / "bd-reindex")], cwd=foreign, env=reindex_env,
        text=True, capture_output=True, timeout=10,
    )
    assert reindex.returncode == 0, reindex.stdout + reindex.stderr
    assert trace.read_text(encoding="utf-8").splitlines()
    assert set(trace.read_text(encoding="utf-8").splitlines()) == {str(REPO.resolve())}

    guard = subprocess.run(
        [str(link_bin / "bd-guardcheck")], cwd=foreign, env=run_env,
        text=True, capture_output=True, timeout=10,
    )
    assert guard.returncode == 0, guard.stdout + guard.stderr
    assert f"(tree: {REPO.resolve()}" in guard.stdout

    pointer.unlink()
    no_root = subprocess.run(
        [str(link_bin / "bd-guardcheck")], cwd=foreign, env=run_env,
        text=True, capture_output=True, timeout=10,
    )
    combined = no_root.stdout + no_root.stderr
    assert no_root.returncode == 2
    assert combined.count("BD-GATE-UNRUNNABLE") == 1
    assert "Traceback" not in combined


def test_installer_publishes_exact_population_and_removes_owned_stale_paths(tmp_path):
    """A successful update exposes exactly one source generation and public roster."""
    env, suite_bin, link_bin, _installed_env, _pointer = _installed_layout(tmp_path)
    source = tmp_path / "source-toolchain"
    shutil.copytree(REPO / "toolchain", source)
    env["BD_WORK_TREE"] = str(REPO)
    first = _install(env, source)
    assert first.returncode == 0, first.stderr

    old_bd = suite_bin / "bd"
    old_bytes = old_bd.read_bytes()
    (source / "bin" / "bd").write_bytes(old_bytes + b"\n# next generation\n")
    stale = suite_bin / "bd-stale-owned"
    stale.write_text("stale\n", encoding="utf-8")
    (link_bin / stale.name).symlink_to(stale)
    unrelated = link_bin / "operator-file"
    unrelated.write_text("keep\n", encoding="utf-8")

    updated = _install(env, source)
    assert updated.returncode == 0, updated.stdout + updated.stderr
    assert (suite_bin / "bd").read_bytes() != old_bytes
    assert not stale.exists()
    assert not (link_bin / stale.name).exists()
    assert unrelated.read_text(encoding="utf-8") == "keep\n"

    manifest = json.loads((suite_bin / ".bdsuite-manifest.json").read_text())
    source_names = sorted(
        path.name for path in (source / "bin").iterdir()
        if path.is_file() and not path.is_symlink()
    )
    installed_names = sorted(
        path.name for path in suite_bin.iterdir() if not path.name.startswith(".")
    )
    public_names = sorted(
        name for name in source_names
        if name == "bd" or name.startswith("bd-")
    )
    assert manifest["source_basenames"] == source_names
    assert manifest["public_commands"] == public_names
    assert installed_names == source_names
    assert sorted(path.name for path in link_bin.iterdir() if path.is_symlink()) == public_names


@pytest.mark.parametrize("command", ["cp", "chmod", "ln"])
def test_installer_failure_preserves_the_complete_old_install(tmp_path, command):
    """Handled staging/link failures return 2 and preserve every old owned path."""
    env, suite_bin, link_bin, _installed_env, _pointer = _installed_layout(tmp_path)
    source = tmp_path / "source-toolchain"
    shutil.copytree(REPO / "toolchain", source)
    env["BD_WORK_TREE"] = str(REPO)
    first = _install(env, source)
    assert first.returncode == 0, first.stderr
    before = _tree_snapshot(suite_bin, link_bin)

    (source / "bin" / "bd").write_bytes(
        (source / "bin" / "bd").read_bytes() + b"\n# replacement\n"
    )
    shim = _failure_shim(tmp_path, command)
    failed_env = {**env, "PATH": str(shim) + os.pathsep + env["PATH"]}
    failed = _install(failed_env, source)

    assert failed.returncode == 2, failed.stdout + failed.stderr
    assert "bdsuite installed" not in failed.stdout
    assert _tree_snapshot(suite_bin, link_bin) == before
    still_runs = _run_installed_bd(link_bin, env, tmp_path)
    assert still_runs.returncode == 0, still_runs.stdout + still_runs.stderr
    assert Path(still_runs.stdout).resolve() == REPO.resolve()


def test_installer_refuses_unrelated_public_collision_before_mutation(tmp_path):
    """An operator-owned public name is never overwritten by the installer."""
    env, suite_bin, link_bin, _installed_env, _pointer = _installed_layout(tmp_path)
    link_bin.mkdir(parents=True)
    collision = link_bin / "bd"
    collision.write_text("operator-owned\n", encoding="utf-8")
    before = _tree_snapshot(suite_bin, link_bin)
    refused = _install(env)
    assert refused.returncode == 2
    assert _tree_snapshot(suite_bin, link_bin) == before
    assert collision.read_text(encoding="utf-8") == "operator-owned\n"


def test_installer_rejects_a_staged_pointer_that_cannot_resolve(tmp_path):
    """Staged authority validation happens before the live generation changes."""
    env, suite_bin, link_bin, _installed_env, _pointer = _installed_layout(tmp_path)
    first = _install(env)
    assert first.returncode == 0, first.stderr
    before = _tree_snapshot(suite_bin, link_bin)

    source = tmp_path / "bad-toolchain"
    shutil.copytree(REPO / "toolchain", source)
    (source / "bin" / "_bd_work_tree.py").write_text(
        "#!/usr/bin/env python3\nraise SystemExit(2)\n", encoding="utf-8"
    )
    refused = _install(env, source)
    assert refused.returncode == 2
    assert _tree_snapshot(suite_bin, link_bin) == before


def test_shared_tool_defaults_are_repo_derived_and_operator_overridable(monkeypatch, tmp_path):
    """Bare use targets this checkout; BD_ROOT remains an explicit override."""
    monkeypatch.delenv("BD_ROOT", raising=False)
    baseline = _load_extensionless("bdtools_sec_baseline", BIN / "bdtools_sec.py")
    assert Path(baseline.DEFAULT_WORK).resolve() == REPO.resolve()

    alternate = tmp_path / "alternate"
    (alternate / "bulk_downloader").mkdir(parents=True, exist_ok=True)
    (alternate / "bulk_downloader" / "__init__.py").write_text("# control\n")
    monkeypatch.setenv("BD_ROOT", str(alternate))
    overridden = _load_extensionless("bdtools_sec_override", BIN / "bdtools_sec.py")
    assert Path(overridden.DEFAULT_WORK).resolve() == alternate.resolve()

    seen: list[str] = []
    overridden.load_state = lambda home: seen.append(str(home)) or None
    overridden.guard_paths()
    assert seen and Path(seen[0]).resolve() == alternate.resolve()


def test_bdenv_preserves_operator_paths_and_can_skip_optional_services(tmp_path):
    """Sourcing the environment must not overwrite operator-owned locations."""
    values = {
        "BD_HOME": tmp_path / "state",
        "PLAYWRIGHT_BROWSERS_PATH": tmp_path / "browsers",
        "GTK_ROOT": tmp_path / "gtk",
        "BD_WORK_TREE": REPO,
    }
    env = {**os.environ, **{key: str(value) for key, value in values.items()}}
    env["BD_ENV_NO_SERVICES"] = "1"
    code = (
        f". {REPO / 'toolchain' / 'bdenv.sh'}; "
        "printf '%s\\n' \"$BD_HOME\" \"$PLAYWRIGHT_BROWSERS_PATH\" "
        "\"$GTK_ROOT\" \"$BD_WORK_TREE\""
    )
    result = subprocess.run(
        ["bash", "-c", code], cwd=REPO, env=env, text=True,
        capture_output=True, timeout=10, check=True,
    )
    assert result.stdout.splitlines()[-4:] == [str(value) for value in values.values()]


def test_installed_bd_resolves_shared_env_and_validated_checkout(tmp_path):
    """The installer layout must execute through its public symlink."""
    env, _suite_bin, link_bin, installed_env, pointer = _installed_layout(tmp_path)
    installed = _install(env)
    assert installed.returncode == 0, installed.stderr

    assert installed_env.is_file()
    assert pointer.read_text(encoding="utf-8") == str(REPO.resolve()) + "\n"
    public_bd = link_bin / "bd"
    assert public_bd.is_symlink()
    assert public_bd.readlink() == (_suite_bin / "bd").resolve()
    result = _run_installed_bd(link_bin, env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout).resolve() == REPO.resolve()


def test_invalid_checkout_leaves_installed_layout_byte_identical(tmp_path):
    """Checkout refusal must happen before any copy, link, env, or pointer write."""
    env, suite_bin, link_bin, installed_env, pointer = _installed_layout(tmp_path)
    invalid = tmp_path / "not-a-checkout"
    invalid.mkdir()
    env["BD_WORK_TREE"] = str(invalid)

    artifacts = {
        suite_bin / "bd": b"old-tool\n",
        link_bin / "bd": b"old-public-entry\n",
        installed_env: b"old-env\n",
        pointer: b"old-pointer\n",
    }
    for path, content in artifacts.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    result = _install(env)
    assert result.returncode == 2
    assert "valid BD_WORK_TREE checkout" in result.stderr
    assert {path: path.read_bytes() for path in artifacts} == artifacts
    assert not (suite_bin / "bd-status").exists()
    assert not (link_bin / "bd-status").exists()


def test_installed_bd_fails_closed_when_checkout_pointer_is_missing_or_corrupt(tmp_path):
    """The installed pointer, not ambient authority, must supply the checkout."""
    env, _suite_bin, link_bin, _installed_env, pointer = _installed_layout(tmp_path)
    installed = _install(env)
    assert installed.returncode == 0, installed.stderr

    pointer.unlink()
    missing = _run_installed_bd(link_bin, env, tmp_path)
    assert missing.returncode == 2
    assert "BD-WORK-TREE-UNRUNNABLE" in missing.stderr

    pointer.write_text(str(tmp_path / "wrong-checkout") + "\n", encoding="utf-8")
    corrupt = _run_installed_bd(link_bin, env, tmp_path)
    assert corrupt.returncode == 2
    assert "BD-WORK-TREE-UNRUNNABLE" in corrupt.stderr


def test_witness_snapshot_requires_the_shipped_nonzero_population(tmp_path):
    """Snapshot discovery uses both shipped suites and refuses an empty denominator."""
    witness_drift = _load_extensionless("witness_drift_v1192", REPO / "tools" / "witness_drift.py")

    logdir = tmp_path / "logs"
    assert witness_drift.snapshot("1192", root=REPO, logdir=logdir) == 0
    payload = json.loads((logdir / "WITNESS_LOG_v1192.json").read_text())
    assert len(payload["sources"]) == 2
    assert len(payload["results"]) == 17

    empty = tmp_path / "empty-root"
    empty.mkdir()
    empty_logs = tmp_path / "empty-logs"
    assert witness_drift.snapshot("empty", root=empty, logdir=empty_logs) == 2
    assert not (empty_logs / "WITNESS_LOG_vempty.json").exists()


def test_bd_state_uses_explicit_local_first_precedence(tmp_path, monkeypatch):
    """Each higher state tier categorically outranks every lower tier."""
    state_root = tmp_path / "state-root"
    uploads = tmp_path / "uploads"
    cwd = tmp_path / "cwd"
    state_root.mkdir(); uploads.mkdir(); cwd.mkdir()
    monkeypatch.setenv("BD_STATE_ROOT", str(state_root))
    monkeypatch.setenv("BD_UPLOADS", str(uploads))
    state = _load_extensionless("bd_state_precedence", BIN / "bd-state")

    direct = state_root / "STATE.json"
    direct.write_text('{"marker":"direct"}', encoding="utf-8")
    _write_state_pack(state_root / "BulkDL_next_session_9.zip", "local-pack")
    _write_state_pack(uploads / "BulkDL_next_session_99.zip", "upload-pack")
    (uploads / "STATE.json").write_text('{"marker":"upload-loose"}', encoding="utf-8")
    (cwd / "STATE.json").write_text('{"marker":"cwd"}', encoding="utf-8")
    recursive = state_root / "nested" / "STATE.json"
    recursive.parent.mkdir()
    recursive.write_text('{"marker":"recursive"}', encoding="utf-8")
    monkeypatch.chdir(cwd)

    selected, label = state.find_state()
    assert json.loads(Path(selected).read_text())["marker"] == "direct"
    assert label.startswith("direct local state")
    direct.unlink()
    selected, label = state.find_state()
    assert json.loads(Path(selected).read_text())["marker"] == "local-pack"
    assert label.startswith("local session pack")
    (state_root / "BulkDL_next_session_9.zip").unlink()
    selected, label = state.find_state()
    assert json.loads(Path(selected).read_text())["marker"] == "upload-pack"
    assert label.startswith("upload session pack")
    (uploads / "BulkDL_next_session_99.zip").unlink()
    selected, label = state.find_state()
    assert json.loads(Path(selected).read_text())["marker"] == "upload-loose"
    assert label.startswith("loose upload state")
    (uploads / "STATE.json").unlink()
    selected, label = state.find_state()
    assert json.loads(Path(selected).read_text())["marker"] == "cwd"
    assert label.startswith("cwd state")
    (cwd / "STATE.json").unlink()
    selected, label = state.find_state()
    assert json.loads(Path(selected).read_text())["marker"] == "recursive"
    assert "WARN:" in label


def test_dependent_defaults_follow_root_and_explicit_values_win(tmp_path):
    """Root-controlled defaults are computed only after argument parsing."""
    consumer_agreement = _load_extensionless(
        "consumer_agreement_defaults", REPO / "tools" / "consumer_agreement.py"
    )
    seed_review_state = _load_extensionless(
        "seed_review_state_defaults", REPO / "tools" / "seed_review_state.py"
    )

    alternate = tmp_path / "alternate"
    explicit_db = tmp_path / "explicit.db"
    explicit_out = tmp_path / "explicit.json"
    explicit_contracts = tmp_path / "explicit-contracts.json"

    seeded = seed_review_state.parse_args(["--root", str(alternate)])
    assert Path(seeded.db) == alternate / "review" / "artifacts" / "KNOWLEDGE_GRAPH.db"
    assert Path(seeded.out) == alternate / "review" / "artifacts" / "REVIEW_STATE.json"
    seeded_explicit = seed_review_state.parse_args([
        "--root", str(alternate), "--db", str(explicit_db), "--out", str(explicit_out),
    ])
    assert Path(seeded_explicit.db) == explicit_db
    assert Path(seeded_explicit.out) == explicit_out

    consumer = consumer_agreement.parse_args(["--root", str(alternate)])
    assert Path(consumer.contracts) == alternate / "review" / "artifacts" / "CONTRACTS.json"
    consumer_explicit = consumer_agreement.parse_args([
        "--root", str(alternate), "--contracts", str(explicit_contracts),
    ])
    assert Path(consumer_explicit.contracts) == explicit_contracts


def test_consumer_agreement_check_has_no_cross_invocation_root_state(tmp_path):
    """Each check reads only its passed root and leaves module defaults immutable."""
    consumer_agreement = _load_extensionless(
        "consumer_agreement_isolation", REPO / "tools" / "consumer_agreement.py"
    )

    contracts = tmp_path / "contracts.json"
    contracts.write_text(json.dumps({"contracts": [{
        "id": "root-control", "symbol": "value", "file": "sample.py",
        "producers": ["producer"], "consumers_relying": [],
        "guard_signature": "SAFE_GUARD",
    }]}), encoding="utf-8")
    good = tmp_path / "good"; bad = tmp_path / "bad"
    good.mkdir(); bad.mkdir()
    (good / "sample.py").write_text(
        "def producer():\n    SAFE_GUARD = True\n    return SAFE_GUARD\n", encoding="utf-8"
    )
    (bad / "sample.py").write_text("def producer():\n    return True\n", encoding="utf-8")
    original = consumer_agreement.ROOT
    assert consumer_agreement.check(contracts, True, good) == 0
    assert consumer_agreement.check(contracts, True, bad) == 1
    assert consumer_agreement.ROOT == original


def test_current_operator_docs_name_the_supported_bootstrap_and_workspace():
    """Current instructions agree on the venv path and supported boot chain."""
    readme = (REPO / "project-knowledge" / "README.md").read_text(encoding="utf-8")
    workspace = readme.split("- **workspace**", 1)[1].split("- **Stack**", 1)[0]
    installer = readme.split("2. **Install the toolchain.**", 1)[1].split(
        "3. **The chain:**", 1
    )[0]
    assert "$BD_WORK/venv" in workspace
    assert "`work/venv`" not in workspace
    assert "zero" in installer.lower()
    assert "failed" in installer.lower()
    assert "symlinks by hand" not in installer

    guide = (REPO / "project-knowledge" / "RENDER_CAPTURE_AUDIT_GUIDE.md").read_text(
        encoding="utf-8"
    )
    prerequisites = guide.split("## 2. Prerequisites & bootstrap", 1)[1].split("## 3.", 1)[0]
    assert "bash /mnt/project/setup.sh" not in prerequisites
    assert "$BD_WORK" in prerequisites
    assert "bd-boot" in prerequisites
    assert "bd-preflight" in prerequisites
    assert "bd-state" in prerequisites


def test_consumer_oracle_accepts_the_explicit_repository_root(tmp_path):
    """The wrapper validates inputs inside context.repo_root without identity gates."""
    contracts = REPO / "contracts-row161.json"
    contracts.write_text(
        json.dumps({"contracts": [{"file": "bulk_downloader/__init__.py"}]}),
        encoding="utf-8",
    )
    try:
        context = AdapterContext(
            REPO, tmp_path / "artifacts", tmp_path / "corpus", 17,
            AdapterBudget(5.0, 1, 64_000),
        )
        adapter = CommandOracleAdapter(
            "consumer-agreement", "tools/consumer_agreement.py", "--gate"
        )
        command = adapter._command(
            AdapterCase("portable-root", {"contracts": contracts.name}),
            context,
            REPO / "tools" / "consumer_agreement.py",
        )
    finally:
        contracts.unlink(missing_ok=True)
    assert command[-5:] == [
        "--contracts", str(contracts), "--root", str(REPO), "--gate"
    ]
