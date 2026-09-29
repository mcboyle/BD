"""IA-12 -- the dev tools' third-party imports are declared in the dev manifest.

tools/cockpit_console.py and tools/framework_dashboard.py import markdown, which
arrived only transitively through apprise (a runtime dep that could drop it any
release); tools/code_intelligence/fuzz_service.py imports hypothesis, which no
manifest named, so `pip install -r requirements-dev.txt` left the fuzz service
unable to run. atheris (tools/fuzz_probe.py) stays undeclared on purpose: it is
a best-effort, ImportError-guarded probe that needs a clang toolchain to build.

The import sites are found by an AST walk of tools/ (so the test fails if the
census cannot see them), and the declared side is requirements-dev.txt with its
`-r` includes followed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
# import root -> distribution name
_TOOL_DEPS = {"markdown": "markdown", "hypothesis": "hypothesis"}


def _declared(manifest: Path, seen=None) -> set:
    seen = set() if seen is None else seen
    if manifest in seen:
        return set()
    seen.add(manifest)
    names = set()
    for raw in manifest.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("-r "):
            names |= _declared(manifest.parent / line[3:].strip(), seen)
            continue
        if not line or line.startswith("-"):
            continue
        m = _NAME.match(line)
        if m:
            names.add(m.group(1).lower().replace("_", "-"))
    return names


def _tool_import_sites(root: str) -> list:
    sites = []
    for path in sorted((_REPO / "tools").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            if any(n.split(".")[0] == root for n in names):
                sites.append(str(path.relative_to(_REPO)))
                break
    return sites


def test_markdown_and_hypothesis_are_declared_for_the_tools_that_import_them():
    declared = _declared(_REPO / "requirements-dev.txt")
    assert "pyinstaller" in declared and "flask" in declared, (
        "manifest walk broken: dev and (via -r) runtime pins must both be seen"
    )
    missing = {}
    for root, dist in _TOOL_DEPS.items():
        sites = _tool_import_sites(root)
        assert sites, f"import census found no tools/ import of {root} -- probe cannot say yes"
        if dist not in declared:
            missing[dist] = sites
    assert missing == {}, (
        f"IA-12: dev tools import undeclared distributions: {missing} "
        "(requirements-dev.txt and its -r includes)"
    )
