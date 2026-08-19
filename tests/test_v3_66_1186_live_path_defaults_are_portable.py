"""RED-first behavior controls for row 161's live-path phase."""
from __future__ import annotations

BD_GATE_SCOPE = "module"

import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from tools.code_intelligence.adapters import AdapterBudget, AdapterCase, AdapterContext
from tools.code_intelligence.oracle_adapters import CommandOracleAdapter


REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "toolchain" / "bin"


def _installed_layout(tmp_path: Path) -> tuple[dict[str, str], Path, Path, Path, Path]:
    home = tmp_path / "home"
    suite_bin = home / ".local" / "bin"
    link_bin = tmp_path / "usr-local-bin"
    installed_env = home / ".local" / "bdenv.sh"
    pointer = home / ".local" / ".bd-work-tree"
    env = {key: value for key, value in os.environ.items() if not key.startswith("BD_")}
    env.update({
        "HOME": str(home),
        "BD_SUITE_BIN": str(suite_bin),
        "BD_SUITE_LINK_BIN": str(link_bin),
    })
    return env, suite_bin, link_bin, installed_env, pointer


def _install(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REPO / "toolchain" / "install_bdsuite.sh")],
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


def test_shared_tool_defaults_are_repo_derived_and_operator_overridable(monkeypatch):
    """Bare use targets this checkout; BD_ROOT remains an explicit override."""
    monkeypatch.delenv("BD_ROOT", raising=False)
    baseline = _load_extensionless("bdtools_sec_baseline", BIN / "bdtools_sec.py")
    assert Path(baseline.DEFAULT_WORK).resolve() == REPO.resolve()

    monkeypatch.setenv("BD_ROOT", str(REPO))
    overridden = _load_extensionless("bdtools_sec_override", BIN / "bdtools_sec.py")
    assert Path(overridden.DEFAULT_WORK).resolve() == REPO.resolve()

    seen: list[str] = []
    overridden.load_state = lambda home: seen.append(str(home)) or None
    overridden.guard_paths()
    assert seen and Path(seen[0]).resolve() == REPO.resolve()


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
    assert "BD_WORK_TREE is not a Git checkout" in missing.stderr

    pointer.write_text(str(tmp_path / "wrong-checkout") + "\n", encoding="utf-8")
    corrupt = _run_installed_bd(link_bin, env, tmp_path)
    assert corrupt.returncode == 2
    assert "BD_WORK_TREE is not a Git checkout" in corrupt.stderr


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
