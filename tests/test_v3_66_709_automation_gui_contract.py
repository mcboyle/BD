"""v3.66.709 (A-GUI Cut 1) -- the global_config WRITE CONTRACT.

The defect this pins is not "21 keys are missing from a schema". It is that the
write path answers **200 to a write it discards**:

    POST /api/global_config {"automation.master_off_switch": true}  -> 200 OK
    GET  /api/global_config                                         -> key absent

`app_global_config` iterates GLOBAL_CONFIG_SCHEMA and skips anything not in it, so
an undeclared key is silently dropped. The endpoint's own comment describes the
bug it was written to fix ("the 306 latent bug where schema keys with no explicit
branch were silently dropped (POST 200, nothing written)") -- and the same bug
survives, in the one place it matters most: `automation.master_off_switch` is the
EMERGENCY STOP for all autonomous action, and today it cannot be turned on.

So this file pins BOTH halves, contract first:

  1. an unknown key is REJECTED (400), not accepted-and-dropped. Fix the contract
     and the next undeclared key cannot re-create this silently.
  2. every automation key the runtime READS is declared -- a self-maintaining gate:
     add a reader without a declaration and the build fails.
  3. the off-switch actually persists.
"""
import json

import pytest

from bulk_downloader.app import app
from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA


def _client():
    return app.test_client()


def _csrf(c):
    j = c.get("/api/csrf").get_json() or {}
    t = j.get("csrf_token") or j.get("token")
    return {"X-CSRFToken": t, "X-CSRF-Token": t, "Content-Type": "application/json"}


def _read_automation_keys():
    """Every automation.* global_config key the RUNTIME actually reads."""
    from bulk_downloader import automation_controller as ac
    from bulk_downloader import lifecycle_automation as la

    keys = set(la.AUTOMATION_TOGGLES.values())
    keys.add(ac._OFF_SWITCH_KEY)
    return keys


def test_every_read_automation_key_is_declared():
    """A key the runtime reads but the schema does not declare is UNWRITABLE:
    the generic write path iterates the schema, so a POST for it returns 200 and
    writes nothing. This gate makes that impossible to reintroduce."""
    read = _read_automation_keys()
    undeclared = sorted(k for k in read if k not in GLOBAL_CONFIG_SCHEMA)
    assert not undeclared, (
        "automation keys READ at runtime but NOT declared in GLOBAL_CONFIG_SCHEMA "
        "-- writes to these silently no-op (POST 200, nothing written): %s" % undeclared)


def test_master_off_switch_persists():
    """The EMERGENCY STOP must be settable. Today: POST 200, key absent on GET."""
    c = _client()
    h = _csrf(c)
    r = c.post("/api/global_config",
               data=json.dumps({"automation.master_off_switch": True}), headers=h)
    assert r.status_code == 200, r.get_json()
    got = (c.get("/api/global_config").get_json() or {})
    assert "automation.master_off_switch" in got, (
        "POST returned 200 but the key is absent on read-back -- the write was "
        "silently discarded")
    assert got["automation.master_off_switch"] is True


def test_off_switch_is_safety_bearing_and_fails_closed_to_engaged():
    """A bad type on the kill switch must fail-CLOSED to ENGAGED (stop autonomy),
    never fail-open to 'not engaged'. safe_default is the fail-closed value."""
    spec = GLOBAL_CONFIG_SCHEMA.get("automation.master_off_switch")
    assert spec, "master_off_switch not declared"
    assert spec.get("safety") is True, "the kill switch must be safety-bearing"
    assert spec.get("safe_default") is True, (
        "fail-closed for the kill switch means ENGAGED (True) -- a safe_default of "
        "False would fail OPEN, leaving autonomy running on a malformed config")


def test_unknown_key_is_rejected_not_silently_dropped():
    """THE CONTRACT. An unrecognised key must 400. Returning 200 for a write that
    is discarded is what allowed the off-switch to be unwritable for 200+ cuts
    while every gate read clean."""
    c = _client()
    h = _csrf(c)
    r = c.post("/api/global_config",
               data=json.dumps({"nonsense.not_a_real_key": 1}), headers=h)
    assert r.status_code == 400, (
        "unknown config key returned %s -- a discarded write MUST NOT report "
        "success" % r.status_code)


def test_known_keys_still_persist():
    """Guard against over-correction: the 400 must not break legitimate writes."""
    c = _client()
    h = _csrf(c)
    r = c.post("/api/global_config", data=json.dumps({"oidc_enabled": True}), headers=h)
    assert r.status_code == 200
    assert (c.get("/api/global_config").get_json() or {}).get("oidc_enabled") is True


def _referenced_data_keys(src):
    """Every global_config key an explicit branch READS from `data`.

    Covers the direct literal reads (`"k" in data`, `data.get("k"`, `data["k"]`)
    AND the loop-driven reads the direct scan is blind to:

        for k in ("watch_folder", "default_quick_add_site"):
            if k in data: ...

    Here the keys are string literals inside the `for <var> in ( ... ):` tuple and
    the read is through the loop variable, so a literal-only scan never sees them --
    the exact blindness that let six loop-read keys reach 780 uncaught. A loop tuple
    is credited only when its own loop variable is actually used against `data`
    (`<var> in data` / `data[<var>]` / `data.get(<var>`), and inclusion is the safe
    direction here: a bigger referenced-set can only surface an undeclared reader,
    never hide one.
    """
    import re

    referenced = {
        k
        for tup in re.findall(
            r'"([a-z_]+)" in data|data\.get\("([a-z_]+)"|data\["([a-z_]+)"\]', src)
        for k in tup
        if k
    }
    for var, tuple_src in re.findall(r'for\s+(\w+)\s+in\s+\(([^)]*)\)\s*:', src, re.DOTALL):
        reads_data = re.search(
            r'\b%s\b\s+in\s+data|data\[\s*%s\s*\]|data\.get\(\s*%s\b'
            % (re.escape(var), re.escape(var), re.escape(var)), src)
        if not reads_data:
            continue
        referenced.update(re.findall(r'"([a-z_]+)"', tuple_src))
    return referenced


def test_explicit_branch_keys_match_source():
    """_EXPLICIT_BRANCH_KEYS must stay in sync with the branches that actually read
    `data`. If a new explicit branch is added and this set is not updated, the
    unknown-key 400 would start REJECTING a legitimate write -- a hardcoded list is
    exactly the drift class this cut exists to kill, so it is pinned to the source."""
    from bulk_downloader import app_global_config as m

    src = open(m.__file__, encoding="utf-8").read()
    referenced = _referenced_data_keys(src)
    missing = sorted(referenced - set(m._EXPLICIT_BRANCH_KEYS))
    assert not missing, (
        "explicit branches read these keys but they are not in "
        "_EXPLICIT_BRANCH_KEYS -- a POST for them would now 400: %s" % missing)


def test_scanner_detects_loop_read_keys():
    """RED-first witness for the read-side widening: a key that appears ONLY inside a
    `for k in (...): if k in data` loop must be detected. A literal-only scan misses
    it -- that blindness is why six loop-read keys reached 780 uncaught."""
    snippet = (
        'def f(data):\n'
        '    for k in ("synthetic_loop_key", "another_loop_key"):\n'
        '        if k in data:\n'
        '            _cfg[k] = data[k]\n'
    )
    found = _referenced_data_keys(snippet)
    assert "synthetic_loop_key" in found and "another_loop_key" in found, (
        "loop-read keys not detected by the scanner: %s" % sorted(found))


def test_default_quick_add_site_persists():
    """Regression (v3.66.781): the tuple-sibling of watch_folder was read+written by
    the explicit loop branch but absent from the accepted set, so a POST for it 400'd
    -- an unwritable-but-handled key. It must now round-trip like watch_folder."""
    c = _client()
    h = _csrf(c)
    r = c.post("/api/global_config",
               data=json.dumps({"default_quick_add_site": "example.com"}), headers=h)
    assert r.status_code == 200, r.get_json()
    got = (c.get("/api/global_config").get_json() or {})
    assert got.get("default_quick_add_site") == "example.com", (
        "default_quick_add_site did not persist -- write was rejected/discarded")


@pytest.mark.parametrize("key", sorted(_read_automation_keys()))
def test_each_automation_toggle_round_trips(key):
    """Every automation toggle must survive a POST -> GET round trip."""
    spec = GLOBAL_CONFIG_SCHEMA.get(key)
    assert spec, "%s is not declared" % key
    val = True if spec.get("type") is bool else (1 if spec.get("type") is int else "x")
    c = _client()
    h = _csrf(c)
    r = c.post("/api/global_config", data=json.dumps({key: val}), headers=h)
    assert r.status_code == 200, r.get_json()
    got = (c.get("/api/global_config").get_json() or {})
    assert got.get(key) == val, "%s did not persist" % key
