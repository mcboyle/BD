"""v3.66.290 — surface the live HUD analysis to the SPA (C3).

The live action timeline + verify readout (inspect_pick.correlate_timeline /
verify_summary) were rendered ONLY into the noVNC HUD via _inject_hud, never to
the SPA. They are structure-only (selectors / roles / request kinds+counts /
redacted excerpts — "no values cross this boundary"), so they can be mirrored
out safely.

This adds a LIVE-MIRROR sidecar in element_pick: the capture's per-tick pump
WRITES INSPECT_STATE.json (overwrite); the SPA POLLS it via a new inspect_poll
action on the existing /cockpit/api/captures/pick endpoint. Unlike PICK_RESULT
/ DOM_RESULT (read-and-delete, one-shot), the inspect state is read WITHOUT
deleting — it is a continuously-refreshed mirror.

Pure-ish filesystem tests (mkdtemp); no browser, no Flask.
"""

import json
import tempfile
from pathlib import Path

from bulk_downloader import element_pick as ep


def _tmp():
    return tempfile.mkdtemp(prefix="bd_inspect_")


def test_inspect_state_path_under_out_dir():
    d = _tmp()
    p = ep.inspect_state_path(d)
    assert Path(p).parent == Path(d)
    assert str(p).endswith("INSPECT_STATE.json")


def test_write_then_read_roundtrip():
    d = _tmp()
    state = {"actions": [{"selector": "a.x", "role": "download link"}],
             "verify": {"verdict": "ready"}, "rec": True}
    assert ep.write_inspect_state(d, state) is True
    got = ep.read_inspect_state(d)
    assert got == state


def test_read_does_not_delete_mirror():
    """The mirror is polled repeatedly — a read must NOT consume it."""
    d = _tmp()
    ep.write_inspect_state(d, {"actions": [], "verify": None})
    first = ep.read_inspect_state(d)
    second = ep.read_inspect_state(d)
    assert first == second
    assert ep.inspect_state_path(d).exists()


def test_write_overwrites_previous_tick():
    d = _tmp()
    ep.write_inspect_state(d, {"actions": [1]})
    ep.write_inspect_state(d, {"actions": [1, 2]})
    assert ep.read_inspect_state(d) == {"actions": [1, 2]}


def test_read_absent_returns_none():
    d = _tmp()
    assert ep.read_inspect_state(d) is None


def test_read_bad_json_returns_none():
    d = _tmp()
    ep.inspect_state_path(d).write_text("{not json", encoding="utf-8")
    assert ep.read_inspect_state(d) is None


def test_write_tolerates_bad_out_dir():
    # A non-existent parent must not raise — best-effort, returns False.
    assert ep.write_inspect_state("/no/such/dir/here", {"a": 1}) is False
