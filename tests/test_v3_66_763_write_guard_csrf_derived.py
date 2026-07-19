"""v3.66.763 -- audit_write_route_guard must DERIVE the CSRF policy, not re-type it.

DERIVE-AUDIT (HIGH): tools/audit_write_route_guard.py hand-typed three constants
that mirror the app's real CSRF guard -- WRITE_METHODS, GUARDED_PREFIXES,
KNOWN_API_EXEMPT -- with a "must mirror the gate" comment. build_endpoint_catalog.py
was already fixed @748 to bind these from bulk_downloader.app (identity, not copies)
after ONE of its copies drifted and reported 28 cockpit writes as csrf:false. This
tool is the sibling mirror that fix missed. A CSRF-coverage auditor that re-types
the policy it audits is wrong in the reassuring direction: it can pass while the
real guard has moved.

RED-first on pristine: the literals exist (test 1 fails) and there is no
_bind_csrf_policy (test 2 raises AttributeError).
"""
import ast
import importlib.util
import os
import pathlib

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "audit_write_route_guard.py"
_NAMES = ("WRITE_METHODS", "GUARDED_PREFIXES", "KNOWN_API_EXEMPT")


def test_csrf_constants_are_not_re_typed_literals():
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    literal = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Name) and t.id in _NAMES
                        and isinstance(n.value, (ast.Tuple, ast.Set, ast.Dict, ast.List))):
                    literal[t.id] = type(n.value).__name__
    assert not literal, (
        "re-typed CSRF policy constants %s -- bind them from bulk_downloader.app "
        "(CSRF_TRIPPING_METHODS / CSRF_GUARDED_PREFIXES / CSRF_EXEMPT_PATHS) the way "
        "build_endpoint_catalog.py does, so the auditor cannot drift from the gate." % literal)


def _load_tool():
    os.environ["BD_DISABLE_KEEPALIVE"] = "1"
    spec = importlib.util.spec_from_file_location("_awrg_under_test", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_constants_bind_to_the_apps_own_objects():
    m = _load_tool()
    m._bind_csrf_policy()  # AttributeError on pristine (no such function) -> RED
    import bulk_downloader.app as A
    assert m.WRITE_METHODS is A.CSRF_TRIPPING_METHODS
    assert m.GUARDED_PREFIXES is A.CSRF_GUARDED_PREFIXES
    assert m.KNOWN_API_EXEMPT is A.CSRF_EXEMPT_PATHS


def test_the_guard_predicate_still_works_after_binding():
    # Behavior-neutral: collect_write_routes runs and every escape (if any) is a
    # real off-/api/ write route, not an artifact of an unbound constant.
    m = _load_tool()
    write_routes, escapes = m.collect_write_routes()
    assert isinstance(write_routes, list) and len(write_routes) > 0
    for rule, _methods in escapes:
        assert not rule.startswith(("/api/", "/cockpit/api/"))
