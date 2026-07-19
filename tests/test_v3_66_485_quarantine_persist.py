"""v3.66.485 R2 (plugin-v3): persist + decay quarantine + transition hooks.

Today ``_quarantine`` is in-memory only: it is lost on restart and never
decays, so a plugin that failed a transient blip stays dead until the process
restarts, and a plugin quarantined just before a restart comes back un-marked.
R2 makes quarantine durable AND self-healing:

  * **Persist.** The quarantine map is written to ``<plugin_dir>/.plugin_state.json``
    on every transition and re-hydrated on load, so it survives a restart.
  * **Decay / cooldown re-probe.** A quarantined plugin is SKIPPED only while
    inside ``_QUARANTINE_COOLDOWN``; once the cooldown elapses the next call is a
    **re-probe** -- on success the plugin recovers (quarantine cleared); on
    failure it re-quarantines WITHOUT resetting the accumulated fail count.
    (Mirrors the 470 ``site.cooldown`` / ``site.recovered`` semantics.)
  * **Transition hooks.** ``plugin.quarantined`` fires once when a key crosses
    the fail budget; ``plugin.recovered`` fires once when a re-probe heals it.

Runner-safe: zero-arg fns, no pytest builtins, paths from __file__, tempfile,
module globals restored in try/finally.
"""
import json
import sys
import tempfile
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from bulk_downloader import plugins as P  # noqa: E402


def _with_plugin_dir(tmp):
    orig = P._plugin_dir
    P._plugin_dir = lambda: Path(tmp)
    return orig


def _quarantine_key(name):
    """Drive a synthetic key to (just past) the fail budget via _record_fail."""
    key = f"processor:test.{name}"
    for _ in range(P._FAIL_BUDGET):
        P._record_fail(key, RuntimeError("boom"))
    return key


def test_quarantine_persists_across_reload():
    """(a) A quarantine survives a simulated process restart (disk re-hydrate)."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    try:
        P.reset()
        key = _quarantine_key("persist")
        assert P._is_quarantined(key), "should be quarantined in-memory"
        # state file written under the plugin dir
        sp = P._quarantine_state_path()
        assert Path(sp).is_file(), f"no state file at {sp}"
        # simulate a restart: drop in-memory state, re-hydrate from disk
        P._quarantine.clear()
        assert not P._is_quarantined(key)
        P._load_quarantine_state()
        assert P._is_quarantined(key), "quarantine did not survive reload"
    finally:
        P._plugin_dir = orig
        P.reset()


def test_decay_reprobes_after_cooldown_and_clears_on_success():
    """(b) After the cooldown, a healthy plugin re-probes and RECOVERS."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    old_cd = P._QUARANTINE_COOLDOWN
    try:
        P.reset()
        P._QUARANTINE_COOLDOWN = 0.3
        state = {"fail": True}

        def flaky(payload):
            if state["fail"]:
                raise RuntimeError("still broken")
            return {"ok": True}

        P.register_processor(flaky, priority=50, name="flaky")
        # trip the budget -> quarantined
        for _ in range(P._FAIL_BUDGET):
            P.run_processors({})
        key = P._qkey(flaky, "processor:")
        assert P._is_quarantined(key), "should be quarantined"
        # within cooldown: still skipped (no re-probe)
        out = P.run_processors({})
        assert all(r["name"] != "flaky" or not r["ok"] for r in out)
        # heal + wait past cooldown -> next run re-probes and clears
        state["fail"] = False
        time.sleep(0.35)
        out = P.run_processors({})
        got = [r for r in out if r["name"] == "flaky"]
        assert got and got[0]["ok"], out
        assert not P._is_quarantined(key), "should have recovered"
    finally:
        P._QUARANTINE_COOLDOWN = old_cd
        P._plugin_dir = orig
        P.reset()


def test_reprobe_failure_requarantines_keeping_failcount():
    """(c) A failed re-probe re-quarantines and does NOT reset the fail count."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    old_cd = P._QUARANTINE_COOLDOWN
    try:
        P.reset()
        P._QUARANTINE_COOLDOWN = 0.3

        def broken(payload):
            raise RuntimeError("always broken")

        P.register_processor(broken, priority=50, name="broken")
        for _ in range(P._FAIL_BUDGET):
            P.run_processors({})
        key = P._qkey(broken, "processor:")
        fails_before = P._quarantine[key]["fails"]
        assert fails_before >= P._FAIL_BUDGET
        # past cooldown -> re-probe fires, fails again -> re-quarantine
        time.sleep(0.35)
        P.run_processors({})
        assert P._is_quarantined(key), "should be re-quarantined"
        assert P._quarantine[key]["fails"] > fails_before, "fail count must not reset"
    finally:
        P._QUARANTINE_COOLDOWN = old_cd
        P._plugin_dir = orig
        P.reset()


def test_plugin_recovered_hook_fires_exactly_once():
    """(d) plugin.recovered fires exactly once on the heal transition."""
    tmp = tempfile.mkdtemp()
    orig = _with_plugin_dir(tmp)
    old_cd = P._QUARANTINE_COOLDOWN
    try:
        P.reset()
        P._QUARANTINE_COOLDOWN = 0.3
        fired = {"recovered": 0, "quarantined": 0}
        P.register_hook("plugin.recovered", lambda p: fired.__setitem__(
            "recovered", fired["recovered"] + 1))
        P.register_hook("plugin.quarantined", lambda p: fired.__setitem__(
            "quarantined", fired["quarantined"] + 1))
        state = {"fail": True}

        def flaky(payload):
            if state["fail"]:
                raise RuntimeError("broken")
            return {"ok": True}

        P.register_processor(flaky, priority=50, name="flaky2")
        for _ in range(P._FAIL_BUDGET):
            P.run_processors({})
        assert fired["quarantined"] == 1, fired
        state["fail"] = False
        time.sleep(0.35)
        P.run_processors({})       # re-probe -> recover
        P.run_processors({})       # already healthy; must NOT fire again
        assert fired["recovered"] == 1, fired
    finally:
        P._QUARANTINE_COOLDOWN = old_cd
        P._plugin_dir = orig
        P.reset()


def test_transition_events_documented():
    """plugin.quarantined / plugin.recovered are in the HOOK_EVENTS doc registry."""
    ev = P.known_events()["hooks"]
    assert "plugin.quarantined" in ev
    assert "plugin.recovered" in ev
