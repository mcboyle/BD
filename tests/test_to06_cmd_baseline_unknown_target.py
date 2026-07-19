"""RED-first repro for F-TO06-01.

``bd_decomp_lib.cmd_baseline`` indexes ``TARGETS[target]`` with no guard, so an
unknown target raises ``KeyError`` (crash) instead of the clean ``rc=2`` its
sibling ``cmd_check`` returns. After the fix cmd_baseline mirrors cmd_check's
unknown-target guard.

Pristine RED: cmd_baseline(unknown) raises KeyError.
"""
import importlib.util
from pathlib import Path


def _load():
    p = Path(__file__).resolve().parent.parent / "tools" / "bd_decomp_lib.py"
    spec = importlib.util.spec_from_file_location("_bd_decomp_lib_t", str(p))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_cmd_baseline_rejects_unknown_target():
    m = _load()

    class A:
        target = "no_such_target_xyz"
        root = "/tmp"

    rc = m.cmd_baseline(A())
    assert rc == 2, rc
