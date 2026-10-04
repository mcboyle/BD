"""AUDIT-16 (row o1671-a16): a capture artifact rotated or deleted between the
glob that listed it and the os.path.getsize that stats it aborted the whole
report with FileNotFoundError (tools/capture_analytics.py, both artifact
loops). The scan must instead skip the ghost and count it, so a lossy pass
says so rather than crashing or claiming a completeness it does not have.

Positive control: every vanishing fixture deletes the file inside the patched
getsize and afterwards asserts the file is really gone, so a green run cannot
come from a race probe that never fired.
"""
from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

# Its subject is tools/capture_analytics.py, not the tree.
BD_GATE_SCOPE = "module"


def _ca():
    import capture_analytics as CA
    return CA


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "captures"
    d.mkdir()
    (d / "capture_host1.session.wacz").write_bytes(b"wacz-bytes")
    (d / "capture_host2.json").write_text(json.dumps({
        "host": "host2.example", "capture_kind": "network",
        "network_log": [{"url": "https://x.invalid/1"}],
    }), encoding="utf-8")
    return tmp_path


def _vanish_at_getsize(monkeypatch, victim):
    """Delete victim the first time getsize stats it: the listing->stat race,
    deterministically, at the exact seam the finding names."""
    real = os.path.getsize
    state = {"fired": False}

    def flaky(path):
        if not state["fired"] and os.path.normpath(str(path)) == os.path.normpath(str(victim)):
            state["fired"] = True
            os.unlink(path)
        return real(path)

    monkeypatch.setattr(os.path, "getsize", flaky)
    return state


def _names(out):
    return [a["path"].split("/")[-1] for a in out["artifacts"]["items"]]


def test_a_wacz_that_vanishes_between_listing_and_stat_is_skipped_and_counted(store, monkeypatch):
    CA = _ca()
    victim = store / "captures" / "capture_host1.session.wacz"
    assert victim.exists(), "precondition: the glob must list the victim"
    state = _vanish_at_getsize(monkeypatch, victim)

    out = CA.analyze(str(store))

    assert state["fired"] and not victim.exists(), "the race probe never fired"
    assert _names(out) == ["capture_host2.json"], (
        f"the ghost wacz was claimed as an artifact: {_names(out)!r}")
    assert out["skipped_artifacts"] == 1, out["skipped_artifacts"]
    assert out["unparsed_artifacts"] == 0


def test_a_capture_json_that_vanishes_between_listing_and_stat_is_skipped_and_counted(store, monkeypatch):
    CA = _ca()
    victim = store / "captures" / "capture_host2.json"
    assert victim.exists(), "precondition: the glob must list the victim"
    state = _vanish_at_getsize(monkeypatch, victim)

    out = CA.analyze(str(store))

    assert state["fired"] and not victim.exists(), "the race probe never fired"
    assert _names(out) == ["capture_host1.session.wacz"], (
        f"the ghost capture was claimed as an artifact: {_names(out)!r}")
    assert out["skipped_artifacts"] == 1, out["skipped_artifacts"]
    assert out["unparsed_artifacts"] == 0


def test_a_clean_scan_skips_nothing_negative_control(store):
    """The over-sensitive direction: a counter that fires without a race would
    make every pass claim loss. Unbounded is the CLI's contract and must stay
    loss-free when nothing vanishes."""
    out = _ca().analyze(str(store))
    assert out["artifacts"]["count"] == 2
    assert sorted(_names(out)) == ["capture_host1.session.wacz", "capture_host2.json"]
    assert out["skipped_artifacts"] == 0
    assert out["unparsed_artifacts"] == 0


# ── bounded path (lens D16 F1) ─────────────────────────────────────
# bulk_downloader/app_data_layer.py collect_capture_analytics always passes a
# limit, so GET /api/data/capture_analytics takes the newest-first sort, which
# stats every listed path with getmtime BEFORE the guarded getsize loops.
BOUNDED = {"limit": 5, "budget_s": 8, "max_bytes": 10 ** 6}  # API collector shape


def _vanish_at_getmtime(monkeypatch, victim):
    """Same race as _vanish_at_getsize, at the bounded sort's mtime key."""
    real = os.path.getmtime
    state = {"fired": False}

    def flaky(path):
        if not state["fired"] and os.path.normpath(str(path)) == os.path.normpath(str(victim)):
            state["fired"] = True
            os.unlink(path)
        return real(path)

    monkeypatch.setattr(os.path, "getmtime", flaky)
    return state


@pytest.mark.parametrize("victim_name, survivor", [
    ("capture_host1.session.wacz", "capture_host2.json"),
    ("capture_host2.json", "capture_host1.session.wacz"),
])
def test_bounded_an_artifact_that_vanishes_at_the_mtime_sort_is_skipped_and_counted(
        store, monkeypatch, victim_name, survivor):
    CA = _ca()
    victim = store / "captures" / victim_name
    assert victim.exists(), "precondition: the glob must list the victim"
    state = _vanish_at_getmtime(monkeypatch, victim)

    out = CA.analyze(str(store), **BOUNDED)

    assert state["fired"] and not victim.exists(), "the race probe never fired"
    assert out["bounded"] is True
    assert _names(out) == [survivor], (
        f"the ghost was claimed as an artifact: {_names(out)!r}")
    assert out["skipped_artifacts"] == 1, out["skipped_artifacts"]
    assert out["unparsed_artifacts"] == 0


def test_bounded_a_vanished_artifact_does_not_take_a_limit_slot(store, monkeypatch):
    """limit=1 with one ghost: the survivor must still be processed, and the
    single skip is the ghost, not the survivor."""
    CA = _ca()
    victim = store / "captures" / "capture_host1.session.wacz"
    state = _vanish_at_getmtime(monkeypatch, victim)

    out = CA.analyze(str(store), limit=1)

    assert state["fired"] and not victim.exists(), "the race probe never fired"
    assert _names(out) == ["capture_host2.json"], _names(out)
    assert out["skipped_artifacts"] == 1, out["skipped_artifacts"]


def test_bounded_getsize_race_is_also_counted(store, monkeypatch):
    """The getsize guards hold on the bounded path too (lens positive control)."""
    CA = _ca()
    victim = store / "captures" / "capture_host1.session.wacz"
    state = _vanish_at_getsize(monkeypatch, victim)

    out = CA.analyze(str(store), **BOUNDED)

    assert state["fired"] and not victim.exists(), "the race probe never fired"
    assert _names(out) == ["capture_host2.json"], _names(out)
    assert out["skipped_artifacts"] == 1, out["skipped_artifacts"]


def test_bounded_clean_scan_skips_nothing_negative_control(store):
    out = _ca().analyze(str(store), **BOUNDED)
    assert out["bounded"] is True
    assert sorted(_names(out)) == ["capture_host1.session.wacz", "capture_host2.json"]
    assert out["skipped_artifacts"] == 0
    assert out["unparsed_artifacts"] == 0


@pytest.mark.parametrize("newest", ["capture_host1.session.wacz", "capture_host2.json"])
def test_bounded_keeps_the_newest_by_mtime(store, newest):
    """The guarded mtime key must still sort newest-first: limit=1 keeps the
    newer artifact whichever kind it is, and counts the older as skipped."""
    d = store / "captures"
    older = ({"capture_host1.session.wacz", "capture_host2.json"} - {newest}).pop()
    os.utime(d / older, (1_000_000, 1_000_000))
    os.utime(d / newest, (2_000_000, 2_000_000))

    out = _ca().analyze(str(store), limit=1)

    assert _names(out) == [newest], _names(out)
    assert out["skipped_artifacts"] == 1, out["skipped_artifacts"]
