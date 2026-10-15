from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = ROOT / "docs"
CLI_REF_DOC = DOCS_DIR / "CLI_REFERENCE.md"
README_DOC = ROOT / "README.md"

def _extract_argparse_flags_from_ast(py_path: Path) -> set[str]:
    tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
    flags: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_add_arg = False
        if isinstance(func, ast.Attribute) and func.attr == "add_argument":
            is_add_arg = True
        if not is_add_arg:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("-"):
                flags.add(arg.value)
    return flags

def _extract_documented_flags(doc_text: str, section_header: str) -> set[str]:
    lines = doc_text.splitlines()
    in_section = False
    section_lines: list[str] = []
    for line in lines:
        if line.startswith("#"):
            if section_header.lower() in line.lower():
                in_section = True
                continue
            elif in_section and line.startswith("##"):
                break
        if in_section:
            section_lines.append(line)
    section_text = "\n".join(section_lines)
    documented_flags: set[str] = set()
    for token in section_text.split():
        clean = token.strip("`*:,()[]")
        if clean.startswith("-") and len(clean) > 1 and (not clean.startswith("---")):
            documented_flags.add(clean)
    return documented_flags

def test_cli_reference_document_exists() -> None:
    assert CLI_REF_DOC.exists(), f"Missing required reference: {CLI_REF_DOC}"

def test_ghost_entry_points_explicitly_documented() -> None:
    main_py = ROOT / "bulk_downloader" / "__main__.py"
    cli_py = ROOT / "cli.py"
    assert not main_py.exists(), "Unexpected file: bulk_downloader/__main__.py"
    assert not cli_py.exists(), "Unexpected file: cli.py"
    readme_text = README_DOC.read_text(encoding="utf-8")
    assert "downloader_ui.py" in readme_text, "README must document downloader_ui.py"
    assert "cli_dashboard" in readme_text, "README must document cli_dashboard"
    if CLI_REF_DOC.exists():
        ref_text = CLI_REF_DOC.read_text(encoding="utf-8")
        assert "downloader_ui.py" in ref_text
        assert "cli_dashboard" in ref_text

def test_ghost_tool_tasktracker_gen_retired() -> None:
    tt_tool = ROOT / "tools" / "tasktracker_gen.py"
    assert not tt_tool.exists(), "tools/tasktracker_gen.py should not exist on disk"
    proc_conv = DOCS_DIR / "PROCESS_CONVENTIONS.md"
    if proc_conv.exists():
        text = proc_conv.read_text(encoding="utf-8")
        assert "python3 tools/tasktracker_gen.py" not in text, "tools/tasktracker_gen.py is retired and must not be invoked"
        assert "retired" in text.lower()

def test_framework_subcommands_truthfulness() -> None:
    ops_wf = DOCS_DIR / "framework" / "OPERATIONS_WORKFLOWS.md"
    if ops_wf.exists():
        text = ops_wf.read_text(encoding="utf-8")
        assert "validation_harness.py capquality" not in text, "capquality belongs to trust_intelligence.py, not validation_harness.py"
        assert "trust_intelligence.py capquality" in text, "trust_intelligence.py capquality should be documented"
        assert "*_USAGE.md" not in text, "Phantom *_USAGE.md references must be removed"
        assert "PARALLEL_CAPTURE_USAGE.md" not in text, "Phantom PARALLEL_CAPTURE_USAGE.md references must be removed"
    phases_map = DOCS_DIR / "framework" / "PHASES_31_40_MAP.md"
    if phases_map.exists():
        text = phases_map.read_text(encoding="utf-8")
        assert "ops_intelligence.py family" not in text, "family subcommand belongs to trust_intelligence.py, not ops_intelligence.py"
        assert "trust_intelligence.py family" in text, "trust_intelligence.py family must be documented"
        assert "operator_layer.py sustainability" not in text, "sustainability subcommand belongs to meta_intelligence.py, not operator_layer.py"
        assert "meta_intelligence.py sustainability" in text, "meta_intelligence.py sustainability must be documented"

def test_cli_dashboard_flags_parity() -> None:
    assert CLI_REF_DOC.exists(), f"Missing required reference: {CLI_REF_DOC}"
    ref_text = CLI_REF_DOC.read_text(encoding="utf-8")
    dash_py = ROOT / "bulk_downloader" / "cli_dashboard.py"
    dash_flags = _extract_argparse_flags_from_ast(dash_py)
    assert "--api" in dash_flags
    assert "--once" in dash_flags
    assert "--refresh" in dash_flags
    doc_flags = _extract_documented_flags(ref_text, "cli_dashboard")
    for flag in dash_flags:
        assert flag in doc_flags, f"Flag {flag} from cli_dashboard.py is missing from CLI_REFERENCE.md"

def test_analysis_frontends_flags_parity() -> None:
    assert CLI_REF_DOC.exists(), f"Missing required reference: {CLI_REF_DOC}"
    ref_text = CLI_REF_DOC.read_text(encoding="utf-8")
    targets = {"semantic_diff.py": ROOT / "tools" / "semantic_diff.py", "reachability.py": ROOT / "tools" / "reachability.py", "differential_oracle.py": ROOT / "tools" / "differential_oracle.py", "fuzz_harness.py": ROOT / "tools" / "fuzz_harness.py"}
    for name, path in targets.items():
        actual_flags = _extract_argparse_flags_from_ast(path)
        doc_flags = _extract_documented_flags(ref_text, name)
        for flag in actual_flags:
            assert flag in doc_flags, f"Flag {flag} from {name} is missing from CLI_REFERENCE.md"

def test_canonical_cli_help_execution() -> None:
    commands = [[sys.executable, "-m", "bulk_downloader.cli_dashboard", "--help"], [sys.executable, "tools/validation_harness.py", "--help"], [sys.executable, "tools/trust_intelligence.py", "--help"], [sys.executable, "tools/ops_intelligence.py", "--help"], [sys.executable, "tools/meta_intelligence.py", "--help"], [sys.executable, "tools/operator_layer.py", "--help"], [sys.executable, "tools/plugin_lint.py", "--help"]]
    for cmd in commands:
        res = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False, timeout=15)
        assert res.returncode == 0, f"Command {cmd} failed with stderr: {res.stderr}"
        assert "usage:" in res.stdout.lower()
