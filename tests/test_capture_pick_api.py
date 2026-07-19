"""Phase 3 of the live element-pick bridge: the capture-pick API.

The SPA workflow arms a one-shot active pick on the *running* capture and reads
the picked selector. A capture is a cockpit task (``cockpit_core._REGISTRY``,
keyed by ``task_id``, carrying ``out_dir``), so the pick API mirrors
``finish_capture`` exactly: look the task up, resolve its ``out_dir``, and drive
the filesystem sentinel bridge (``bulk_downloader.element_pick``).

  * ``cc.pick_capture(task_id, action)`` -- arm | poll | clear, validated like
    finish_capture (task must exist + be a capture + have an out_dir);
  * route ``POST /cockpit/api/captures/pick`` -- thin wrapper, next to
    ``/cockpit/api/captures/finish`` (auth + CSRF inherited from the host app's
    before_request, same as finish).

No Flask client / CSRF dance needed for the core: cc.pick_capture is tested
directly with a registered fake task; route registration is asserted against
``app.url_map``.
"""

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import cockpit_core as cc


def _register(tid, category="capture", out_dir=None):
    rec = {"task_id": tid, "category": category, "status": "running",
           "out_dir": str(out_dir) if out_dir else None}
    with cc._REG_LOCK:
        cc._REGISTRY[tid] = rec


def _unregister(tid):
    with cc._REG_LOCK:
        cc._REGISTRY.pop(tid, None)


# ── cc.pick_capture core ─────────────────────────────────────────────────────

def test_pick_capture_arm_poll_consume_clear(tmp_path):
    from bulk_downloader import element_pick as ep
    out = tmp_path / "out"
    out.mkdir()
    tid = "pick-roundtrip"
    _register(tid, out_dir=out)
    try:
        # arm
        r = cc.pick_capture(tid, action="arm")
        assert r["armed"] is True
        assert ep.arm_path(out).exists()

        # poll before any pick -> still armed, no result
        r = cc.pick_capture(tid, action="poll")
        assert r["armed"] is True and r["result"] is None

        # capture process wrote a result (PICK_RESULT.json)
        ep.result_path(out).write_text(json.dumps(
            {"selector": 'a.ct_dl_button[data-framerate="60"]',
             "unique": True, "visible": True}), encoding="utf-8")

        r = cc.pick_capture(tid, action="poll")
        assert r["result"]["selector"] == 'a.ct_dl_button[data-framerate="60"]'

        # poll is read-and-clear: a second poll sees nothing
        assert cc.pick_capture(tid, action="poll")["result"] is None

        # clear removes the arm sentinel
        r = cc.pick_capture(tid, action="clear")
        assert r["armed"] is False
        assert not ep.arm_path(out).exists()
    finally:
        _unregister(tid)


def test_pick_capture_unknown_task_raises():
    with pytest.raises(cc.ValidationError):
        cc.pick_capture("does-not-exist", action="arm")


def test_pick_capture_non_capture_task_raises(tmp_path):
    tid = "a-report-task"
    _register(tid, category="report", out_dir=tmp_path)
    try:
        with pytest.raises(cc.ValidationError):
            cc.pick_capture(tid, action="arm")
    finally:
        _unregister(tid)


def test_pick_capture_unknown_action_raises(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    tid = "bad-action"
    _register(tid, out_dir=out)
    try:
        with pytest.raises(cc.ValidationError):
            cc.pick_capture(tid, action="frobnicate")
    finally:
        _unregister(tid)


# ── route registration ───────────────────────────────────────────────────────

def test_pick_route_is_registered_and_post_only():
    from bulk_downloader import app as _app
    rules = [r for r in _app.app.url_map.iter_rules()
             if str(r.rule) == "/cockpit/api/captures/pick"]
    assert rules, "POST /cockpit/api/captures/pick not registered"
    methods = set().union(*(r.methods for r in rules))
    assert "POST" in methods, methods
    assert "GET" not in methods, "pick route must be POST-only (mutating)"


if __name__ == "__main__":
    import tempfile
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f(Path(tempfile.mkdtemp())) if "tmp_path" in _f.__code__.co_varnames else _f()
            except TypeError:
                _f()
            print("PASS", _n)
    print("ALL PASS")
