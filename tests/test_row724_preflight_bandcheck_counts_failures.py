"""Row 724: preflight receipts preserve bandcheck's unreadable count."""
from __future__ import annotations
import importlib.machinery, importlib.util, subprocess, sys
import pytest
from pathlib import Path
BD_GATE_SCOPE = "repo-wide"
ROOT = Path(__file__).resolve().parents[1]
def load():
    p = ROOT / "toolchain/bin/bd-cut-preflight"; loader = importlib.machinery.SourceFileLoader("row724_preflight", str(p)); spec = importlib.util.spec_from_loader(loader.name, loader); module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
def test_bandcheck_parser_reports_bad_lines_as_unverifiable():
    text = "  UNSAFE x\n  NOT A FILE y\n  MISSING z\n"
    assert load().p_bandcheck(text) == (3, 0, 3, "band target(s)")
def test_bandcheck_parser_keeps_all_ok_receipts_verified():
    assert load().p_bandcheck("  ok      tests/a.py\n  ok      tests/b.py\n") == (2, 2, 0, "band target(s)")
def test_selftest_e12_exercises_flagged_band_receipt_through_battery():
    tool = ROOT / "toolchain/bin/bd-cut-preflight"
    result = subprocess.run([sys.executable, str(tool), "--selftest"], text=True, capture_output=True, timeout=90)
    out = result.stdout + result.stderr
    assert result.returncode == 0 and "E12" in out and "examined 0 of 3 band target(s)" in out, out
def test_empty_target_line_is_counted_as_a_failure_not_dropped_from_the_denominator():
    # 4 targets: unlike the 3-bad and 2-ok fixtures above, so no assertion here is a
    # literal already present in this file. The EMPTY TARGET line must land in the
    # unverifiable slot AND keep its place in the total -- dropping the regex term
    # would take the refused target out of the denominator altogether.
    t = ("  EMPTY TARGET -- '' names nothing; an empty target is not the tree root.\n"
         "  ok      tests/test_a.py\n  ok      tests/test_b.py\n  ok      tests/test_c.py\n")
    assert load().p_bandcheck(t) == (4, 3, 1, "band target(s)")
def test_empty_target_keeps_its_place_in_the_operator_receipt_end_to_end():
    m = load()
    interp = m._find_pytest_python()
    if interp is None: pytest.skip("no interpreter on this host can import pytest; the real battery cannot run")
    empty_line = "  EMPTY TARGET -- '' names nothing; an empty target is not the tree root."
    ok_line = "  ok      tests/test_a.py"
    tools = dict(m._E2E_GOOD)
    tools["bd-bandcheck"] = "import sys\nprint(%r)\nprint(%r)\nsys.exit(2)\n" % (empty_line, ok_line)
    root, logdir = m._fixture(interp, tools, "row724-empty-target")
    assert root is not None, "git could not build the fixture repo"
    try:
        # PRECONDITION, on the fixture rather than on the seam under test: the hazard
        # really is in the corpus the REAL battery is about to run. Asserting the parser
        # tuple here instead would let the precondition, not the operator receipt, be the
        # thing that catches a mutation of the classification.
        stub = Path(root) / "toolchain" / "bin" / "bd-bandcheck"
        assert stub.is_file() and "EMPTY TARGET" in stub.read_text(), stub
        rc, out = m._run_battery(root, logdir)
        st, why = m._row(out, "bd-bandcheck")
        assert st is not None, out
        # THE OPERATOR-VISIBLE RECEIPT: the refused target keeps its place in the
        # denominator (2, not 1) and is counted as unverifiable, not silently dropped.
        assert "verified 1 of 2 band target(s), 1 unverifiable" in why, "%s %s\n%s" % (st, why, out)
        assert st == m.UNKNOWN, "%s %s" % (st, why)
        assert rc != 0, "rc=%s\n%s" % (rc, out)
    finally:
        m._cleanup(root)
