"""Folded-in phase boundary test (v3.66.91).

Wraps the standalone phase test (in tests/_phase_scripts/) as a runner-discoverable
test_ function executed from the repo root, so its subprocess `tools/<tool>.py` calls
resolve. The wrapped script asserts internally and exits non-zero on any failure;
this test propagates that.
"""
import os
import sys
import subprocess

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.dirname(os.path.abspath(__file__))
_MODULE = "_phase_scripts.phase02_selector"


def test_phase_boundaries():
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (_TESTS, env.get("PYTHONPATH"))))
    r = subprocess.run([sys.executable, "-m", _MODULE], cwd=_REPO, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, "phase test failed:\n" + r.stdout + "\n" + r.stderr
