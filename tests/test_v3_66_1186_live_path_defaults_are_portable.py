"""RED-first behavior controls for row 161's live-path phase."""
from __future__ import annotations

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
        "BD_WORK_TREE": tmp_path / "tree",
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
