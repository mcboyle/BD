"""Current skip-governance contract for canonical real-pytest JUnit."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "repo-wide"


# ── Load the tool as a module via importlib so we can call the parser
# directly. tools/ is not a package on the path.
def _load_tool():
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "tools" / "check_skip_baseline.py"
    spec = importlib.util.spec_from_file_location(
        "check_skip_baseline", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_TOOL = _load_tool()


def _load_runtime_report():
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "tools" / "test_runtime_report.py"
    spec = importlib.util.spec_from_file_location("test_runtime_report", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _junit(tmp_path, body: str) -> Path:
    path = tmp_path / "result.xml"
    path.write_text(body, encoding="utf-8")
    return path


def _passing_lanes(tmp_path) -> tuple[Path, Path]:
    paths = []
    for lane in ("parallel", "serial"):
        path = tmp_path / f"{lane}.xml"
        path.write_text(
            "<testsuites><testsuite tests='1' failures='0' errors='0' "
            "skipped='0'><testcase classname='tests.%s' name='test_runs'/>"
            "</testsuite></testsuites>" % lane,
            encoding="utf-8",
        )
        paths.append(path)
    return paths[0], paths[1]


def test_junit_reader_returns_exact_skip_identities_and_reasons(tmp_path):
    path = _junit(tmp_path, '''<?xml version="1.0"?>
<testsuites><testsuite name="pytest" tests="3" failures="0" errors="0" skipped="2">
  <testcase classname="tests.test_alpha" name="test_runs" />
  <testcase classname="tests.test_alpha" name="test_parked">
    <skipped message="parked by operator">detail</skipped>
  </testcase>
  <testcase classname="tests.test_beta.TestMode" name="test_external[x]">
    <skipped message="fixture unavailable">detail</skipped>
  </testcase>
</testsuite></testsuites>''')

    result = _TOOL._read_junit(path)

    assert result["executed"] == 3
    assert result["skipped"] == {
        "tests.test_alpha::test_parked": "parked by operator",
        "tests.test_beta.TestMode::test_external[x]": "fixture unavailable",
    }


@pytest.mark.parametrize("xml, message", [
    ("<testsuites/>", "summary"),
    ("<testsuites><testsuite tests='0' failures='0' errors='0' skipped='0'/></testsuites>",
     "zero"),
    ("<testsuites><testsuite tests='2' failures='0' errors='0' skipped='0'>"
     "<testcase classname='tests.x' name='test_one'/></testsuite></testsuites>",
     "summary disagrees"),
    ("<testsuites><testsuite tests='1' failures='0' errors='0' skipped='1'>"
     "<testcase classname='tests.x' name='test_one'><skipped/></testcase>"
     "</testsuite></testsuites>", "reason"),
    ("<testsuites><testsuite tests='1' failures='1' errors='0' skipped='0'>"
     "<testcase classname='tests.x' name='test_one'><failure/></testcase>"
     "</testsuite></testsuites>", "failure"),
    ("<testsuites><testsuite tests='1' failures='0' errors='1' skipped='0'>"
     "<testcase classname='tests.x' name='test_one'><error/></testcase>"
     "</testsuite></testsuites>", "error"),
    ("<testsuites><testsuite tests='1' failures='0' errors='0' skipped='0'>"
     "<testcase classname='tests.x' name='test_one'><skipped message='parked'/>"
     "</testcase></testsuite></testsuites>", "summary disagrees"),
])
def test_junit_reader_fails_closed_on_incomplete_or_vacuous_evidence(
        tmp_path, xml, message):
    with pytest.raises(_TOOL.EvidenceError, match=message):
        _TOOL._read_junit(_junit(tmp_path, xml))


def test_skip_comparison_is_identity_and_reason_exact():
    expected = {
        "tests.test_alpha::test_parked": "parked by operator",
        "tests.test_beta::test_external": "fixture unavailable",
    }
    assert _TOOL._compare_skips(expected, dict(expected)) == []
    assert _TOOL._compare_skips(expected, {
        "tests.test_alpha::test_parked": "different reason",
        "tests.test_gamma::test_new": "fixture unavailable",
    }) == [
        "missing: tests.test_beta::test_external",
        "unexpected: tests.test_gamma::test_new",
        "reason changed: tests.test_alpha::test_parked",
    ]


def test_main_checks_a_complete_junit_against_exact_json_baseline(
        tmp_path, monkeypatch, capsys):
    junit = _junit(tmp_path, '''<testsuites><testsuite tests="2" failures="0"
errors="0" skipped="1"><testcase classname="tests.alpha" name="test_runs"/>
<testcase classname="tests.alpha" name="test_parked"><skipped
message="parked by operator">detail</skipped></testcase></testsuite></testsuites>''')
    baseline = tmp_path / "SKIP_BASELINE.json"
    baseline.write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
        "skips": [{
            "identity": "tests.alpha::test_parked",
            "reason": "parked by operator",
        }],
    }), encoding="utf-8")
    other = tmp_path / "other.xml"
    other.write_text(
        "<testsuites><testsuite tests='1' failures='0' errors='0' skipped='0'>"
        "<testcase classname='tests.beta' name='test_runs'/></testsuite></testsuites>",
        encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--junit", str(junit),
        "--junit", str(other),
        "--baseline", str(baseline),
    ])

    assert _TOOL.main() == 0
    assert "1 baseline identity executed" in capsys.readouterr().out


def test_main_refuses_missing_summary_and_zero_collection(
        tmp_path, monkeypatch, capsys):
    baseline = tmp_path / "SKIP_BASELINE.json"
    baseline.write_text(json.dumps({
        "schema": "bd-skip-baseline/1", "skips": []}), encoding="utf-8")
    for xml in ("<testsuites/>",
                "<testsuites><testsuite tests='0' failures='0' errors='0' "
                "skipped='0'/></testsuites>"):
        junit = _junit(tmp_path, xml)
        other = tmp_path / "other.xml"
        other.write_text(
            "<testsuites><testsuite tests='1' failures='0' errors='0' skipped='0'>"
            "<testcase classname='tests.beta' name='test_runs'/></testsuite></testsuites>",
            encoding="utf-8")
        monkeypatch.setattr(sys, "argv", [
            "check_skip_baseline.py", "--junit", str(junit),
            "--junit", str(other),
            "--baseline", str(baseline),
        ])
        assert _TOOL.main() == 2
    assert "REFUSED" in capsys.readouterr().err


def test_baseline_rejects_duplicate_json_keys(tmp_path):
    baseline = tmp_path / "SKIP_BASELINE.json"
    baseline.write_text(
        '{"schema":"bd-skip-baseline/1","schema":"wrong","skips":[]}',
        encoding="utf-8")
    with pytest.raises(_TOOL.EvidenceError, match="duplicate JSON key"):
        _TOOL._read_identity_baseline(baseline)


def test_main_refuses_schema_only_baseline_without_required_skips(
        tmp_path, monkeypatch, capsys):
    baseline = tmp_path / "SKIP_BASELINE.json"
    baseline.write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
    }), encoding="utf-8")
    parallel, serial = _passing_lanes(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--junit", str(parallel),
        "--junit", str(serial), "--baseline", str(baseline),
    ])

    assert _TOOL.main() == 2
    assert "required skips list is missing" in capsys.readouterr().err


def test_main_refuses_ordinary_identity_in_collection_skip_namespace(
        tmp_path, monkeypatch, capsys):
    baseline = tmp_path / "SKIP_BASELINE.json"
    baseline.write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
        "skips": [],
        "collection_skips": [{
            "identity": "tests.required::test_must_execute",
            "reason": "must execute",
        }],
    }), encoding="utf-8")
    parallel, serial = _passing_lanes(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--junit", str(parallel),
        "--junit", str(serial), "--baseline", str(baseline),
    ])

    assert _TOOL.main() == 2
    assert "outside the <collection>:: namespace" in capsys.readouterr().err


def test_runtime_report_counts_the_canonical_identity_baseline(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "SKIP_BASELINE.json").write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
        "skips": [
            {"identity": "tests.a::test_one", "reason": "parked"},
            {"identity": "tests.b::test_two", "reason": "external"},
        ],
        "collection_skips": [
            {"identity": "<collection>::tests.c", "reason": "optional"},
        ],
    }), encoding="utf-8")
    assert _load_runtime_report()._skip_baseline(str(tmp_path)) == 3


@pytest.mark.parametrize("contents, match", [
    ("{not-json", "malformed"),
    (json.dumps({"schema": "wrong", "skips": []}), "schema"),
    (json.dumps({"schema": "bd-skip-baseline/1", "skips": {}}), "list"),
    (json.dumps({"schema": "bd-skip-baseline/1", "skips": [{}]}),
     "fields"),
    (json.dumps({"schema": "bd-skip-baseline/1", "skips": [
        {"identity": "tests.a::test_one", "reason": ""}]}), "reason"),
    (json.dumps({"schema": "bd-skip-baseline/1", "skips": [
        {"identity": "tests.a::test_one", "reason": "one"},
        {"identity": "tests.a::test_one", "reason": "two"}]}), "duplicate"),
])
def test_runtime_report_refuses_invalid_skip_authority(tmp_path, contents, match):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "SKIP_BASELINE.json").write_text(contents, encoding="utf-8")
    report = _load_runtime_report()
    with pytest.raises(report.ReportEvidenceError, match=match):
        report._skip_baseline(str(tmp_path))


@pytest.mark.parametrize("collection_skips, skips, match", [
    ({}, [], "list"),
    ([{"identity": "tests.a::test_one", "reason": "ordinary"}], [],
     "<collection>::"),
    ([{"identity": "<collection>::tests.a", "reason": "one"}], [
        {"identity": "<collection>::tests.a", "reason": "two"}],
     "two policies"),
    ([{"identity": "<collection>::tests.a", "reason": "one"},
      {"identity": "<collection>::tests.a", "reason": "two"}], [],
     "duplicate"),
])
def test_runtime_report_shares_collection_policy_validation(
        tmp_path, collection_skips, skips, match):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "SKIP_BASELINE.json").write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
        "skips": skips,
        "collection_skips": collection_skips,
    }), encoding="utf-8")
    report = _load_runtime_report()
    with pytest.raises(report.ReportEvidenceError, match=match):
        report._skip_baseline(str(tmp_path))


def test_skip_baseline_update_is_disabled_and_cannot_shrink_authority(
        tmp_path, monkeypatch, capsys):
    junit = _junit(tmp_path, """<testsuites><testsuite tests='1'
      failures='0' errors='0' skipped='0'><testcase classname='tests.alpha'
      name='test_runs'/></testsuite></testsuites>""")
    other = tmp_path / "other.xml"
    other.write_text("""<testsuites><testsuite tests='1'
      failures='0' errors='0' skipped='0'><testcase classname='tests.beta'
      name='test_runs'/></testsuite></testsuites>""", encoding="utf-8")
    baseline = tmp_path / "SKIP_BASELINE.json"
    baseline.write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
        "skips": [{"identity": "tests.alpha::test_parked", "reason": "parked"}],
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--junit", str(junit),
        "--junit", str(other), "--baseline", str(baseline), "--update",
    ])
    assert _TOOL.main() == 2
    assert "--update is disabled" in capsys.readouterr().err
    assert _TOOL._read_identity_baseline(baseline) == {
        "tests.alpha::test_parked": "parked",
    }


def test_junit_reader_reconciles_each_result_state(tmp_path):
    mismatch = _junit(tmp_path, """<testsuites><testsuite tests='1'
      failures='1' errors='0' skipped='0'><testcase classname='tests.alpha'
      name='test_failed'/></testsuite></testsuites>""")
    with pytest.raises(_TOOL.EvidenceError, match="summary disagrees"):
        _TOOL._read_junit(mismatch)

    multiple = _junit(tmp_path, """<testsuites><testsuite tests='1'
      failures='1' errors='0' skipped='1'><testcase classname='tests.alpha'
      name='test_impossible'><failure/><skipped message='parked'/></testcase>
      </testsuite></testsuites>""")
    with pytest.raises(_TOOL.EvidenceError, match="multiple result states"):
        _TOOL._read_junit(multiple)


def test_junit_reader_rejects_duplicate_same_result_state(tmp_path):
    duplicate = _junit(tmp_path, """<testsuites><testsuite tests='1'
      failures='0' errors='0' skipped='2'><testcase classname='tests.alpha'
      name='test_impossible'><skipped message='first'/>
      <skipped message='second'/></testcase></testsuite></testsuites>""")

    with pytest.raises(_TOOL.EvidenceError, match="multiple result states"):
        _TOOL._read_junit(duplicate)


def test_junit_reader_rejects_unknown_result_state(tmp_path):
    unknown = _junit(tmp_path, """<testsuites><testsuite tests='1'
      failures='0' errors='0' skipped='0'><testcase classname='tests.alpha'
      name='test_impossible'><passed/></testcase></testsuite></testsuites>""")

    with pytest.raises(_TOOL.EvidenceError,
                       match="unknown testcase result element"):
        _TOOL._read_junit(unknown)


def test_written_skip_baseline_is_read_back_before_success(tmp_path, monkeypatch):
    target = tmp_path / "SKIP_BASELINE.json"
    original = _TOOL.Path.replace

    def corrupt_after_replace(source, destination):
        result = original(source, destination)
        destination.write_text(
            '{"schema":"bd-skip-baseline/1","skips":[]}\n',
            encoding="utf-8")
        return result

    monkeypatch.setattr(_TOOL.Path, "replace", corrupt_after_replace)
    with pytest.raises(_TOOL.EvidenceError, match="did not verify"):
        _TOOL._write_identity_baseline(
            target, {"tests.alpha::test_parked": "parked"})
