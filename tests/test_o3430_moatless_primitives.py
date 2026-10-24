"""Behavioral controls for the O3430 moatless-tools primitive port."""
import importlib
import os
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
CANDIDATE = Path(os.environ.get(
    "BD_MOATLESS_CANDIDATE",
    "/home/mboyle/bd-persist/harness-work/O1789/local-agent-reliability/ws/MOATLESS",
))

MODULE = '''"""Module doc."""
import os
import sys

LIMIT = 3


class Store(Base):
    kind = "kv"

    @property
    def size(self):
        return 0

    @size.setter
    def size(self, value):
        pass

    def get(self, key: str, default=None) -> str:
        return default


def get(key):
    return key
'''


@pytest.fixture(scope="module")
def mp():
    sys.path.insert(0, str(CANDIDATE))
    try:
        yield importlib.import_module("moatless_primitives")
    finally:
        sys.path.remove(str(CANDIDATE))


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "store.py").write_text(MODULE)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_store.py").write_text("class Store:\n    def get(self):\n        pass\n")
    return tmp_path


@pytest.mark.parametrize("path,expected", [
    ("pkg/tests/x.py", True), ("pkg/test_x.py", True), ("pkg/x_test.py", True), ("tests.py", True),
    ("pkg/testing_utils.py", False), ("pkg/contest.py", False), ("pkg/attest/x.py", False),
])
def test_is_test_matches_moatless(mp, path, expected):
    assert mp.is_test(path) is expected


def test_span_ids_follow_moatless_convention(mp):
    spans = {s.span_id: s for s in mp.index_spans(MODULE)}
    assert list(spans) == ["docstring", "imports", "impl", "Store", "Store.size", "Store.size:2", "Store.get", "get"]
    assert spans["Store.size"].start_line == 11  # decorator line opens the span
    assert spans["Store.get"].signature == "def get(self, key: str, default=None) -> str:"
    assert (spans["Store"].start_line, spans["Store"].kind) == (8, "class")


def test_unparseable_source_is_an_error_not_empty(mp):
    with pytest.raises(mp.SpanParseError, match="unparseable source"):
        mp.index_spans("def broken(:\n")


def test_changed_spans_names_only_touched_symbols(mp):
    patched = MODULE.replace("return default", "return key")
    assert mp.changed_spans(MODULE, patched) == {"added": [], "removed": [], "modified": ["Store.get"]}
    assert mp.changed_spans(MODULE, MODULE) == {"added": [], "removed": [], "modified": []}


LATE_BODY = '''class C:
    def f(self):
        pass

    kind = "a"

    def g(self):
        pass

    if True:
        flag = 1
'''


def test_class_body_after_method_is_its_own_span(mp):
    spans = {s.span_id: s for s in mp.index_spans(LATE_BODY)}
    assert list(spans) == ["C", "C.f", "C:2", "C.g", "C:3"]
    assert (spans["C:2"].kind, spans["C:2"].start_line, spans["C:3"].end_line) == ("impl", 5, 11)
    for old, new, touched in (('"a"', '"b"', "C:2"), ("flag = 1", "flag = 2", "C:3")):
        assert mp.changed_spans(LATE_BODY, LATE_BODY.replace(old, new))["modified"] == [touched]


def test_find_by_name_class_then_method(mp, repo):
    res = mp.find_by_name(repo, class_name="Store", function_name="get")
    assert [(h.file_path, h.span_id) for h in res.hits] == [
        ("pkg/store.py", "Store.get"), ("tests/test_store.py", "Store.get")]
    res = mp.find_by_name(repo, function_name="get")
    assert {h.span_id for h in res.hits} == {"Store.get", "get"}


def test_find_by_name_implementation_category_drops_tests(mp, repo):
    res = mp.find_by_name(repo, class_name="Store", category="implementation")
    assert [h.file_path for h in res.hits] == ["pkg/store.py"]
    res = mp.find_by_name(repo, class_name="Store", category="test")
    assert len(res.hits) == 2


def test_find_by_name_distinguishes_missing_path_from_empty_pattern(mp, repo):
    assert mp.find_by_name(repo, class_name="Store", file_pattern="pkg/nope.py").message == \
        "No file found on path pkg/nope.py."
    assert mp.find_by_name(repo, class_name="Store", file_pattern="**/nope_*.py").message == \
        "No files found for file pattern **/nope_*.py."
    assert mp.find_by_name(repo, class_name="Missing").message == "No classes found with the name Missing."
    with pytest.raises(ValueError):
        mp.find_by_name(repo)


def test_find_by_name_reports_unparsed_files(mp, repo):
    (repo / "pkg" / "bad.py").write_text("class Store(:\n")
    res = mp.find_by_name(repo, class_name="Store")
    assert res.unparsed == ["pkg/bad.py"] and len(res.hits) == 2


def test_find_code_snippet_line_span(mp, repo):
    hits = mp.find_code_snippet(repo, "    @size.setter\n    def size(self, value):", file_pattern="**/*.py")
    assert [(h.file_path, h.start_line, h.end_line) for h in hits] == [("pkg/store.py", 15, 16)]


def test_walk_never_escapes_root_or_reads_forbidden(mp, repo, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside")
    (outside / "leak.py").write_text("class Store:\n    pass\n")
    (repo / "pkg" / "link.py").symlink_to(outside / "leak.py")
    (repo / "linkdir").symlink_to(outside, target_is_directory=True)
    for part in ("sealed", ".review", "corpus-heldout"):
        (repo / part).mkdir()
        (repo / part / "store.py").write_text("class Store:\n    pass\n")
    assert mp.iter_python_files(repo) == ["pkg/store.py", "tests/test_store.py"]


SUMMARY_LOG = """tests/test_a.py::test_x PASSED
=================================== FAILURES ===================================
___________________________________ test_y ____________________________________
E       assert 1 == 2
=========================== short test summary info ============================
PASSED tests/test_a.py::test_x
FAILED tests/test_a.py::test_y[1-2] - assert 1 == 2
SKIPPED [1] tests/test_a.py:9: no db
"""

COLLECTION_LOG = """==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_c.py ______________________
ImportError while importing test module 'tests/test_c.py'.
=========================== short test summary info ============================
ERROR tests/test_c.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
"""


def test_pytest_summary_parsed_per_test(mp):
    res = mp.parse_pytest_output(SUMMARY_LOG)
    assert [(r.status.value, r.name) for r in res if r.method] == [
        ("PASSED", "tests/test_a.py::test_x"), ("FAILED", "tests/test_a.py::test_y[1-2]")]
    assert mp.get_test_status(res) == mp.TestStatus.FAILED
    assert mp.get_test_counts(res) == (2, 1, 0)


def test_collection_error_is_error_never_failed(mp):
    res = mp.parse_pytest_output(COLLECTION_LOG)
    assert [(r.status.value, r.file_path) for r in res] == [("ERROR", "tests/test_c.py")]
    assert mp.get_test_status(res) == mp.TestStatus.ERROR


def test_traceback_without_rows_is_error(mp):
    res = mp.parse_pytest_output("Traceback (most recent call last):\nModuleNotFoundError: x\n")
    assert mp.get_test_status(res) == mp.TestStatus.ERROR


def test_summary_counts_fallback_and_empty_is_unjudged(mp):
    assert mp.get_test_status(mp.parse_pytest_output("==== 3 passed in 0.1s ====")) == mp.TestStatus.PASSED
    assert mp.get_test_status(mp.parse_pytest_output("==== 1 failed, 2 passed ====")) == mp.TestStatus.FAILED
    assert mp.get_test_status(mp.parse_pytest_output("== 2 passed, 1 error ==")) == mp.TestStatus.ERROR
    assert mp.get_test_status(mp.parse_pytest_output("")) is None
