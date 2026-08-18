"""v3.66.1181 -- capture lanes must produce one trustworthy diagnosis."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from tools import pytest_capture_results
from tools.pytest_capture_results import convert_junit


BD_GATE_SCOPE = "repo-wide"
REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_skip_tool():
    spec = importlib.util.spec_from_file_location(
        "check_skip_baseline_capture_reconciliation",
        REPO_ROOT / "tools" / "check_skip_baseline.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _case(classname: str, name: str, duration: str, state: str = "") -> str:
    return (
        f'<testcase classname="{classname}" name="{name}" time="{duration}">'
        f"{state}</testcase>"
    )


def _junit(path: Path, cases: list[str], *, failures: int = 0, errors: int = 0,
           skipped: int = 0) -> Path:
    path.write_text(
        "<testsuites><testsuite tests=\"%d\" failures=\"%d\" errors=\"%d\" "
        "skipped=\"%d\">%s</testsuite></testsuites>"
        % (len(cases), failures, errors, skipped, "".join(cases)),
        encoding="utf-8",
    )
    return path


def _baseline(path: Path, rows: list[tuple[str, str]]) -> Path:
    path.write_text(json.dumps({
        "schema": "bd-skip-baseline/1",
        "skips": [{"identity": identity, "reason": reason}
                  for identity, reason in rows],
    }), encoding="utf-8")
    return path


def test_converter_reconciles_lanes_and_renders_deterministic_diagnostics(tmp_path):
    parallel = _junit(tmp_path / "parallel.xml", [
        _case("tests.z", "test_zeta", "45"),
        _case("tests.b", "test_exact_budget", "30"),
        _case("tests.k", "test_skipped", "0.2", '<skipped message="needs postgres"/>'),
    ], skipped=1)
    serial = _junit(tmp_path / "serial.xml", [
        _case("tests.a", "test_alpha", "45"),
        _case("tests.m", "test_over", "30.1"),
    ])
    json_path = tmp_path / "results.json"
    summary_path = tmp_path / "summary.txt"

    payload = convert_junit([parallel, serial], json_path, summary_path,
                            version="3.66.fixture")

    assert payload["ok"] is True
    assert payload["budget"]["threshold_s"] == 30.0
    assert [row["identity"] for row in payload["budget"]["over"]] == [
        "tests.a::test_alpha", "tests.z::test_zeta", "tests.m::test_over",
    ]
    assert [row["identity"] for row in payload["budget"]["slowest"]] == [
        "tests.a::test_alpha", "tests.z::test_zeta", "tests.m::test_over",
        "tests.b::test_exact_budget", "tests.k::test_skipped",
    ]
    assert payload["skips"] == [{
        "identity": "tests.k::test_skipped", "file": "tests/k",
        "test": "test_skipped", "reason": "needs postgres",
    }]
    text = summary_path.read_text(encoding="utf-8")
    assert "BUDGET: >30.0s = 3" in text
    assert "SLOWEST (top 5):" in text
    assert "SKIPS (1):" in text
    assert "tests.k::test_skipped :: needs postgres" in text
    assert json.loads(json_path.read_text(encoding="utf-8")) == payload


def test_converter_renders_exact_top20_and_text_detail_parity(tmp_path):
    # Twenty-one over-budget cases prove both the exact top-20 cutoff and that
    # every structured diagnostic row is rendered in the text artifact.
    slow_cases = [
        _case(f"tests.slow_{number:02d}", "test_case", str(51 - number))
        for number in range(21)
    ]
    skip_cases = [
        _case("tests.skip_b", "test_case", "0.2", '<skipped message="reason b"/>'),
        _case("tests.skip_a", "test_case", "0.1", '<skipped message="reason a"/>'),
    ]
    parallel = _junit(tmp_path / "parallel.xml", slow_cases)
    serial = _junit(tmp_path / "serial.xml", skip_cases, skipped=2)
    payload = convert_junit([parallel, serial], tmp_path / "out.json",
                            tmp_path / "out.txt", version="fixture")

    expected_over = [f"tests.slow_{number:02d}::test_case" for number in range(21)]
    expected_skips = [
        ("tests.skip_a::test_case", "reason a"),
        ("tests.skip_b::test_case", "reason b"),
    ]
    assert [row["identity"] for row in payload["budget"]["over"]] == expected_over
    assert [row["identity"] for row in payload["budget"]["slowest"]] == expected_over[:20]
    assert len(payload["budget"]["slowest"]) == 20
    assert [(row["identity"], row["reason"]) for row in payload["skips"]] == expected_skips

    lines = (tmp_path / "out.txt").read_text(encoding="utf-8").splitlines()
    over_start = lines.index("OVER BUDGET (21):") + 1
    slow_start = lines.index("SLOWEST (top 20):") + 1
    skip_start = lines.index("SKIPS (2):") + 1
    assert lines[over_start:over_start + 21] == [
        f"  {51 - number:.4f}s {identity}"
        for number, identity in enumerate(expected_over)
    ]
    assert lines[slow_start:slow_start + 20] == [
        f"  {51 - number:.4f}s {identity}"
        for number, identity in enumerate(expected_over[:20])
    ]
    assert lines[skip_start:skip_start + 2] == [
        f"  {identity} :: {reason}" for identity, reason in expected_skips
    ]


def test_converter_refuses_mixed_direct_and_nested_suite_testcases(tmp_path):
    mixed = tmp_path / "mixed.xml"
    mixed.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.direct" name="test_omitted" time="0.1"/>'
        '<testsuite tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.nested" name="test_seen" time="0.1"/>'
        "</testsuite></testsuite></testsuites>",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="mixed direct and nested"):
        convert_junit(mixed, tmp_path / "out.json", tmp_path / "out.txt",
                      version="fixture")


@pytest.mark.parametrize("parallel, serial, match", [
    (
        [_case("tests.x", "test_same", "0.1")],
        [_case("tests.x", "test_same", "0.2")], "duplicate",
    ),
    ([_case("tests.x", "test_nan", "nan")], [], "duration"),
    ([_case("tests.x", "test_negative", "-0.1")], [], "duration"),
    ([_case("tests.x", "test_unknown", "0.1", "<unknown/>")], [], "unknown"),
    (
        [_case("tests.x", "test_two", "0.1", "<failure/><skipped message=\"x\"/>")],
        [], "multiple",
    ),
])
def test_converter_refuses_ambiguous_or_malformed_lane_evidence(
        tmp_path, parallel, serial, match):
    parallel_path = _junit(tmp_path / "parallel.xml", parallel)
    serial_path = _junit(tmp_path / "serial.xml", serial)

    with pytest.raises(ValueError, match=match):
        convert_junit([parallel_path, serial_path], tmp_path / "out.json",
                      tmp_path / "out.txt", version="fixture")


def test_converter_refuses_duplicate_identity_within_one_lane(tmp_path):
    lane = _junit(tmp_path / "parallel.xml", [
        _case("tests.x", "test_same", "0.1"),
        _case("tests.x", "test_same", "0.2"),
    ])

    with pytest.raises(ValueError, match="duplicate"):
        convert_junit(lane, tmp_path / "out.json", tmp_path / "out.txt",
                      version="fixture")


def test_converter_refuses_declared_count_mismatch_and_failures_make_not_ok(tmp_path):
    malformed = (tmp_path / "malformed.xml")
    malformed.write_text(
        '<testsuite tests="2" failures="0" errors="0" skipped="0">'
        '<testcase classname="tests.x" name="test_one" time="0.1"/>'
        '</testsuite>', encoding="utf-8")
    with pytest.raises(ValueError, match="summary disagrees"):
        convert_junit(malformed, tmp_path / "out.json", tmp_path / "out.txt",
                      version="fixture")

    failed = _junit(tmp_path / "failed.xml", [
        _case("tests.x", "test_failure", "0.1", "<failure message=\"nope\"/>"),
    ], failures=1)
    payload = convert_junit(failed, tmp_path / "failed.json",
                            tmp_path / "failed.txt", version="fixture")
    assert payload["ok"] is False


@pytest.mark.parametrize("classname, name", [("", "test_name"), ("   ", "test_name"),
                                               ("tests.x", "")])
def test_converter_requires_trimmed_classname_and_name_for_identity(
        tmp_path, classname, name):
    junit = _junit(tmp_path / "lane.xml", [_case(classname, name, "0.1")])
    with pytest.raises(ValueError, match="stable identity"):
        convert_junit(junit, tmp_path / "out.json", tmp_path / "out.txt",
                      version="fixture")


def test_converter_cli_requires_two_distinct_nonempty_lanes(tmp_path):
    lane = _junit(tmp_path / "lane.xml", [_case("tests.x", "test_one", "0.1")])
    for lanes in ([lane], [lane, lane]):
        args = [
            item for lane_path in lanes for item in ("--junit", str(lane_path))
        ] + ["--json", str(tmp_path / "out.json"), "--summary", str(tmp_path / "out.txt")]
        with pytest.raises(SystemExit) as exc:
            pytest_capture_results.main(args)
        assert exc.value.code == 2


def test_skip_checker_allows_each_baseline_identity_to_pass_or_exactly_skip(
        tmp_path, monkeypatch, capsys):
    checker = _load_skip_tool()
    baseline = _baseline(tmp_path / "baseline.json", [
        ("tests.a::test_can_pass", "optional capability"),
        ("tests.b::test_can_skip", "needs postgres"),
    ])
    parallel = _junit(tmp_path / "parallel.xml", [
        _case("tests.a", "test_can_pass", "0.1"),
    ])
    serial = _junit(tmp_path / "serial.xml", [
        _case("tests.b", "test_can_skip", "0.1", '<skipped message="needs postgres"/>'),
    ], skipped=1)
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(parallel), "--junit", str(serial),
    ])

    assert checker.main() == 0
    assert "2 baseline identities executed" in capsys.readouterr().out


def test_skip_checker_reconciles_fleet_shaped_18_pass_21_exact_skips(
        tmp_path, monkeypatch, capsys):
    checker = _load_skip_tool()
    passed = [(f"tests.pass_{number}::test_case", "optional capability")
              for number in range(18)]
    skipped = [(f"tests.skip_{number}::test_case", f"permitted skip {number}")
               for number in range(21)]
    baseline = _baseline(tmp_path / "baseline.json", passed + skipped)
    parallel = _junit(tmp_path / "parallel.xml", [
        _case(identity.split("::", 1)[0], "test_case", "0.1")
        for identity, _reason in passed
    ])
    serial = _junit(tmp_path / "serial.xml", [
        _case(identity.split("::", 1)[0], "test_case", "0.1",
              f'<skipped message="{reason}"/>')
        for identity, reason in skipped
    ], skipped=21)
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(parallel), "--junit", str(serial),
    ])

    assert checker.main() == 0
    assert "39 baseline identities executed" in capsys.readouterr().out


def test_skip_reason_uses_xunit_message_not_location_prefixed_text(tmp_path, monkeypatch):
    checker = _load_skip_tool()
    baseline = _baseline(tmp_path / "baseline.json", [
        ("tests.b::test_can_skip", "needs postgres"),
    ])
    parallel = _junit(tmp_path / "parallel.xml", [
        _case("tests.extra", "test_runs", "0.1"),
    ])
    serial = _junit(tmp_path / "serial.xml", [
        _case("tests.b", "test_can_skip", "0.1",
              '<skipped message="  needs postgres  ">'
              'tests/test_b.py:20: no MOD3_PG_TEST_DSN</skipped>'),
    ], skipped=1)
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(parallel), "--junit", str(serial),
    ])

    assert checker.main() == 0


def test_skip_checker_cli_requires_two_distinct_lanes_and_refuses_update(
        tmp_path, monkeypatch, capsys):
    checker = _load_skip_tool()
    baseline = _baseline(tmp_path / "baseline.json", [
        ("tests.a::test_can_pass", "optional capability"),
    ])
    lane = _junit(tmp_path / "lane.xml", [_case("tests.a", "test_can_pass", "0.1")])
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(lane), "--update",
    ])

    assert checker.main() == 2
    assert "exactly two" in capsys.readouterr().err
    assert _load_skip_tool()._read_identity_baseline(baseline) == {
        "tests.a::test_can_pass": "optional capability",
    }

    other_lane = _junit(tmp_path / "other.xml", [
        _case("tests.extra", "test_runs", "0.1"),
    ])
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(lane), "--junit", str(other_lane), "--update",
    ])
    assert checker.main() == 2
    assert "update is disabled" in capsys.readouterr().err
    assert _load_skip_tool()._read_identity_baseline(baseline) == {
        "tests.a::test_can_pass": "optional capability",
    }

    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(lane), "--junit", str(lane),
    ])
    assert checker.main() == 2
    assert "exactly two" in capsys.readouterr().err


@pytest.mark.parametrize("serial_cases, skipped, expected_exit, message", [
    ([], 0, 1, "missing"),
    ([_case("tests.b", "test_can_skip", "0.1", '<skipped message="wrong"/>')], 1,
     1, "reason changed"),
    ([_case("tests.extra", "test_unexpected", "0.1", '<skipped message="nope"/>')], 1,
     1, "unexpected"),
    ([_case("tests.a", "test_can_pass", "0.1")], 0, 2, "duplicate"),
])
def test_skip_checker_refuses_missing_drift_or_duplicate_multilane_evidence(
        tmp_path, monkeypatch, capsys, serial_cases, skipped, expected_exit, message):
    checker = _load_skip_tool()
    baseline = _baseline(tmp_path / "baseline.json", [
        ("tests.a::test_can_pass", "optional capability"),
        ("tests.b::test_can_skip", "needs postgres"),
    ])
    parallel = _junit(tmp_path / "parallel.xml", [
        _case("tests.a", "test_can_pass", "0.1"),
    ])
    serial = _junit(
        tmp_path / "serial.xml",
        serial_cases or [_case("tests.extra", "test_runs", "0.1")],
        skipped=skipped,
    )
    monkeypatch.setattr(sys, "argv", [
        "check_skip_baseline.py", "--baseline", str(baseline),
        "--junit", str(parallel), "--junit", str(serial),
    ])

    assert checker.main() == expected_exit
    assert message in capsys.readouterr().err.lower()


def test_capture_wires_one_multilane_skip_reconciliation_after_converter():
    source = (REPO_ROOT / "capture.sh").read_text(encoding="utf-8")
    converter = source.index("tools/pytest_capture_results.py")
    checker = source.index("tools/check_skip_baseline.py")

    assert source.count("tools/check_skip_baseline.py") == 1
    assert checker > converter
    check_block = source[checker:source.index("SKIP_BASELINE_EXIT=$?", checker)]
    assert '--junit "$OUT/02_pytest_parallel.xml"' in check_block
    assert '--junit "$OUT/02_pytest_serial.xml"' in check_block
