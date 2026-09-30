"""fx-challenge-autoreg: a parked takeover/captcha lists itself on the challenge board.

bd4 (2026-09-30 01:19Z): the app parked a blacked manual-login takeover, but the board
(harness/bd-challenge-board.py) only lists rows seats append to NEEDS-HUMAN.tsv by hand, so
the operator never saw it. The app already publishes every park (``/api/status``
``awaiting_manual_login`` / ``awaiting_manual_download``, ``/api/captcha/pending``); the board,
the one host-side reader of NEEDS-HUMAN.tsv, now watches those and appends one row per park,
and one NEEDS-HUMAN-DONE.tsv row when the park clears. The VMs here are fakes; nothing
touches a site.
"""
from __future__ import annotations

import importlib.util
import os
import threading
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"

CANDIDATE = os.environ.get("BD_FX_CHALLENGE_AUTOREG_CANDIDATE", "")
pytestmark = pytest.mark.skipif(not CANDIDATE, reason="candidate opt-in required")

BD4, BD1 = "10.0.70.54", "10.0.70.51"


@pytest.fixture
def board(tmp_path, monkeypatch):
    src = Path(CANDIDATE) / "bd-challenge-board.py"
    assert src.is_file(), f"candidate board missing: {src}"
    spec = importlib.util.spec_from_file_location("bd_challenge_board_candidate", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    (tmp_path / "SITE-ASSIGNMENT.tsv").write_text(
        f"ISP-1\t{BD4}\tbd4\tblacked vixen\nISP-1\t{BD1}\tbd1\tdorcelclub\n", encoding="utf-8")
    monkeypatch.setattr(mod, "D", tmp_path)
    monkeypatch.setattr(mod, "NEED", tmp_path / "NEEDS-HUMAN.tsv")
    monkeypatch.setattr(mod, "DONE", tmp_path / "NEEDS-HUMAN-DONE.tsv")
    monkeypatch.setattr(mod, "ASSIGN", tmp_path / "SITE-ASSIGNMENT.tsv", raising=False)
    return mod


class FakeFleet:
    """GET-only view of each VM's app: /api/status and /api/captcha/pending."""

    def __init__(self):
        self.status = {BD4: {}, BD1: {}}
        self.captcha = {BD4: [], BD1: []}
        self.down = set()

    def site(self, ip, sid, name, login=False, download=False):
        self.status[ip][sid] = {"name": name, "state": "stopped",
                                "awaiting_manual_login": login,
                                "awaiting_manual_download": download}

    def __call__(self, url):
        ip = url.split("//", 1)[1].split(":", 1)[0]
        if ip in self.down:
            raise OSError(f"connect {ip}: refused")
        if url.endswith("/api/status"):
            return self.status[ip]
        if url.endswith("/api/captcha/pending"):
            return {"ok": True, "pending": self.captcha[ip]}
        raise AssertionError(f"autoreg asked for {url}: GET /api/status and /api/captcha/pending only")


def need(board):
    return board.rows(board.NEED)


def done(board):
    return board.rows(board.DONE)


def test_a_parked_manual_login_is_listed_once(board):
    fleet = FakeFleet()
    fleet.site(BD4, "3c8aba9b", "blacked", login=True)
    fleet.site(BD4, "11111111", "vixen")
    reg = board.AutoReg()
    reg.sync(fleet)
    reg.sync(fleet)
    rows = need(board)
    assert [r[1:4] for r in rows] == [["bd4", BD4, "blacked"]], (
        f"FX-AUTOREG: NEEDS-HUMAN.tsv after 2 polls of a parked blacked login = {rows}")
    assert "manual login" in rows[0][4].lower()
    assert [r[3] for r in board.open_rows()] == ["blacked"]


def test_clearing_the_park_appends_one_done_row(board):
    fleet = FakeFleet()
    fleet.site(BD4, "3c8aba9b", "blacked", login=True)
    reg = board.AutoReg()
    reg.sync(fleet)
    fleet.site(BD4, "3c8aba9b", "blacked", login=False)   # I'm Done / cancel
    reg.sync(fleet)
    reg.sync(fleet)
    assert [r[1:3] for r in done(board)] == [["bd4", "blacked"]], (
        f"FX-AUTOREG: NEEDS-HUMAN-DONE.tsv after the park cleared = {done(board)}")
    assert board.open_rows() == []
    assert len(need(board)) == 1


def test_a_parked_captcha_is_listed_under_its_site_name_without_the_query(board):
    fleet = FakeFleet()
    fleet.site(BD1, "8cab7bee", "dorcelclub")
    fleet.captcha[BD1] = [{"url": "https://www.dorcelclub.com/blocked?r=/dl/scene/305502&token=zero-entropy",
                           "site_id": "8cab7bee", "captcha_type": "hcaptcha", "status": "pending"}]
    reg = board.AutoReg()
    reg.sync(fleet)
    rows = need(board)
    assert [r[1:4] for r in rows] == [["bd1", BD1, "dorcelclub"]], rows
    assert "hcaptcha" in rows[0][4] and "www.dorcelclub.com/blocked" in rows[0][4]
    assert "token" not in rows[0][4] and "?" not in rows[0][4]
    fleet.captcha[BD1] = []
    reg.sync(fleet)
    assert board.open_rows() == []


def test_a_manual_download_takeover_is_listed(board):
    fleet = FakeFleet()
    fleet.site(BD4, "11111111", "vixen", download=True)
    board.AutoReg().sync(fleet)
    assert [r[3] for r in board.open_rows()] == ["vixen"]


def test_a_hand_filed_row_is_not_duplicated_and_closes_when_the_park_clears(board):
    board.NEED.write_text(f"2026-09-30T01:22:33Z\tbd4\t{BD4}\tblacked\tAdded by bd-pm-A.\n", encoding="utf-8")
    fleet = FakeFleet()
    fleet.site(BD4, "3c8aba9b", "blacked", login=True)
    reg = board.AutoReg()
    reg.sync(fleet)
    assert len(need(board)) == 1, need(board)
    fleet.site(BD4, "3c8aba9b", "blacked", login=False)
    reg.sync(fleet)
    assert board.open_rows() == []


def test_a_hand_filed_row_the_watcher_never_saw_parked_stays_open(board):
    """A row for a park the app does not publish (e.g. a harness plain browser) is not closed."""
    board.NEED.write_text(f"2026-09-30T01:00:00Z\tbd4\t{BD4}\tvixen\tplain browser on :99\n", encoding="utf-8")
    fleet = FakeFleet()
    fleet.site(BD4, "11111111", "vixen")
    board.AutoReg().sync(fleet)
    assert [r[3] for r in board.open_rows()] == ["vixen"] and done(board) == []


def test_an_unreachable_vm_closes_nothing(board):
    fleet = FakeFleet()
    fleet.site(BD4, "3c8aba9b", "blacked", login=True)
    reg = board.AutoReg()
    reg.sync(fleet)
    fleet.down.add(BD4)
    reg.sync(fleet)
    assert done(board) == [] and [r[3] for r in board.open_rows()] == ["blacked"]


def test_mark_done_during_a_park_does_not_relist_it_until_it_parks_again(board):
    fleet = FakeFleet()
    fleet.site(BD4, "3c8aba9b", "blacked", login=True)
    reg = board.AutoReg()
    reg.sync(fleet)
    with board.DONE.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\tbd4\tblacked\toperator-board\n")
    time.sleep(1.1)  # the board hides rows at or before a DONE second
    reg.sync(fleet)
    assert len(need(board)) == 1, "the same park was listed twice"
    fleet.site(BD4, "3c8aba9b", "blacked", login=False)
    reg.sync(fleet)
    fleet.site(BD4, "3c8aba9b", "blacked", login=True)
    reg.sync(fleet)
    assert len(need(board)) == 2, f"a new park after the clear was not listed: {need(board)}"


def test_the_board_process_runs_the_watcher(board, monkeypatch):
    seen = {}

    class Server:
        def __init__(self, addr, handler):
            pass

        def serve_forever(self):
            seen["threads"] = [t.name for t in threading.enumerate() if t.is_alive()]

    monkeypatch.setattr(board, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(board, "http_json", FakeFleet())
    monkeypatch.setattr(board, "POLL_S", 3600)
    board.main(["8098"])
    assert "challenge-autoreg" in seen["threads"], (
        f"FX-AUTOREG: board serves without the watcher thread; threads={seen['threads']}")
