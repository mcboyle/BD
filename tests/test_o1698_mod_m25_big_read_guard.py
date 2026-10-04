"""O1698 M25 (FR-33b): the bd-guard plugin denies a whole-file read over 4096 B into context.

Harness cut (RULING-harness-cut-repo-wt.md): the subject is a plugin directory, not repo code. M25 is live since
INSTALL-LOG-wave1 CUT 7, so this row is tests only (no install).
BD_O1698_MOD_M25_BIG_READ_GUARD_CANDIDATE names it (absolute path):
  GREEN  /home/mboyle/bd-persist/plugins/bd-guard (the live plugin, M25 block present)
  RED    a scratch byte-copy of the live plugin whose hooks/register.tsx is the pre-M25 snapshot
         register.tsx.pre-20261003T032149Z (sha256 ee6d672f..., live before CUT 7)
The plugin's own test kit judges it (`claude plugin test`) on a scratch copy; the candidate is never edited. The M25
cases are hooks/m25.test.ts; a candidate without that file gets the FIX copy of it.
Skipped when the env var is unset (remote bands have no claude CLI). A supplied candidate that is absent, or a missing
CLI, fails rather than skips.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_O1698_MOD_M25_BIG_READ_GUARD_CANDIDATE", "")
M25_TEST = Path("/home/mboyle/bd-persist/harness-work/FIX/o1698-mod-m25-big-read-guard/bd-guard/hooks/m25.test.ts")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cand = Path(CANDIDATE)
    assert (cand / "hooks" / "register.tsx").is_file(), f"candidate plugin absent: {cand}"
    claude = shutil.which("claude")
    assert claude, "claude CLI not on PATH: cannot run the plugin test kit"
    work = tmp_path_factory.mktemp("bd-guard") / "bd-guard"
    shutil.copytree(cand, work, ignore=shutil.ignore_patterns("types", "node_modules"))
    if not (work / "hooks" / "m25.test.ts").is_file():
        assert M25_TEST.is_file(), f"M25 cases absent: {M25_TEST}"
        shutil.copy2(M25_TEST, work / "hooks" / "m25.test.ts")
    # Budget: measured <= 8.4 s (.review/TIMING-plugin-test-r3.log); 2x with the 60 s floor, under the 240 s item bound.
    r = subprocess.run([claude, "plugin", "test", str(work)], capture_output=True, text=True, timeout=60, check=False)
    return r.returncode, r.stdout + r.stderr


def _line(out: str, name: str) -> str:
    hits = [ln for ln in out.splitlines() if name in ln and ln.startswith(("(pass)", "(fail)"))]
    assert len(hits) == 1, f"test case {name!r} not reported exactly once:\n{out[-3000:]}"
    return hits[0]


@pytest.mark.parametrize("case", [
    "M25 positive control: cat of a 5 KB file is denied",
    "M25 boundary: 4097 B is denied",
    "M25 fail closed: an unmeasurable single-file read is refused",
    "M25 Read: no limit on a 5 KB file is denied",
    "M25 Read fail closed: an unmeasurable Read target is refused",
    "M25: a 3 KB cat, an exactly-4096 B cat, and a 40-line sed slice",
    "M25: piped, redirected or multi-file forms are never denied",
    "M25: a big read of a SMALL file passes",
])
def test_m25_case_passes(run, case):
    _rc, out = run
    assert _line(out, case).startswith("(pass)"), f"M25-BIG-READ: {case!r} failed:\n{out[-3000:]}"


def test_whole_plugin_suite_green_including_the_skeleton_gates(run):
    rc, out = run
    assert rc == 0 and " 0 fail" in out, f"M25-BIG-READ: plugin suite not green (rc {rc}):\n{out[-3000:]}"
