"""v3.66.780 -- CFG-PARITY-WRITE: the Settings-save 400 class.

LIVE BUG (operator-reported @778, Settings page): every save returns
``400 unknown config key(s): ai_model_text, ai_model_vision, watch_folder``.
The write is atomic and the FE submits the full config draft, so three (in fact
SIX) unaccepted keys 400 every save -- the whole Settings page is unsaveable.

Root cause: the write denominator is ``set(GLOBAL_CONFIG_SCHEMA) |
_EXPLICIT_BRANCH_KEYS`` (app_global_config.py:385; 400 at :389). Six keys the
same file READS and WRITES via explicit ``for k in (...): if k in data`` loops
are in NEITHER set:

  ai_model_text, ai_model_vision                (read+write: L251-252, L268-269)
  watch_folder                                  (read+write: L163-164, app.py:1958)
  session_keep_alive_lead_time_min,
  session_keep_alive_fetch_interval_min,
  session_keep_alive_navigate_interval_min      (read+write: L176-186)

All six already have write branches -- the fix only widens the ACCEPTED set. This
is the project's canonical shape one layer up: the WRITE denominator structurally
excludes keys the READ side of the same file uses. The @709 contract ("unknown
key = 400") fires correctly against a denominator missing legitimate keys.

Why the existing gates missed it: test_gui_parity checks route/control WIRING, not
config-key acceptance. The @709 read-side scan (test_explicit_branch_keys_match_source)
uses a regex that matches ``"key" in data`` / ``data.get("key")`` literals but NOT
the ``for k in (...): if k in data`` loop pattern -- so it never saw these six. The
class-closer here derives the FE Settings-field set and asserts it is a subset of
the backend write denominator: FE-config-key set == unwritable-if-omitted set.

RED-first: both anchors fail on pristine 779; green after the six keys join
_EXPLICIT_BRANCH_KEYS.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from bulk_downloader.app import app
from bulk_downloader.global_config import GLOBAL_CONFIG_SCHEMA
from bulk_downloader import app_global_config as agc

_REPO = Path(__file__).resolve().parents[1]
_SETTINGS_SCHEMA_TS = _REPO / "frontend" / "src" / "lib" / "settingsSchema.ts"

# The six keys this cut widens the accepted set by. Named explicitly so the test
# documents the exact class even if the FE parse changes.
_SIX = [
    "ai_model_text",
    "ai_model_vision",
    "watch_folder",
    "session_keep_alive_lead_time_min",
    "session_keep_alive_fetch_interval_min",
    "session_keep_alive_navigate_interval_min",
]


def _client():
    return app.test_client()


def _csrf(c):
    j = c.get("/api/csrf").get_json() or {}
    t = j.get("csrf_token") or j.get("token")
    return {"X-CSRFToken": t, "X-CSRF-Token": t, "Content-Type": "application/json"}


def _accepted_write_keys():
    """The backend WRITE denominator: exactly the set app_global_config checks a
    POSTed key against before the 400."""
    return set(GLOBAL_CONFIG_SCHEMA) | set(agc._EXPLICIT_BRANCH_KEYS)


def _fe_settings_keys():
    """Every editable field the Settings page knows about (SETTINGS_SCHEMA keys).
    The Settings form POSTs the full config draft to /api/global_config, so each
    of these must be in the backend write denominator or the save 400s."""
    body = _SETTINGS_SCHEMA_TS.read_text(encoding="utf-8")
    m = re.search(r"SETTINGS_SCHEMA[^{]*\{(.*)\n\};", body, re.S)
    obj = m.group(1) if m else body
    return set(re.findall(r"^\s{2}([a-z][a-zA-Z0-9_]*):\s*\{", obj, re.M))


# -- Anchor A: the round trip (the operator symptom) -------------------------

@pytest.mark.parametrize("key,val", [
    ("ai_model_text", "gpt-4o-mini"),
    ("ai_model_vision", "gpt-4o"),
    ("watch_folder", "/tmp/wf_780"),
    ("session_keep_alive_lead_time_min", 12),
    ("session_keep_alive_fetch_interval_min", 15),
    ("session_keep_alive_navigate_interval_min", 20),
])
def test_each_settings_key_round_trips(key, val):
    """Each of the six must POST 200 and survive a GET round trip. Today every
    one 400s -> the Settings page cannot save."""
    c = _client()
    h = _csrf(c)
    r = c.post("/api/global_config", data=json.dumps({key: val}), headers=h)
    assert r.status_code == 200, (
        "POST %r returned %s (not 200) -- the Settings save is broken for this "
        "key: %s" % (key, r.status_code, r.get_json()))
    got = c.get("/api/global_config").get_json() or {}
    assert key in got, "%s returned 200 but is absent on read-back" % key


def test_full_draft_save_does_not_400():
    """The real symptom: a save carrying all six at once (as the form does) must
    not 400."""
    c = _client()
    h = _csrf(c)
    draft = {
        "ai_model_text": "gpt-4o-mini",
        "ai_model_vision": "gpt-4o",
        "watch_folder": "/tmp/wf_780",
        "session_keep_alive_lead_time_min": 12,
        "session_keep_alive_fetch_interval_min": 15,
        "session_keep_alive_navigate_interval_min": 20,
    }
    r = c.post("/api/global_config", data=json.dumps(draft), headers=h)
    assert r.status_code == 200, (
        "full-draft save 400'd: %s" % r.get_json())


# -- Anchor B: the class-closer (permanent parity gate) ----------------------

def test_every_fe_settings_key_is_accepted_by_backend():
    """FE-config-key set MUST be a subset of the backend write denominator.
    This is the symmetric contract the @709 comment asked for and never got: a
    Settings field the backend would 400 on is unsaveable. Closing it prevents
    the whole class, not just today's six."""
    fe = _fe_settings_keys()
    accepted = _accepted_write_keys()
    assert fe, "parsed zero SETTINGS_SCHEMA keys -- the FE parse is broken"
    unsaveable = sorted(k for k in fe if k not in accepted)
    assert not unsaveable, (
        "FE Settings fields the backend write denominator does NOT accept -- "
        "every save carrying one of these 400s: %s" % unsaveable)


def test_the_six_are_in_the_explicit_branch_set():
    """Pin the fix location: the six belong in _EXPLICIT_BRANCH_KEYS (matching
    their sibling ai_*/watch_* keys, which are handled by explicit branches, not
    the schema-typed path), NOT GLOBAL_CONFIG_SCHEMA (which would perturb the
    config-parity manifest whose denominator IS the schema)."""
    ebk = set(agc._EXPLICIT_BRANCH_KEYS)
    missing = [k for k in _SIX if k not in ebk]
    assert not missing, (
        "these belong in _EXPLICIT_BRANCH_KEYS: %s" % missing)
    # and must NOT have leaked into the schema (keeps the parity ratchet stable)
    leaked = [k for k in _SIX if k in GLOBAL_CONFIG_SCHEMA]
    assert not leaked, (
        "these were added to GLOBAL_CONFIG_SCHEMA -- wrong home, perturbs the "
        "config-parity manifest: %s" % leaked)
