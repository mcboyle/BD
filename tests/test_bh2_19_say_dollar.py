from __future__ import annotations

import os
import sys
import types
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = os.environ.get("BD_BH2_19_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


def _get_cli_module() -> Any:
    cand_path = Path(CANDIDATE)
    if cand_path.is_dir():
        file_path = cand_path / "cli.py"
    else:
        file_path = cand_path
    assert file_path.is_file(), f"cli.py not found: {file_path}"

    if "server" not in sys.modules:
        s: Any = types.ModuleType("server")
        s.premise_verify = lambda *a, **k: {"verdict": "PROCEED"}
        s.say_validate = lambda *a, **k: {"valid": True}
        s.say_validate_batch = lambda items, *a, **k: [{"valid": True} for _ in items]
        sys.modules["server"] = s

    loader = SourceFileLoader(f"cli_{file_path.stat().st_mtime_ns}", str(file_path))
    return loader.load_module()


def test_double_quoted_escaped_dollar_allowed() -> None:
    cli = _get_cli_module()
    cmd = r'bd-say target "Cost is \$0"'
    res = cli.hook(cmd)
    assert res == [{"valid": True}]


def test_single_quoted_dollar_allowed() -> None:
    cli = _get_cli_module()
    cmd = "bd-say target 'Cost is $0'"
    res = cli.hook(cmd)
    assert res == [{"valid": True}]


def test_unquoted_escaped_dollar_allowed() -> None:
    cli = _get_cli_module()
    cmd = r"bd-say target Cost\ is\ \$0"
    res = cli.hook(cmd)
    assert res == [{"valid": True}]


def test_double_quoted_escaped_backtick_allowed() -> None:
    cli = _get_cli_module()
    cmd = r'bd-say target "Run \`whoami\` test"'
    res = cli.hook(cmd)
    assert res == [{"valid": True}]


def test_double_quoted_unescaped_dollar_refused() -> None:
    cli = _get_cli_module()
    cmd = 'bd-say target "Cost is $0"'
    with pytest.raises(ValueError, match="dynamic argument"):
        cli.hook(cmd)


def test_double_quoted_subshell_refused() -> None:
    cli = _get_cli_module()
    cmd = 'bd-say target "Cost is $(whoami)"'
    with pytest.raises(ValueError, match="dynamic argument"):
        cli.hook(cmd)


def test_double_quoted_unescaped_backtick_refused() -> None:
    cli = _get_cli_module()
    cmd = 'bd-say target "Cost is `whoami`"'
    with pytest.raises(ValueError, match="dynamic argument"):
        cli.hook(cmd)


def test_unquoted_unescaped_dollar_refused() -> None:
    cli = _get_cli_module()
    cmd = r"bd-say target Cost\ is\ $0"
    with pytest.raises(ValueError, match="dynamic argument"):
        cli.hook(cmd)
