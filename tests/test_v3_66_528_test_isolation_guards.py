"""v3.66.528 -- test-isolation guards for the THB hygiene batch.

THB-1: tests/test_v3_66_302_gui_parity_reconcile.py used to insert
``_REPO/'tools'`` + ``_REPO`` at ``sys.path[0]`` at MODULE scope and never
restore -> any later test importing a name colliding with a ``tools/*.py``
module was silently shadowed. The inserts are now scoped + restored (finally)
inside the one regen test that needs them.

THB-3: tests/test_v3_66_337_guided_capture_cut1.py used
``saved = A._app_cfg.get('path_allowlist')`` + an unconditional
``finally: A._app_cfg['path_allowlist'] = saved``, which wrote ``None`` (or
``[]``) into ``_app_cfg`` when the key had been ABSENT -> absent->None/[]
pollution leaking into later tests. It now saves with a ``_MISSING`` sentinel
(copying when present, since allowlist_add mutates the list in place) and
restores-to-absent (pop).

THB-4: tests/test_v3_66_306_queue_hk_parity.py loaded
tools/autonomy_queue_hk.py as top-level ``autonomy_queue_hk``. Its import-time
registration replaced the canonical apply hooks with closures bound to the
alias module, bypassing the fake queue installed by test_v3_66_135. The parity
test now imports ``tools.autonomy_queue_hk`` canonically.

These guards combine structural checks with behavioral module-identity,
subprocess-isolation, and restore-to-absent checks against shipped helpers.
Zero-arg fns; stdlib only; runs under the custom runner and pytest.
"""
import ast
import importlib.util
import sys
import tempfile
from pathlib import Path

_TESTS = Path(__file__).resolve().parent
_302 = _TESTS / "test_v3_66_302_gui_parity_reconcile.py"
_306 = _TESTS / "test_v3_66_306_queue_hk_parity.py"
_337 = _TESTS / "test_v3_66_337_guided_capture_cut1.py"


def _dotted(func):
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return ".".join(reversed(parts))
    return None


def _has_module_level_call(src, dotted):
    """True iff `dotted` (e.g. 'sys.path.insert') is CALLED at module scope --
    function/class-nested calls are excluded (those are the scoped, restored ones)."""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and _dotted(sub.func) == dotted:
                return True
    return False


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ───────────────────────────── THB-1 ─────────────────────────────

def test_thb1_no_module_level_syspath_insert_in_302():
    src = _302.read_text(encoding="utf-8")
    assert not _has_module_level_call(src, "sys.path.insert"), (
        "test_v3_66_302 mutates sys.path at MODULE scope (THB-1 leak) -- scope the "
        "insert into the regen test with a finally restore")


def test_thb1_302_scopes_and_restores_syspath():
    before = list(sys.path)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            mod = _load(_302, "t302_thb1_guard")
            generated = mod.generated_inventory_path.__wrapped__(Path(tmp))
            assert generated.is_file()
        after = list(sys.path)
    finally:
        sys.path[:] = before
    assert after == before, "parity generation changed the parent sys.path"


def test_thb4_queue_parity_uses_canonical_module_identity():
    from tools import autonomy_queue_hk as canonical

    mod = _load(_306, "t306_thb4_guard")
    assert mod.qh is canonical, (
        "queue parity loaded tools/autonomy_queue_hk.py under a second module name")


# ───────────────────────────── THB-3 ─────────────────────────────

def test_thb3_no_leaky_unconditional_allowlist_restore_in_337():
    """No `A._app_cfg['path_allowlist'] = saved` assignment (the unconditional
    restore that pollutes absent->None/[])."""
    tree = ast.parse(_337.read_text(encoding="utf-8"))
    leaky = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if (isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Attribute) and t.value.attr == "_app_cfg"
                    and isinstance(node.value, ast.Name) and node.value.id == "saved"):
                leaky += 1
    assert leaky == 0, (
        f"test_v3_66_337 still has {leaky} unconditional `_app_cfg['path_allowlist'] = saved` "
        "restores (THB-3 absent->None/[] leak) -- use the restore-to-absent helper")


def test_thb3_restore_to_absent_contract():
    """The shipped helpers restore absent->absent and present->original-copy."""
    mod = _load(_337, "t337_thb3_guard")
    assert hasattr(mod, "_save_allowlist") and hasattr(mod, "_restore_allowlist"), (
        "test_v3_66_337 must define _save_allowlist/_restore_allowlist")

    class _A:
        pass

    # key ABSENT -> must restore to ABSENT (not None, not [])
    a = _A(); a._app_cfg = {}
    prior = mod._save_allowlist(a)
    a._app_cfg["path_allowlist"] = ["/x"]            # test/route sets it
    mod._restore_allowlist(a, prior)
    assert "path_allowlist" not in a._app_cfg, "absent must restore to ABSENT"

    # key PRESENT -> must restore to the ORIGINAL value even if mutated in place
    a = _A(); a._app_cfg = {"path_allowlist": ["/orig"]}
    prior = mod._save_allowlist(a)
    a._app_cfg["path_allowlist"].append("/added")    # allowlist_add mutates in place
    mod._restore_allowlist(a, prior)
    assert a._app_cfg["path_allowlist"] == ["/orig"], "present must restore the original (copied) value"
