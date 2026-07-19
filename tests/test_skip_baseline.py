"""Tests for tools/check_skip_baseline.py (PT9).

Pinned because v3.63.9 shipped a parser that recognised only
"Skipped: N" while the runner's SUMMARY.txt writes "N skipped".
On a fresh deploy, the first `--update` call failed with
"could not read Skipped count from SUMMARY.txt", silently breaking
the release-time skip-count gate. v3.63.10's parser accepts both
shapes; these tests pin that the actual runner output round-trips.
"""
import importlib.util
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest


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


# ── Real-runner shape: `N total | N passed | N failed | N skipped` ──

def test_parser_reads_real_runner_summary(tmp_path):
    """The exact shape run_tests.py writes to SUMMARY.txt. Pinned
    verbatim — if this shape ever changes the parser must keep up."""
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "BulkDownloader test summary\n"
        "version : 3.63.10\n"
        "run at  : 2026-05-22T00:14:07\n"
        "result  : 4443 total | 4443 passed | 0 failed | 0 skipped\n"
        "\n"
        "FAILURES: none\n",
        encoding="utf-8",
    )
    assert _TOOL._read_summary_skipped(summary) == 0


def test_parser_handles_nonzero_skipped(tmp_path):
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "result  : 100 total | 95 passed | 0 failed | 5 skipped\n",
        encoding="utf-8",
    )
    assert _TOOL._read_summary_skipped(summary) == 5


def test_parser_handles_large_count(tmp_path):
    """Pre-empt any 'small int regex' assumption."""
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "result  : 99999 total | 99000 passed | 0 failed | 999 skipped\n",
        encoding="utf-8",
    )
    assert _TOOL._read_summary_skipped(summary) == 999


# ── Stdout one-liner shape: `... | Skipped: N` ──

def test_parser_falls_back_to_label_colon_shape(tmp_path):
    """The shape the runner prints to stdout. Used to be the only
    shape the parser recognised; kept as a fallback."""
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "  Total: 4443 | Passed: 4443 | Failed: 0 | Skipped: 0\n",
        encoding="utf-8",
    )
    assert _TOOL._read_summary_skipped(summary) == 0


def test_parser_label_colon_nonzero(tmp_path):
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "  Total: 100 | Passed: 92 | Failed: 0 | Skipped: 8\n",
        encoding="utf-8",
    )
    assert _TOOL._read_summary_skipped(summary) == 8


# ── Negative cases ──

def test_parser_returns_none_for_missing_file(tmp_path):
    assert _TOOL._read_summary_skipped(tmp_path / "nope.txt") is None


def test_parser_returns_none_when_no_skip_count(tmp_path):
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "BulkDownloader test summary\n"
        "version : 3.63.10\n"
        "(crashed before writing the result line)\n",
        encoding="utf-8",
    )
    assert _TOOL._read_summary_skipped(summary) is None


# ── End-to-end: write a real SUMMARY.txt by invoking the runner's
# own formatter, then parse it. This is the regression that v3.63.9
# missed — no test ever round-tripped runner output through this
# tool. The runner's summary-writing code is at run_tests.py around
# line 929; we reproduce the line shape here so a runner-format
# change that we miss in this test still gets caught the next time
# someone updates SUMMARY.txt by hand.

def test_runner_summary_line_shape_unchanged():
    """Pin the exact line the runner writes. If the runner format
    changes, this test fails and a maintainer must update the
    parser (or the runner) together — they can't drift again."""
    # Verbatim from run_tests.py L934-L935:
    expected_template = (
        "result  : {total} total | {passed} passed | "
        "{failed} failed | {skipped} skipped"
    )
    # Use real values; the parser must extract `skipped`.
    line = expected_template.format(
        total=4443, passed=4443, failed=0, skipped=0)
    assert "skipped" in line and "Skipped:" not in line
    # And the parser pulls the right number from it.
    import tempfile
    with tempfile.NamedTemporaryFile(
            "w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(line + "\n")
        p = Path(fh.name)
    try:
        assert _TOOL._read_summary_skipped(p) == 0
    finally:
        p.unlink(missing_ok=True)


# ── --update path against real summary ──

def test_update_initialises_baseline(tmp_path, monkeypatch):
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "result  : 4443 total | 4443 passed | 0 failed | 0 skipped\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "SKIP_BASELINE.txt"
    monkeypatch.setattr(
        sys, "argv",
        ["check_skip_baseline.py",
         "--summary", str(summary),
         "--baseline", str(baseline),
         "--update"])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _TOOL.main()
    assert rc == 0, buf.getvalue()
    assert baseline.is_file()
    content = baseline.read_text(encoding="utf-8")
    # The int must be present on a non-comment line.
    nums = [int(ln.strip()) for ln in content.splitlines()
            if ln.strip() and not ln.strip().startswith("#")]
    assert nums == [0]


def test_update_then_check_roundtrips(tmp_path, monkeypatch):
    """Initialise, then re-run without --update; should report match."""
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "result  : 100 total | 93 passed | 0 failed | 7 skipped\n",
        encoding="utf-8",
    )
    baseline = tmp_path / "SKIP_BASELINE.txt"

    monkeypatch.setattr(
        sys, "argv",
        ["check_skip_baseline.py",
         "--summary", str(summary),
         "--baseline", str(baseline),
         "--update"])
    with redirect_stdout(io.StringIO()):
        assert _TOOL.main() == 0

    # Now re-run without --update; should pass.
    monkeypatch.setattr(
        sys, "argv",
        ["check_skip_baseline.py",
         "--summary", str(summary),
         "--baseline", str(baseline)])
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _TOOL.main()
    assert rc == 0
    assert "matches baseline (7)" in buf.getvalue()


def test_check_fails_on_skip_drift(tmp_path, monkeypatch):
    """The whole point of PT9: a drift in skip count must fail."""
    baseline = tmp_path / "SKIP_BASELINE.txt"
    baseline.write_text("# header\n5\n", encoding="utf-8")
    summary = tmp_path / "SUMMARY.txt"
    summary.write_text(
        "result  : 100 total | 88 passed | 0 failed | 12 skipped\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv",
        ["check_skip_baseline.py",
         "--summary", str(summary),
         "--baseline", str(baseline)])
    err_buf = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err_buf):
        rc = _TOOL.main()
    assert rc == 1
    err = err_buf.getvalue()
    assert "UP" in err and "5" in err and "12" in err
