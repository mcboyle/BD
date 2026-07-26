import importlib.machinery
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
SCANNER = ROOT / "toolchain" / "bin" / "bd-defect-scan"


def _load_scanner():
    loader = importlib.machinery.SourceFileLoader("bd_defect_scan_precision", str(SCANNER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _hits(scanner, relative, dp):
    source = (ROOT / relative).read_text(encoding="utf-8")
    return scanner.scan_file(relative, source, only={dp})


@pytest.fixture(scope="module")
def scanner():
    return _load_scanner()


@pytest.mark.parametrize(("dp", "relative"), [
    pytest.param("DP-03", "tools/graph_build.py", id="dp03-graph-build"),
    pytest.param("DP-08", "tools/code_intelligence/adapters.py", id="dp08-adapters"),
    pytest.param("DP-10", "tools/coverage_map.py", id="dp10-coverage-map"),
    pytest.param("DP-10", "tools/l0_extract.py", id="dp10-l0-extract"),
])
def test_actual_file_false_positives_are_silent(scanner, dp, relative):
    assert _hits(scanner, relative, dp) == []


def test_dp03_detects_post_conversion_bounds_without_a_finite_guard(scanner):
    source = """\
def post_conversion_bounds(v):
    n = float(v)
    if n < 0 or n > 1:
        raise ValueError
    return n
"""

    assert len(scanner.scan_file("example.py", source, only={"DP-03"})) == 1


def test_dp08_detects_divergent_equivalent_redaction_surfaces(scanner):
    source = """\
_SECRET_QUERY_KEYS = {"token", "code"}
_SECRET_KV_KEYS = {"token"}
"""

    assert len(scanner.scan_file("redaction.py", source, only={"DP-08"})) == 1


def test_dp10_detects_interpolated_sql_table_name(scanner):
    source = """\
def query(connection, table):
    return connection.execute(f"SELECT * FROM {table}")
"""

    assert len(scanner.scan_file("query.py", source, only={"DP-10"})) == 1


def test_dp10_allows_allowlisted_sql_table_name(scanner):
    source = """\
ALLOWED_TABLES = {"events"}

def query(connection, table):
    if table not in ALLOWED_TABLES:
        raise ValueError(table)
    return connection.execute(f"SELECT * FROM {table}")
"""

    assert scanner.scan_file("query.py", source, only={"DP-10"}) == []


@pytest.mark.parametrize("relative", [
    "tools/code_intelligence/fuzz_service.py",
    "tools/code_intelligence/oracle_adapters.py",
    "tools/code_intelligence/reachability_service.py",
    "tools/code_intelligence/semantic_service.py",
])
def test_dp06_resolves_actual_file_lexical_bindings(scanner, relative):
    findings = _hits(scanner, relative, "DP-06")

    assert not any(finding["precision"] == "error" for finding in findings)
    assert findings == []


def _assert_one_dp06_candidate(scanner, relative, source):
    findings = scanner.scan_file(relative, source, only={"DP-06"})

    assert len(findings) == 1
    assert findings[0]["dp"] == "DP-06"
    assert findings[0]["precision"] != "error"


def test_dp06_reports_an_unbound_getattr_receiver(scanner):
    source = '''\
def broken():
    return getattr(missing_name, "value", None)
'''

    _assert_one_dp06_candidate(scanner, "broken.py", source)


def test_dp06_does_not_treat_a_sibling_binding_as_lexical(scanner):
    source = '''\
def sibling():
    missing_name = object()
    return missing_name

def broken():
    return getattr(missing_name, "value", None)
'''

    _assert_one_dp06_candidate(scanner, "siblings.py", source)


def test_dp06_does_not_promote_global_assignment_from_an_uncalled_sibling(scanner):
    source = '''\
def sibling():
    global missing_name
    missing_name = object()

def broken():
    return getattr(missing_name, "value", None)
'''

    _assert_one_dp06_candidate(scanner, "global_sibling.py", source)


def test_dp06_method_does_not_close_over_a_class_local(scanner):
    source = '''\
class Container:
    missing_name = object()

    def broken(self):
        return getattr(missing_name, "value", None)
'''

    _assert_one_dp06_candidate(scanner, "class_scope.py", source)


def test_dp06_function_default_is_evaluated_in_the_enclosing_scope(scanner):
    source = '''\
def broken(missing_name=getattr(missing_name, "value", None)):
    return missing_name
'''

    _assert_one_dp06_candidate(scanner, "default_scope.py", source)


def test_dp06_class_comprehension_leftmost_iterator_uses_class_scope(scanner):
    source = '''\
class Container:
    missing_name = []
    values = [item for item in getattr(missing_name, "items", [])]
'''

    findings = scanner.scan_file("class_comprehension.py", source, only={"DP-06"})

    assert not any(finding["precision"] == "error" for finding in findings)
    assert findings == []


def test_dp06_nested_class_comprehension_iterator_uses_class_scope(scanner):
    source = '''\
class Container:
    missing_name = []
    values = [item for item in [nested for nested in getattr(missing_name, "items", [])]]
'''

    exec(source, {})
    findings = scanner.scan_file("nested_class_comprehension.py", source, only={"DP-06"})

    assert not any(finding["precision"] == "error" for finding in findings)
    assert findings == []


def test_dp06_nested_comprehension_element_uses_isolated_scope(scanner):
    source = '''\
class Container:
    missing_name = object()
    values = [
        item
        for item in [
            getattr(missing_name, "value", None)
            for nested in [None]
        ]
    ]
'''

    with pytest.raises(NameError):
        exec(source, {})
    _assert_one_dp06_candidate(scanner, "nested_comprehension_element.py", source)


def test_dp06_nested_comprehension_filter_uses_isolated_scope(scanner):
    source = '''\
class Container:
    missing_name = object()
    values = [
        item
        for item in [
            nested
            for nested in [None]
            if getattr(missing_name, "value", False)
        ]
    ]
'''

    with pytest.raises(NameError):
        exec(source, {})
    _assert_one_dp06_candidate(scanner, "nested_comprehension_filter.py", source)


def test_dp06_nested_comprehension_later_iterator_uses_isolated_scope(scanner):
    source = '''\
class Container:
    missing_name = object()
    values = [
        item
        for item in [
            second
            for first in [None]
            for second in getattr(missing_name, "value", [])
        ]
    ]
'''

    with pytest.raises(NameError):
        exec(source, {})
    _assert_one_dp06_candidate(scanner, "nested_comprehension_later_iterator.py", source)


def test_dp06_lambda_default_in_first_iterator_uses_class_scope(scanner):
    source = '''\
class Container:
    missing_name = object()
    values = [
        item
        for item in (
            lambda default=getattr(missing_name, "items", []): default
        )()
    ]
'''

    exec(source, {})
    findings = scanner.scan_file("lambda_default.py", source, only={"DP-06"})

    assert not any(finding["precision"] == "error" for finding in findings)
    assert findings == []


def test_dp06_lambda_body_in_first_iterator_uses_isolated_scope(scanner):
    source = '''\
class Container:
    missing_name = object()
    values = [
        item
        for item in (lambda: getattr(missing_name, "items", []))()
    ]
'''

    with pytest.raises(NameError):
        exec(source, {})
    _assert_one_dp06_candidate(scanner, "lambda_body.py", source)


def test_dp06_lambda_in_first_iterator_does_not_inherit_comprehension_target(scanner):
    source = '''\
class Container:
    values = [
        item
        for item in (lambda: getattr(item, "items", []))()
    ]
'''

    with pytest.raises(NameError):
        exec(source, {})
    findings = scanner.scan_file(
        "lambda_first_iterator_parentage.py", source, only={"DP-06"}
    )

    assert len(findings) == 1
    assert findings[0]["dp"] == "DP-06"
    assert findings[0]["precision"] != "error"
    assert "'item'" in findings[0]["title"]


def test_dp06_nested_comprehension_in_lambda_default_preserves_boundaries(scanner):
    source = '''\
class Container:
    default_iterable = [None]
    body_value = object()
    values = [
        item
        for item in (
            lambda *, default=[
                getattr(body_value, "value", None)
                for nested in getattr(default_iterable, "__iter__")()
            ]: default
        )()
    ]
'''

    with pytest.raises(NameError):
        exec(source, {})
    findings = scanner.scan_file(
        "lambda_default_comprehension.py", source, only={"DP-06"}
    )

    assert len(findings) == 1
    assert findings[0]["dp"] == "DP-06"
    assert findings[0]["precision"] != "error"
    assert "'body_value'" in findings[0]["title"]


@pytest.mark.parametrize(("relative", "recovery_lines"), [
    pytest.param("tools/code_intelligence/fuzz_adapters.py", (63,), id="fuzz-adapters"),
    pytest.param("tools/code_intelligence/fuzz_service.py", (258, 311), id="fuzz-service"),
    pytest.param("tools/code_intelligence/oracle_service.py", (141,), id="oracle-service"),
    pytest.param("tools/code_intelligence/reachability_service.py", (675,), id="reachability-service"),
    pytest.param("tools/code_intelligence/schemas.py", (995,), id="schemas"),
    pytest.param("tools/graph_build.py", (1166,), id="graph-build"),
])
def test_dp13_ignores_actual_handlers_with_recovery_actions(scanner, relative, recovery_lines):
    hit_lines = {hit["line"] for hit in _hits(scanner, relative, "DP-13")}

    assert hit_lines.isdisjoint(recovery_lines)


@pytest.mark.parametrize("source", [
    '''\
try:
    feature()
except Exception:
    pass
''',
    '''\
try:
    feature()
except Exception:
    logger.exception("feature failed")
''',
])
def test_dp13_keeps_pass_and_logger_only_handlers_as_candidates(scanner, source):
    assert len(scanner.scan_file("handler.py", source, only={"DP-13"})) == 1
