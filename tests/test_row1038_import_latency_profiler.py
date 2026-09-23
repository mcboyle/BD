"""Row 1038 -- a cold-start kernel and module import-latency profiler.

MEASURED ON BASE bc1544b7, not assumed:

    grep -rlniE 'import_latency|import_time|cold[_ ]start|importtime' bulk_downloader/ tools/ -> 6 files
      tools/nuitka_eval.py     measures ONE number: seconds for a COMPILED binary to answer /health
      the other five are unrelated ("_import_time" locals, a startup_s budget constant)
    bulk_downloader/perf_lab.py (704 lines) profiles RSS, child processes, tracemalloc, interpreter
      stats -- everything about a RUNNING process, nothing about what importing it cost
    positive control, same probe shape: 'turnstile' -> 41 files

So cold start is measurable only as a single end-to-end number, and only for the packaged binary.
Nothing can answer the question an import regression actually poses: WHICH module cost the time.
``python -X importtime`` already prints that table on every run; no code in the repo reads it.

The first test below is importable ON BASE -- it asserts against perf_lab, which exists -- so it
fails with an AssertionError naming the gap, not an ImportError about the module this row adds.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

from bulk_downloader import perf_lab

BD_GATE_SCOPE = "repo-wide"

ROOT = Path(__file__).resolve().parent.parent

# A COMPLETE -X importtime subtree in CPython's exact format. Complete matters: the arithmetic
# below (parent cumulative == own self + children cumulative) only holds when no child is missing,
# and a truncated table would make that check unfalsifiable. Indentation IS the import tree: a
# deeper row is a child whose cumulative time is already inside its parent's cumulative.
# The parser is additionally run against a LIVE `-X importtime` table further down, so it is not
# only ever shown a shape that was written to match it.
SAMPLE = """import time:       153 |        153 |         ntpath
import time:      1040 |       1193 |       pathlib
import time:      2203 |       3396 |     bulk_downloader._envfile
import time:      2457 |       5853 |   bulk_downloader
import time:      3098 |       8951 | bulk_downloader.app_kernel
"""

def _profiler():
    """Import the subject lazily: on base this raises inside ONE test, not at collection."""
    return importlib.import_module('bulk_downloader.import_profiler')


# -- 1. the row's reason, asserted against a module that exists on base --------------

def test_the_repo_can_attribute_cold_start_to_a_module_not_just_time_it():
    snapshot = perf_lab.snapshot()
    assert isinstance(snapshot, dict), "probe read no perf_lab snapshot at all"
    # positive control: perf_lab really does answer runtime questions, so a missing import
    # attribution below is a MISSING CAPABILITY and not a dead probe.
    assert any(key in snapshot for key in ("rss_bytes", "interpreter", "app")), (
        f"perf_lab.snapshot() returned {sorted(snapshot)}; the probe is not reading it")
    assert (ROOT / 'bulk_downloader/import_profiler.py').is_file(), (
        "cold start is measurable only as one end-to-end number for the packaged binary "
        "(tools/nuitka_eval.py startup_s); perf_lab profiles the RUNNING process and nothing "
        "attributes import latency to a module, so an import regression has no owner")


# -- 2. the parser: CPython's table, read as a tree --------------------------------

def test_the_table_is_parsed_as_a_tree_with_self_and_cumulative_kept_apart():
    prof = _profiler()
    rows = prof.parse_importtime(SAMPLE)
    by_name = {row['module']: row for row in rows}
    assert set(by_name) == {'ntpath', 'pathlib', 'bulk_downloader._envfile',
                            'bulk_downloader', 'bulk_downloader.app_kernel'}
    kernel = by_name['bulk_downloader.app_kernel']
    assert kernel['self_us'] == 3098 and kernel['cumulative_us'] == 8951
    assert kernel['depth'] == 0 and by_name['bulk_downloader']['depth'] == 1
    assert by_name['bulk_downloader']['parent'] == 'bulk_downloader.app_kernel'
    assert by_name['ntpath']['parent'] == 'pathlib'
    # The distinction is the whole point: pathlib is 1.0ms of its own work, 1.2ms of total cost.
    assert by_name['pathlib']['self_us'] < by_name['pathlib']['cumulative_us']


def test_the_parse_is_checked_against_the_arithmetic_cpython_guarantees():
    """NEGATIVE CONTROL for the tree: a parent's cumulative must equal its own self time plus its
    children's cumulative. A parser that mis-assigns depth breaks this, so the test cannot pass by
    accident on a table it merely split into lines."""
    prof = _profiler()
    rows = prof.parse_importtime(SAMPLE)
    residuals = prof.tree_residuals(rows)
    assert residuals == {}, f"cumulative != self + children for: {residuals}"
    # And against a LIVE table, so the parser is held to CPython's real output and not only to a
    # constant in this file. encodings.idna is chosen because its tree is small and complete.
    live = prof.capture_importtime('encodings.idna', python=sys.executable, timeout=120.0)
    live_rows = prof.parse_importtime(live)
    assert len(live_rows) > 20, f"the live -X importtime table came back with {len(live_rows)} rows"
    # CPython rounds every number in the table to whole microseconds independently, so a parent's
    # printed cumulative can sit a microsecond per child below the sum of what it printed for them.
    # MEASURED here: the residuals are single-digit microseconds. The tolerance is one microsecond
    # per row -- large enough to absorb that rounding, far too small to hide a mis-parented subtree,
    # which the scrambled control below shows lands in the thousands.
    live_residuals = prof.tree_residuals(live_rows, tolerance_us=len(live_rows))
    assert live_residuals == {}, f"the parser does not survive a real table: {live_residuals}"
    assert all(abs(delta) <= 8 for delta in prof.tree_residuals(live_rows).values()), (
        "the untoleranced residuals on a real table must be rounding-sized, not structural")
    scrambled = SAMPLE.replace("   bulk_downloader\n", " bulk_downloader\n")
    assert prof.tree_residuals(prof.parse_importtime(scrambled)) != {}, (
        "a table whose indentation was corrupted must FAIL the arithmetic; if it does not, the "
        "check is not reading the tree and its pass above means nothing")


def test_junk_lines_are_ignored_rather_than_guessed_at():
    prof = _profiler()
    noisy = "Traceback (most recent call last):\n" + SAMPLE + "import time: bad | rows | here\n"
    assert len(prof.parse_importtime(noisy)) == 5
    assert prof.parse_importtime("") == []


# -- 3. the profiler: a real cold start, attributed --------------------------------

def test_a_real_cold_start_is_measured_and_attributed_to_in_package_modules():
    prof = _profiler()
    report = prof.profile_cold_start('bulk_downloader.app_kernel', python=sys.executable,
                                     cwd=str(ROOT), timeout=120.0)
    assert report['ok'] is True, report.get('error')
    assert report['total_us'] > 0
    owned = report['owned']
    assert owned, "no bulk_downloader module was attributed any import cost"
    assert all(name.startswith('bulk_downloader') for name, _ in owned)
    assert owned == sorted(owned, key=lambda item: -item[1]), "costs must come back ranked"
    assert report['module'] == 'bulk_downloader.app_kernel'
    # The report must be able to say "I could not measure" instead of returning a zero that reads
    # like a fast import -- the same fail-loud discipline the rest of the repo uses.
    broken = prof.profile_cold_start('bulk_downloader.this_module_does_not_exist',
                                     python=sys.executable, cwd=str(ROOT), timeout=120.0)
    assert broken['ok'] is False and broken['total_us'] is None
    assert 'ModuleNotFoundError' in (broken.get('error') or '')


def test_a_budget_names_the_module_that_broke_it():
    prof = _profiler()
    rows = prof.parse_importtime(SAMPLE)
    within = prof.check_budget(rows, budget_us=30000)
    assert within['ok'] is True and within['total_us'] == 8951
    over = prof.check_budget(rows, budget_us=5000)
    assert over['ok'] is False
    # Ranked by SELF time among the modules the package owns, with the root excluded: the root's
    # cumulative is the whole measurement, so naming it is the same as naming nothing, and naming
    # pathlib tells the operator to fix the standard library.
    assert over['worst'][0] == 'bulk_downloader', (
        f"budget breach blamed {over['worst']!r}; it must name the costliest OWNED module by self "
        "time with the profiled root excluded")
    assert over['worst'][1] == 2457
    assert [name for name, _ in over['owned']] == ['bulk_downloader', 'bulk_downloader._envfile']


def test_the_profiler_is_reachable_from_perf_lab_and_is_not_inside_snapshot():
    """WIRING: the one caller. It must be an explicit call, because profiling spawns a fresh
    interpreter -- putting that inside snapshot() would make a read-only probe cost a process."""
    assert hasattr(perf_lab, 'import_latency')
    assert 'import_latency' not in perf_lab.snapshot(), (
        "snapshot() is documented read-only and safe anytime; a subprocess cold start is neither")
    report = perf_lab.import_latency('bulk_downloader._envfile', budget_us=10 ** 9)
    assert report['ok'] is True and report['budget']['ok'] is True
    assert report['budget']['total_us'] == report['total_us']


# ---- rebuild (ORDERS-2345): an operator entry point, E1 of both REFUTEs ----

def _run_bdctl_doctor(monkeypatch, capsys, *argv):
    """Drive bdctl's real argv parser; no server may be contacted for a local profile."""
    import bdctl

    def no_server(*a, **k):
        raise AssertionError("doctor --import-latency contacted the server: %r" % (a,))

    monkeypatch.setattr(bdctl, "_request", no_server)
    monkeypatch.setattr(sys, "argv", ["bdctl", "doctor", *argv])
    code = 0
    try:
        bdctl.main()
    except SystemExit as exc:
        code = exc.code
    out = capsys.readouterr()
    return code, out.out, out.err


def test_bdctl_doctor_import_latency_attributes_the_cold_start(monkeypatch, capsys):
    """RED on the prior tree: import_latency had no caller; bdctl doctor rejected the flag."""
    import json

    code, out, err = _run_bdctl_doctor(monkeypatch, capsys, "--import-latency",
                                       "bulk_downloader.import_profiler", "--json")
    assert code == 0, "bdctl doctor --import-latency is not an operator entry point: exit %r %s" % (code, err[-300:])
    report = json.loads(out)
    assert report["ok"] is True and report["module"] == "bulk_downloader.import_profiler"
    assert report["total_us"] > 0
    assert report["owned"], "no bulk_downloader module attributed"


def test_bdctl_doctor_import_latency_budget_breach_exits_1_and_names_the_worst(monkeypatch, capsys):
    code, out, err = _run_bdctl_doctor(monkeypatch, capsys, "--import-latency",
                                       "bulk_downloader.import_profiler", "--budget-us", "1")
    assert code == 1, (code, err[-300:])
    assert "OVER by" in out and "worst: bulk_downloader." in out, out


def test_bdctl_doctor_import_latency_unmeasured_exits_2(monkeypatch, capsys):
    """Fail closed: a module that did not import is 'not measured', never a fast cold start."""
    code, out, err = _run_bdctl_doctor(monkeypatch, capsys, "--import-latency",
                                       "bulk_downloader.no_such_module_1038")
    assert code == 2, (code, out)
    assert "not measured" in err and "ModuleNotFoundError" in err, err


def test_a_non_module_name_is_refused_before_any_interpreter_runs(monkeypatch):
    """The name is spliced into `-c "import <name>"`; anything but a dotted name is refused unrun."""
    prof = _profiler()

    def must_not_run(*a, **k):
        raise AssertionError("an interpreter was launched for an invalid module name")

    monkeypatch.setattr(prof.subprocess, "run", must_not_run)
    report = prof.profile_cold_start("os; print('x')")
    assert report["ok"] is False and report["total_us"] is None
    assert "not a dotted module name" in report["error"]
    import pytest
    with pytest.raises(ValueError, match="not a dotted module name"):
        prof.capture_importtime("os; print('x')")


def test_a_rootless_budget_sums_every_top_level_import():
    """No root=: the interpreter paid for every top-level import, so the total is their SUM."""
    rows = _profiler().parse_importtime(
        "import time:       100 |        100 | site\n"
        "import time:       300 |        300 | encodings\n")
    budget = _profiler().check_budget(rows, budget_us=350)
    assert budget["total_us"] == 400
    assert budget["ok"] is False and budget["over_us"] == 50
