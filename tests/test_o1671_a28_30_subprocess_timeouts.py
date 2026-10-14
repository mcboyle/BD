"""O1671 AUDIT-28/29/30 (MED RELIABILITY): six test files ran subprocess.run with no timeout, so a
hung child (git waiting on a lock, a tool blocked on stdin, `ip netns`) wedged the whole runner
instead of failing one test. Every subprocess.run/call/check_call/check_output in those files must
pass timeout=. A call that forwards **kwargs is not judged (the timeout may ride in the mapping)."""

import ast
import pathlib

import pytest

BD_GATE_SCOPE = "module"

_TESTS = pathlib.Path(__file__).resolve().parent
_FUNCS = {"run", "call", "check_call", "check_output"}
AUDITED = (
    "test_v3_66_151_normalizer.py",
    "test_v3_66_155_pipeline_roundtrip.py",
    "test_v3_66_321_data_layer_path.py",
    "test_v3_66_701_browser_netns_launch_wiring.py",
    "test_v3_66_872_claim_survives_the_shell.py",
    "test_v3_66_929_doctruth_sees_the_contract.py",
)


def _untimed_calls(src):
    """Line numbers of subprocess.<run|call|check_call|check_output>(...) calls with no timeout=."""
    out = []
    for node in ast.walk(ast.parse(src)):
        fn = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(fn, ast.Attribute) and fn.attr in _FUNCS
                and isinstance(fn.value, ast.Name) and fn.value.id == "subprocess"):
            continue
        kws = {k.arg for k in node.keywords}
        if "timeout" not in kws and None not in kws:
            out.append(node.lineno)
    return out


def test_scanner_sees_an_untimed_call_and_passes_a_timed_one():
    """Positive and negative control for the scanner itself, so a zero below means something."""
    assert _untimed_calls("import subprocess\nsubprocess.run(['true'], check=True)\n") == [2]
    assert _untimed_calls("import subprocess\nsubprocess.check_output(\n    ['true'],\n)\n") == [2]
    assert _untimed_calls("import subprocess\nsubprocess.run(['true'], timeout=5)\n") == []
    assert _untimed_calls("import subprocess\nsubprocess.run(['true'], **kw)\n") == []


@pytest.mark.parametrize("name", AUDITED)
def test_audited_file_has_no_subprocess_call_without_timeout(name):
    src = (_TESTS / name).read_text(encoding="utf-8")
    assert "subprocess." in src, f"{name}: no subprocess call left to judge (scope drifted?)"
    bad = _untimed_calls(src)
    assert not bad, (
        f"O1671-A28-30 UNTIMED-SUBPROCESS {name}: subprocess call(s) without timeout= at line(s) "
        f"{bad}; a hung child blocks the runner. Pass timeout=."
    )
