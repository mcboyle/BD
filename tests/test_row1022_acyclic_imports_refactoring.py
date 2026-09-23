"""Row 1022 -- eliminate the reflection getter back-edge from app_template to app_state.

WHY THIS GATE EXISTS (Row 1022, PLAN-2040; bounce of P4-B REFUTE on tree 00b97ab3):
app_template reached app_state through importlib reflection
(getattr(import_module("bulk_downloader.app_state"), name)). Nothing app_state imports
(transitively, statically) reaches app_template or app, so that lazy accessor guards
no cycle: it only hides a real
edge from tools/decomp/import_graph_gate.py (H-14 lazy-accessor sprawl). The fix
imports app_state statically and keeps the getters BY REFERENCE, FRESH PER CALL.

WHAT THIS GATE ASSERTS:
1. The getters follow a rebinding of app_state.<name> (no cached/stale value).
2. app_template has no reflection edge to app_state; it imports it statically.
3. The static import closure of app_state never reaches app_template or app, so the new
   static edge is acyclic;
   importing app_template in a fresh interpreter does not pull in bulk_downloader.app.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

# Its subject is one module's import edges (app_template -> app_state).
BD_GATE_SCOPE = "module"

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "bulk_downloader"


def _internal_imports(path: Path) -> list[str]:
    """Every bulk_downloader-internal import in a module (any nesting), as dotted names."""
    names = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").startswith("bulk_downloader"):
                base = node.module or ""
                names.extend(f"{base}.{a.name}".strip(".") if not base else base for a in node.names)
        elif isinstance(node, ast.Import):
            names.extend(a.name for a in node.names if a.name.startswith("bulk_downloader"))
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.append(f"reflect:{node.args[0].value}")
    return names


@pytest.mark.parametrize(
    "getter, name",
    [("_app_runners", "runners"), ("_app_s_cfg", "s_cfg"), ("_app_s_meta", "s_meta")],
)
def test_getter_follows_rebinding_of_app_state(monkeypatch, getter, name):
    """E1: by reference, fresh per call -- a rebound app_state.<name> is what the getter returns."""
    from bulk_downloader import app_state, app_template

    first = getattr(app_template, getter)()
    assert first is getattr(app_state, name)
    rebound = object()
    monkeypatch.setattr(app_state, name, rebound)
    assert getattr(app_template, getter)() is rebound


def test_app_template_has_no_reflection_edge_to_app_state():
    """E2: the app_state edge is a static import, not importlib reflection or an indirection module."""
    imports = _internal_imports(PKG / "app_template.py")
    assert "reflect:bulk_downloader.app_state" not in imports
    assert not [i for i in imports if "acyclic_imports" in i]
    tree = ast.parse((PKG / "app_template.py").read_text(encoding="utf-8"))
    top_level = [
        a.name for n in tree.body if isinstance(n, ast.ImportFrom) and n.level == 1 and not n.module
        for a in n.names
    ]
    assert "app_state" in top_level


def _module_path(dotted: str) -> Path | None:
    rel = Path(*dotted.split("."))
    for cand in (ROOT / rel.with_suffix(".py"), ROOT / rel / "__init__.py"):
        if cand.is_file():
            return cand
    return None


def _static_targets(path: Path) -> set[str]:
    """Package modules a file imports with an import STATEMENT at any nesting (reflection excluded)."""
    pkg = list(path.relative_to(ROOT).parts[:-1])  # the file's package (also for __init__.py)
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - (node.level - 1)]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            if not mod.startswith("bulk_downloader"):
                continue
            out.add(mod)
            out.update(f"{mod}.{a.name}" for a in node.names if _module_path(f"{mod}.{a.name}"))
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("bulk_downloader"))
    return {m for m in out if _module_path(m)}


def _closure(start: str) -> set[str]:
    seen, todo = set(), [start]
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        todo.extend(_static_targets(_module_path(mod)) - seen)
    return seen


def test_app_state_closure_never_reaches_app_template_or_app():
    """E2 (re-scoped per N2-B E1): app_state is not a leaf on main (it imports lock_monitor), but its
    static import closure reaches neither app_template nor app, so the new edge closes no cycle."""
    closure = _closure("bulk_downloader.app_state")
    assert "bulk_downloader.lock_monitor" in closure  # the measured non-leaf edge is seen
    assert "bulk_downloader.app_template" not in closure
    assert "bulk_downloader.app" not in closure
    # Positive control (rule 7): the same walk DOES find a real path -- app_template reaches app_state.
    assert "bulk_downloader.app_state" in _closure("bulk_downloader.app_template")


def test_import_app_template_fresh_does_not_pull_in_app():
    """E2: a fresh interpreter importing app_template loads app_state and not app (no cycle entered)."""
    code = (
        "import sys, bulk_downloader.app_template as t;"
        "print('app_state' if 'bulk_downloader.app_state' in sys.modules else 'no-app_state');"
        "print('app' if 'bulk_downloader.app' in sys.modules else 'no-app')"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.split() == ["app_state", "no-app"]
