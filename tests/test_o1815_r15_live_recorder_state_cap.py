"""O1815 R15 -- live_recorder restart re-arm and active-cap accounting.

P2-7: _load_state reset EVERY persisted entry to "pending", so a watch the
operator cancelled (or one that finished/failed) was re-armed and recorded
again after a restart. Only watches that were active (pending/recording)
when the server stopped may be re-armed; terminal entries keep their state.

P2-8: watch() compared len(_recordings) -- every entry ever created,
terminal ones included, persisted across restarts -- against the cap of
active recordings. After N lifetime watches every watch() returned
too_many_active. The cap must count active entries only, and still refuse
once N watches are actually active.
"""

# An ordinary module test: its subject is the module under test, not the
# tree, so it is not a repo-wide CI gate.
BD_GATE_SCOPE = "module"

import pytest

from bulk_downloader import live_recorder


@pytest.fixture
def recorder(tmp_path, monkeypatch):
    live_recorder._reset_for_tests()
    monkeypatch.setattr(live_recorder, "is_available", lambda: True)
    monkeypatch.setattr(live_recorder, "_max_active_recordings", lambda: 2)
    live_recorder.init(str(tmp_path / "state"))
    yield tmp_path
    live_recorder._reset_for_tests()


def _watch(tmp_path, room):
    return live_recorder.watch(f"https://chaturbate.com/{room}/",
                               str(tmp_path / "out"))


def _restart(tmp_path):
    live_recorder._reset_for_tests()
    live_recorder.init(str(tmp_path / "state"))


def _set_state(rid, state):
    # cancelled through the public path; finished/failed are written by the
    # scheduler, so set them directly and persist as the module does.
    if state == "cancelled":
        assert live_recorder.unwatch(rid) == {"ok": True}
        return
    with live_recorder._lock:
        live_recorder._recordings[rid].state = state
    live_recorder._save_state()


TERMINAL = ("finished", "failed", "cancelled")


@pytest.mark.parametrize("state", TERMINAL)
def test_restart_keeps_terminal_state_and_rearms_only_active(recorder, state):
    ended = _watch(recorder, "endedroom")
    active = _watch(recorder, "activeroom")
    assert ended["ok"] and active["ok"]
    _set_state(ended["recording_id"], state)

    _restart(recorder)

    got = live_recorder.get_recording(ended["recording_id"])
    assert got is not None, "P2-7: %s entry vanished on restart" % state
    assert got["state"] == state, (
        "P2-7: %s watch re-armed as %r after restart" % (state, got["state"]))
    assert live_recorder.get_recording(
        active["recording_id"])["state"] == "pending"


@pytest.mark.parametrize("state", TERMINAL)
def test_cap_counts_only_active_recordings(recorder, state):
    for room in ("roomone", "roomtwo"):
        r = _watch(recorder, room)
        assert r["ok"], r
        _set_state(r["recording_id"], state)

    _restart(recorder)

    third = _watch(recorder, "roomthree")
    assert third["ok"], (
        "P2-8: %s entries counted against the active cap: %r" % (state, third))
    _set_state(third["recording_id"], "recording")
    fourth = _watch(recorder, "roomfour")
    assert fourth["ok"], (
        "P2-8: %s entries counted against the active cap: %r" % (state, fourth))
    refused = _watch(recorder, "roomfive")
    assert refused.get("error") == "too_many_active", (
        "P2-8: a 'recording' entry was not counted against the cap: %r"
        % refused)
    assert refused["ok"] is False


ACTIVE = ("pending", "recording")


class _Egress:
    def close(self):
        pass


@pytest.mark.parametrize("state", ACTIVE + TERMINAL)
def test_restart_rearms_and_schedules_only_active(recorder, monkeypatch,
                                                   state):
    # A watch that was active when the server stopped comes back 'pending'
    # with no pid and is picked up by the next scheduler tick; a terminal
    # one keeps its state and is never scheduled.
    r = _watch(recorder, "someroom")
    assert r["ok"], r
    rid = r["recording_id"]
    if state != "pending":
        _set_state(rid, state)
    with live_recorder._lock:
        live_recorder._recordings[rid].pid = 4242
    live_recorder._save_state()

    _restart(recorder)

    got = live_recorder.get_recording(rid)
    assert got is not None, "P2-7: %s entry vanished on restart" % state
    want = "pending" if state in ACTIVE else state
    assert got["state"] == want, (
        "P2-7: %s watch came back %r after restart, want %r"
        % (state, got["state"], want))
    assert got["pid"] is None, (
        "P2-7: %s watch kept pid %r after restart" % (state, got["pid"]))

    probed = []
    monkeypatch.setattr(live_recorder, "_egress_prepare",
                        lambda site: _Egress())
    monkeypatch.setattr(live_recorder, "_is_room_live",
                        lambda site, room, url, prepared_egress=None:
                        probed.append(room) or False)
    live_recorder._scheduler_tick()

    if state in ACTIVE:
        assert probed == ["someroom"], (
            "P2-7: re-armed %s watch was not scheduled after restart" % state)
    else:
        assert probed == [], (
            "P2-7: %s watch was scheduled after restart" % state)
    assert live_recorder.get_recording(rid)["state"] == want, (
        "P2-7: %s watch left %r after one tick"
        % (state, live_recorder.get_recording(rid)["state"]))
