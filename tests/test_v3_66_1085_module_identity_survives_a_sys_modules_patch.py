"""v3.66.1085 -- a `patch.dict(sys.modules, ...)` must not split a module identity.

THE DEFECT, from production. test6's capture at v3.66.1083 failed one unit test
in the parallel lane, worker gw27:
`test_v3_43_26_qb_bridge::test_qb_submit_raises_qberror_on_unreachable`, with
"submit raised ConnectError instead of QBError". The traceback named
`httpcore.ConnectError`, not `httpx.ConnectError` -- httpx re-raised the raw
transport error out of its own `map_httpcore_exceptions` context, through the
branch httpx itself marks `# pragma: no cover`.

THE MECHANISM, measured. `unittest.mock.patch.dict` restores the dict to the
snapshot it took on entry, so any module FIRST IMPORTED inside the block is
DELETED on exit -- a side effect nobody writes and nothing gates. The chain
recorded in /tmp/bd-runctx/2521/gw27.chain on test6 was:

    tests/test_v3_43_64_mp4_metadata.py          <- patches sys.modules["httpx"]
    tests/test_v3_66_25_phase4_ssrf_rebinding.py <- re-imports httpcore
    tests/test_v3_43_26_qb_bridge.py             <- the victim

`httpx` was already imported, so it was in the snapshot and SURVIVED. `httpcore`
was first imported inside the block -- by httpx's own lazy
`_load_httpcore_exceptions()`, which builds the module-global
`HTTPCORE_EXC_MAP` -- so it was EVICTED. The next import produced a second
`httpcore` module object with different exception classes, and from that moment
every `isinstance(exc, from_exc)` in the map failed, for the rest of that worker
process.

THE ASYMMETRY IS THE WHOLE THING, and it is why a first attempt at this test
failed to reproduce: evicting httpx AND httpcore together is self-healing,
because httpx's transport module carries the map and re-importing it resets the
map to empty, after which it rebuilds from the live httpcore. Only the case
where the MAP survives and the CLASSES do not produces the split.

THE FIX is one import in `tests/conftest.py`: httpcore is present before any
test runs, so it is in every later snapshot and no restore can evict it.

WHAT THIS GATE CANNOT SEE, stated because a gate that cannot see its subject
reports OK: it is not a general guard against `patch.dict(sys.modules, ...)`.
That trap applies to ANY lazily-imported module backing an identity-keyed
cache, and 28 call sites across 6 test files use the idiom today. This closes
the instance that reached production and names the class; it does not close the
class.
"""
from __future__ import annotations

BD_GATE_SCOPE = "repo-wide"

import pathlib
import subprocess
import sys
from unittest.mock import patch

import pytest

_REPO = pathlib.Path(__file__).resolve().parent.parent

# sys.executable, NOT a hardcoded venv/bin/python. CLAUDE.md section 5's rule
# ("the interpreter is venv/bin/python, never bare python3") is about the BOX
# and the cloud container, where a bare python3 is 3.11 without the project
# dependencies. A GitHub runner is a third environment and has no venv/ at all:
# this file shipped with the hardcoded path, went green on test5, and CI's
# isolation shard failed with
# `FileNotFoundError: /home/runner/work/BD/BD/venv/bin/python`.
# sys.executable is right in all three, and it also guarantees the child has
# the SAME httpx and httpcore as the process making the assertion -- which is
# the entire subject here, so a different interpreter would not just fail to
# run, it would measure the wrong thing.
_PY = sys.executable


def test_httpcore_is_in_the_module_table_before_any_test_patches_it():
    """The property `tests/conftest.py` buys, asserted directly.

    RED on pristine source when this file runs alone: nothing imported httpcore
    at collection time, so the key is absent and this raises KeyError.
    """
    assert "httpcore" in sys.modules, (
        "httpcore is not in sys.modules. tests/conftest.py imports it at "
        "import time precisely so that every later patch.dict(sys.modules) "
        "snapshot contains it -- without that, the first test to import it "
        "inside such a block hands its eviction to everything downstream.")


def test_a_sys_modules_patch_cannot_evict_httpcore():
    """The eviction itself, exercised rather than reasoned about."""
    before = sys.modules["httpcore"]
    with patch.dict(sys.modules, {"_bd_identity_probe": object()}):
        import httpcore  # noqa: F401  -- would be a FIRST import without the fix
        assert "httpcore" in sys.modules
    assert "httpcore" in sys.modules, (
        "patch.dict evicted httpcore on exit -- it was not in the snapshot, "
        "so 'restore' deleted it")
    assert sys.modules["httpcore"] is before, (
        "httpcore was replaced by a different module object; every exception "
        "class in httpx's HTTPCORE_EXC_MAP now belongs to the old one")


def test_httpx_maps_a_refused_connection_to_its_own_exception():
    """The behavioural end-state, which is what actually broke.

    This is a REGRESSION GUARD, not an escape test: in a clean process it is
    green before and after the fix. It goes red only in a process whose map has
    already been poisoned, which is exactly the state test6 was in.
    """
    import httpx

    with pytest.raises(httpx.ConnectError):
        with httpx.Client(timeout=5) as client:
            client.get("http://127.0.0.1:1/")


def _arm(preimport: bool) -> str:
    """Run one arm of the mechanism in a clean interpreter and return what the
    second request raised, as `module.ClassName`."""
    script = (
        "import sys\n"
        "from unittest.mock import patch\n"
        # httpx OUTSIDE the block, so the map survives; httpcore's fate is the
        # variable under test.
        "import httpx\n"
        + ("import httpcore\n" if preimport else "")
        + "with patch.dict(sys.modules, {'_bd_identity_probe': object()}):\n"
        "    try:\n"
        "        httpx.Client(timeout=5).get('http://127.0.0.1:1/')\n"
        "    except Exception:\n"
        "        pass\n"
        "try:\n"
        "    httpx.Client(timeout=5).get('http://127.0.0.1:1/')\n"
        "except Exception as e:\n"
        "    print(type(e).__module__ + '.' + type(e).__name__)\n"
    )
    proc = subprocess.run([str(_PY), "-c", script], capture_output=True,
                          text=True, timeout=120, cwd=str(_REPO))
    assert proc.returncode == 0, (
        f"the arm itself failed to run (exit {proc.returncode}), so it "
        f"measured nothing:\n{proc.stderr}")
    out = proc.stdout.strip()
    assert out, (
        "the arm produced no output, so the second request did not raise at "
        "all -- something is listening on 127.0.0.1:1 and this experiment is "
        "invalid")
    return out


def test_the_trap_is_real():
    """PRECONDITION for the test below: without the pre-import, the split
    happens. Asserted separately so that a green result there cannot come from
    an experiment that was never capable of failing.

    This does not depend on conftest -- the arm controls its own interpreter.
    """
    assert _arm(preimport=False) == "httpcore.ConnectError", (
        "the poisoning did not reproduce, so the arm below proves nothing. "
        "Either httpx changed its lazy-map mechanism or patch.dict changed "
        "its restore semantics; re-derive before trusting this file.")


def test_the_preimport_closes_it():
    """The fix, in the same controlled interpreter as the trap above."""
    assert _arm(preimport=True) == "httpx.ConnectError", (
        "httpcore was pre-imported and the map STILL did not match. The fix "
        "in tests/conftest.py does not close the mechanism it was written for.")
