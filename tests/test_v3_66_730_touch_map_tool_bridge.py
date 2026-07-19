"""v3.66.730 -- tool_bridge.py gets a CURATED band row (TOUCH_MAP), plus map soundness.

WHY: bulk_downloader/tool_bridge.py is the exec bridge -- the single highest-
consequence file in the tree (it is the one place the GUI can subprocess into an
allowlisted binary). Until this cut its band coverage came from EXACTLY ONE
signal: bd-band-derive's mechanical consumer grep (Signal 4 @728). One signal is
one regression away from zero. This cut adds the second, independent signal the
backlog asked for: a curated row in precut_check.TOUCH_MAP, so the pre-cut gate
predicts the exec-bridge suites even if the mechanical grep ever goes blind.

Also pins two soundness properties of the map itself:
  * every suite a TOUCH_MAP row names must EXIST in tests/ -- a row pointing at a
    deleted suite is a gate that cannot see and reports a band anyway;
  * the tool_bridge row's band must include BOTH exec-bridge suites, not one.

Zero-arg tests; repo root via __file__; stdlib only.
"""
import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "precut_check", REPO / "tools" / "precut_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_tool_bridge_has_a_curated_touch_map_row():
    """The exec bridge must be covered by the CURATED signal, not only the grep."""
    m = _load()
    target = "bulk_downloader/tool_bridge.py"
    hits = [pat for pat, _tests in m.TOUCH_MAP if re.search(pat, target)]
    assert hits, (
        "TOUCH_MAP has no row matching bulk_downloader/tool_bridge.py. The exec "
        "bridge is the highest-consequence file in the tree and must be covered "
        "by TWO independent signals (curated map + mechanical derive), not one.")


def test_tool_bridge_band_names_both_exec_bridge_suites():
    """A curated row that bands only half the exec-bridge suites is half a gate."""
    m = _load()
    target = "bulk_downloader/tool_bridge.py"
    band = set()
    for pat, tests in m.TOUCH_MAP:
        if re.search(pat, target):
            band.update(tests)
    for required in ("test_v3_66_717_exec_bridge", "test_v3_66_719_tools_control"):
        assert required in band, (
            f"TOUCH_MAP row for tool_bridge.py must band {required}; "
            f"curated band was {sorted(band)}")


def test_every_touch_map_suite_exists_on_disk():
    """A map row naming a deleted suite predicts a band that cannot run.

    That is the 'gate that cannot see' shape: precut would print the suite name,
    the runner would match nothing, and the cut would look banded while testing
    nothing. The map must only ever name suites that exist.
    """
    m = _load()
    missing = []
    for pat, tests in m.TOUCH_MAP:
        for t in tests:
            if not (REPO / "tests" / f"{t}.py").exists():
                missing.append((pat, t))
    assert not missing, f"TOUCH_MAP names suites that do not exist: {missing}"
