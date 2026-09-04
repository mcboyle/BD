"""Row 698: corpus guards are classified by execution, never source presence."""

from __future__ import annotations

import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


BD_GATE_SCOPE = "repo-wide"

_REPO = Path(__file__).resolve().parents[1]
_BIN = _REPO / "toolchain" / "bin"


def _load_tool(name: str, module_name: str):
    loader = importlib.machinery.SourceFileLoader(
        module_name, str(_BIN / name)
    )
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _tool_source(body: str, marker: str = "") -> str:
    return (
        "#!/usr/bin/env python3\n"
        "import argparse, os, sys\n"
        f"sys.path.insert(0, {str(_BIN)!r})\n"
        "import bdtools_sec\n"
        f"{marker}"
        "def record(name):\n"
        "    with open(os.environ['ROW698_FIRED_LOG'], 'a', encoding='ascii') as fh:\n"
        "        fh.write(name + '\\n')\n"
        f"{body}"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n"
        "# --json\n"
    )


def test_row698_guard_roster_executes_and_reports_four_distinct_states(
    tmp_path, monkeypatch
):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fired_log = tmp_path / "fired.log"
    monkeypatch.setenv("ROW698_FIRED_LOG", str(fired_log))

    sources = {
        "bd-dead-guard": _tool_source(
            "def unused(path):\n"
            "    bdtools_sec.require_corpus(path, label='--tree')\n"
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    args = ap.parse_args()\n"
            "    record('dead')\n"
            "    return 0\n"
        ),
        "bd-live-guard": _tool_source(
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    args = ap.parse_args()\n"
            "    record('live')\n"
            "    bdtools_sec.require_corpus(args.tree, label='--tree')\n"
            "    return 0\n"
        ),
        "bd-comment-only": _tool_source(
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    ap.parse_args()\n"
            "    record('comment')\n"
            "    return 0\n",
            "# lint: corpus-guard-ok -- fixture uses a reviewed alternate guard\n",
        ),
        "bd-reasonless-optout": _tool_source(
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    ap.parse_args()\n"
            "    record('reasonless')\n"
            "    return 0\n",
            "# lint: corpus-guard-ok\n",
        ),
        "bd-string-decoy": _tool_source(
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    ap.parse_args()\n"
            "    example = '# lint: corpus-guard-ok -- not a comment'\n"
            "    record('string')\n"
            "    return 0\n"
        ),
        "bd-cut": _tool_source(
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    ap.parse_args()\n"
            "    record('forbidden')\n"
            "    return 0\n"
        ),
    }
    for name, source in sources.items():
        (bindir / name).write_text(source, encoding="ascii")

    module = _load_tool("bd-tool-lint", "row698_bd_tool_lint")
    result = module.run(str(bindir), do_runtime=True)
    by_tool = {row["tool"]: row for row in result["results"]}
    assert sorted(by_tool) == sorted(sources)
    assert len(by_tool) == 6
    assert result["corpus_debt"]["with_corpus_arg"] == 6

    dead_errors = [
        error
        for error in by_tool["bd-dead-guard"]["errors"]
        if "corpus-arg-unguarded" in error
    ]
    assert len(dead_errors) == 1, (
        "dead guard was classified as guarded without executing: "
        f"{dead_errors!r}"
    )

    states = {row["tool"]: row["corpus_state"] for row in result["results"]}
    assert states == {
        "bd-comment-only": "opted-out",
        "bd-cut": "unknown",
        "bd-dead-guard": "unguarded",
        "bd-live-guard": "guarded",
        "bd-reasonless-optout": "unknown",
        "bd-string-decoy": "unguarded",
    }
    with pytest.raises(
        AssertionError, match="negative control expected guarded, got unguarded"
    ):
        assert states["bd-dead-guard"] == "guarded", (
            "negative control expected guarded, got "
            f"{states['bd-dead-guard']}"
        )
    debt = result["corpus_debt"]
    assert debt["guarded"] == ["bd-live-guard"]
    assert debt["guarded_count"] == 1
    assert debt["opted_out"] == ["bd-comment-only"]
    assert debt["opted_out_count"] == 1
    assert debt["unguarded_count"] == 2
    assert debt["unknown"] == ["bd-cut", "bd-reasonless-optout"]
    assert debt["unknown_count"] == 2

    invalid = [
        error
        for error in by_tool["bd-reasonless-optout"]["errors"]
        if "corpus-opt-out-invalid" in error
    ]
    assert len(invalid) == 1
    assert "requires a non-empty reason" in invalid[0]
    assert fired_log.read_text(encoding="ascii").splitlines() == [
        "dead", "live", "string"
    ]
    assert len(fired_log.read_text(encoding="ascii").splitlines()) == 3


def test_row698_bd_golden_keeps_the_tree_derived_flag_seam():
    source = (
        "import argparse\n"
        "def main():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument('--tree')\n"
    )
    assert source.count("parser.add_argument('--tree')") == 1
    module = _load_tool("bd-golden", "row698_bd_golden")
    invocation = module.corpus_invocation("bd-fixture", "/missing-fixture", source)
    assert invocation == ["--tree", "/missing-fixture"]
    assert len(invocation) == 2


def test_row698_bd_sweep_executes_the_shared_profile_flag(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fired_log = tmp_path / "sweep-fired.log"
    monkeypatch.setenv("ROW698_FIRED_LOG", str(fired_log))
    tool = bindir / "bd-live-guard"
    tool.write_text(
        _tool_source(
            "def main():\n"
            "    ap = argparse.ArgumentParser()\n"
            "    ap.add_argument('--tree')\n"
            "    args = ap.parse_args()\n"
            "    record('sweep')\n"
            "    bdtools_sec.require_corpus(args.tree, label='--tree')\n"
            "    return 0\n"
        ),
        encoding="ascii",
    )
    tool.chmod(0o755)
    ghost = tmp_path / "ghost"
    assert not ghost.exists()

    module = _load_tool("bd-sweep", "row698_bd_sweep")
    rows = module.corpus_cases(
        str(bindir), tool.name, 5, [("absent", str(ghost))]
    )
    assert len(rows) == 1
    assert rows[0][:4] == (
        "bd-live-guard",
        "--tree absent",
        module.PASS,
        "refused (rc=2)",
    )
    assert fired_log.read_text(encoding="ascii").splitlines() == ["sweep"]


def test_row698_transform_control_imports_without_asserting_guard_behavior():
    _load_tool("bd-tool-lint", "row698_bd_tool_lint_control")
