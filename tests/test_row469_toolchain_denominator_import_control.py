"""TRANSFORM CONTROL band for row 469 (see tests/mutants/*_transform_control.json).

This imports the census module WITHOUT driving the reconciliation decision, so a
mutant of that decision MUST ESCAPE here. That escape is the control which proves
the CAUGHTs in the regression spec are assertion failures, not import breaks.
"""
import os
import sys

BD_GATE_SCOPE = "repo-wide"

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "toolchain", "bin")


def test_module_imports():
    sys.path.insert(0, BIN)
    import bdtools_population as pop
    assert hasattr(pop, "Census")
