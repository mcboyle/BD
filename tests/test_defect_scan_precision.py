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
    assert _hits(scanner, relative, "DP-06") == []


def test_dp06_reports_an_unbound_getattr_receiver(scanner):
    source = '''\
def broken():
    return getattr(missing_name, "value", None)
'''

    assert len(scanner.scan_file("broken.py", source, only={"DP-06"})) == 1


def test_dp06_does_not_treat_a_sibling_binding_as_lexical(scanner):
    source = '''\
def sibling():
    missing_name = object()
    return missing_name

def broken():
    return getattr(missing_name, "value", None)
'''

    assert len(scanner.scan_file("siblings.py", source, only={"DP-06"})) == 1


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
