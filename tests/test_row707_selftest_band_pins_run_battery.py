"""Row 707 -- pin every ``run_battery`` call site inside ``bd-mutate --selftest``.

The row 707 fix adds a before-verdict subject check inside ``run_battery``.  That
makes ``run_battery`` a changed function, and its call sites are the seams the
self-mutation floor mutates.  Most of them are the ten ``run_battery`` calls in
``_selftest`` -- the tool's own four-state regression battery.  A behavioural
test that drives the CLI's ``main`` path exercises exactly one of them; deleting
any of the others would go uncaught.

``bd-mutate --selftest`` runs that battery and prints ``SELFTEST PASS`` / exits 0
only when every check ran.  Delete any ``run_battery`` call in ``_selftest`` and
the very next line dereferences an undefined ``rows`` -- the run aborts non-zero
with no ``SELFTEST PASS``.  Asserting both here turns each of those deletions
RED, so the floor's census over the changed ``run_battery`` resolves to k=0.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parent.parent
_TOOL = _REPO / "toolchain" / "bin" / "bd-mutate"


def test_bd_mutate_selftest_battery_still_passes():
    # Budget derived from a measured baseline: the selftest battery runs in
    # ~36s here, so 120s is ~3x headroom and stays well under the 240s worker
    # bound (test_v3_66_1222) -- above the bound the timeout could never fire.
    result = subprocess.run(
        [sys.executable, str(_TOOL), "--selftest"],
        capture_output=True, text=True, cwd=_REPO, timeout=120)
    assert result.returncode == 0, (
        f"selftest exit={result.returncode}\n{result.stdout[-2000:]}\n"
        f"{result.stderr[-2000:]}")
    assert "SELFTEST PASS" in result.stdout, result.stdout[-2000:]
