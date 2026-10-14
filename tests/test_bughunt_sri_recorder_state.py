"""staging-integrity recorder-state-fix r1 -- HIGH-F2 (live_recorder recordings.json).

F2  _save_state built its snapshot under _lock, dropped the lock, then every caller wrote the
    same fixed "<state>.json.tmp": overlapping saves tore the file (or hit ENOENT on the second
    os.replace), and _load_state treated an unparseable file as "starting fresh", so the next
    save wiped every watch for good.

Probe shapes from S56-C ev/s27_recorder_state.py; spec from pm-B ANSWER-pm-B-HIGH-F2-0836Z.
No subprocesses, no network: tmp state dirs only.
"""
import json
import threading
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"


@pytest.fixture
def lr(tmp_path, monkeypatch):
    from bulk_downloader import live_recorder as mod

    pushed = []
    mod._reset_for_tests()
    monkeypatch.setattr(mod, "_state_dir", None)
    monkeypatch.setattr(mod, "_push_notifier", lambda t, b: pushed.append((t, b)))
    monkeypatch.setattr(mod, "_test_pushed", pushed, raising=False)
    yield mod
    mod._reset_for_tests()


def _recs(mod, tmp_path, n):
    return [mod.Recording(recording_id=f"r{i}", site="chaturbate", room=f"room{i}",
                          url=f"https://chaturbate.com/room{i}/",
                          output_path=str(tmp_path / f"room{i}.mp4"),
                          started_at=1.0).to_dict() for i in range(n)]


def _fresh(mod, d, items):
    mod._reset_for_tests()
    f = Path(d) / mod.STATE_FILE_NAME
    f.write_text(json.dumps({"recordings": items, "saved_at": 1.0}, indent=2))
    return f


def _states(f):
    return {r["recording_id"]: r["state"] for r in json.loads(f.read_text())["recordings"]}


def _corrupt(d, mod):
    return sorted(p.name for p in Path(d).iterdir()
                  if p.name.startswith(mod.STATE_FILE_NAME + ".corrupt-"))


def test_f2_overlapping_saves_never_tear_the_state_file(lr, tmp_path, capsys):
    trials, unreadable, lost = 2000, [], []
    for i in range(trials):
        d = tmp_path / str(i)
        d.mkdir()
        f = _fresh(lr, d, _recs(lr, tmp_path, 2))
        lr.init(str(d))
        bar = threading.Barrier(2)
        t1 = threading.Thread(target=lambda: (bar.wait(), lr._save_state()))
        t2 = threading.Thread(target=lambda: (bar.wait(), lr.unwatch("r0")))
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        try:
            s = _states(f)
        except ValueError as e:
            unreadable.append((i, repr(e), f.read_bytes()[-40:]))
            continue
        if s.get("r0") != "cancelled":
            lost.append((i, s))
    err = capsys.readouterr().err
    assert unreadable == [], f"{len(unreadable)}/{trials} torn, first {unreadable[:3]}"
    assert lost == [], f"{len(lost)}/{trials} cancel lost, first {lost[:3]}"
    assert "state save failed" not in err, err[-400:]
    left = sorted(p.name for p in tmp_path.rglob("*.tmp"))
    assert left == [], left[:5]


def test_f2_torn_state_file_restores_from_bak_and_is_kept(lr, tmp_path, capsys):
    d = tmp_path / "state"
    d.mkdir()
    f = _fresh(lr, d, _recs(lr, tmp_path, 3))
    raw = f.read_bytes()
    torn = raw[: len(raw) // 2]
    bak = d / (lr.STATE_FILE_NAME + ".bak")
    bak.write_bytes(raw)                   # the last good file
    f.write_bytes(torn)                    # main torn by an overlapping save
    lr.init(str(d))
    assert sorted(r["recording_id"] for r in lr.list_recordings()) == ["r0", "r1", "r2"]
    kept = _corrupt(d, lr)
    assert len(kept) == 1 and (d / kept[0]).read_bytes() == torn, kept
    assert sorted(_states(f)) == ["r0", "r1", "r2"], "main file not written back"
    assert "ERROR" in capsys.readouterr().err
    # each later save keeps the previous good main file as the .bak
    lr.unwatch("r1")
    assert _states(bak)["r1"] == "pending" and _states(f)["r1"] == "cancelled"
    lr._save_state()
    assert _states(bak)["r1"] == "cancelled"


def test_f2_both_unreadable_starts_empty_loudly_and_keeps_both(lr, tmp_path, capsys):
    d = tmp_path / "state"
    d.mkdir()
    f = _fresh(lr, d, _recs(lr, tmp_path, 3))
    raw = f.read_bytes()
    torn = raw[: len(raw) // 2]
    f.write_bytes(torn)
    bak = d / (lr.STATE_FILE_NAME + ".bak")
    bak.write_bytes(raw[: len(raw) // 3])
    lr.init(str(d))
    assert lr.list_recordings() == []
    err = capsys.readouterr().err
    assert "ERROR: no readable state" in err, err
    assert len(lr._test_pushed) == 1 and "unreadable" in lr._test_pushed[0][0], lr._test_pushed
    kept = _corrupt(d, lr)
    assert len(kept) == 1 and (d / kept[0]).read_bytes() == torn, kept
    lr._save_state()                       # saves after the failed load: the 2nd one
    lr._save_state()                       # would rotate the empty main into the .bak
    assert bak.read_bytes() == raw[: len(raw) // 3], ".bak overwritten after a failed load"
    assert (d / kept[0]).read_bytes() == torn
    assert json.loads(f.read_text())["recordings"] == []


def test_f2_control_valid_state_loads_without_alarm(lr, tmp_path, capsys):
    d = tmp_path / "state"
    d.mkdir()
    f = _fresh(lr, d, _recs(lr, tmp_path, 3))
    lr.init(str(d))
    assert len(lr.list_recordings()) == 3
    lr._save_state()
    assert sorted(_states(f)) == ["r0", "r1", "r2"]
    assert _corrupt(d, lr) == [] and lr._test_pushed == []
    assert "ERROR" not in capsys.readouterr().err
    # a fresh install (no file, no .bak) is not a failed load either
    empty = tmp_path / "empty"
    lr._reset_for_tests()
    lr.init(str(empty))
    assert lr.list_recordings() == [] and lr._test_pushed == []
    assert not getattr(lr, "_load_failed", False)
