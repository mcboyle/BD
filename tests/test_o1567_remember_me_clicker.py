"""fx-remember-me-clicker: scripts/vnc_display_sync.py ticks the "Remember me" box.

The script is the operator's ~/.local/bin copy taken byte-for-byte: it imports
pyautogui at module level and calls pyautogui.locateCenterOnScreen(REFERENCE_FRAME,
confidence=0.8) each poll. These tests load it with a stand-in pyautogui whose
locateCenterOnScreen runs the real pyscreeze matcher against a synthetic screen,
so no display is needed. REFERENCE_FRAME still names ~/.local/bin, so the tests
point it at the repo PNG beside the script. Loading the script sets DISPLAY and
XDG_SESSION_TYPE defaults in os.environ; every load goes through monkeypatch so
those are undone for the rest of the session.
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

import cv2
import numpy as np
import pyscreeze
import pytest
from PIL import Image

BD_GATE_SCOPE = "module"

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "vnc_display_sync.py"
REFERENCE = SCRIPT.with_name("vnc_ref_frame.png")
WHITE = (255, 255, 255)
SCREENS = [(1920, 1080, (700, 300)), (3840, 2160, (3100, 1500))]


class _FakeGui(types.ModuleType):
    """pyautogui's surface the script uses; the screen is whatever the test sets."""

    ImageNotFoundException = pyscreeze.ImageNotFoundException

    def __init__(self):
        super().__init__("pyautogui")
        self.screen = None
        self.clicks, self.moves, self.looked = [], [], 0

    def locateCenterOnScreen(self, image, confidence):
        self.looked += 1
        pil = Image.fromarray(cv2.cvtColor(self.screen, cv2.COLOR_BGR2RGB))
        found = pyscreeze.locate(image, pil, confidence=confidence)   # raises when absent
        return None if found is None else pyscreeze.center(found)

    def click(self, location):
        self.clicks.append((int(location.x), int(location.y)))

    def moveTo(self, x, y):
        self.moves.append((x, y))


_ENV = ("DISPLAY", "XDG_SESSION_TYPE")


def _load(monkeypatch, env):
    """Exec the script against a fake pyautogui with exactly `env` set for _ENV."""
    for key in _ENV:
        monkeypatch.setenv(key, "")          # records the original value for teardown
        monkeypatch.delenv(key)
        if key in env:
            monkeypatch.setenv(key, env[key])
    fake = _FakeGui()
    monkeypatch.setitem(sys.modules, "pyautogui", fake)
    spec = importlib.util.spec_from_file_location("vnc_display_sync_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, fake


@pytest.fixture
def run(monkeypatch):
    """Load the script against a fake pyautogui; return (module, fake, sleeps)."""
    mod, fake = _load(monkeypatch, {})
    mod.REFERENCE_FRAME = str(REFERENCE)
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", sleeps.append)
    return mod, fake, sleeps


def _row(w, h, at, tick=None):
    """The reference pasted on a white screen with its label; returns (screen, checkbox bounds)."""
    ref = cv2.imread(str(REFERENCE))
    screen = np.full((h, w, 3), WHITE, np.uint8)
    x, y = at
    screen[y:y + ref.shape[0], x:x + ref.shape[1]] = ref
    ys, xs = np.nonzero(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY) < 235)
    x0, y0, x1, y1 = x + xs.min(), y + ys.min(), x + xs.max(), y + ys.max()
    side = y1 - y0
    if tick == "filled":                     # Chrome's checked state: a filled blue box
        cv2.rectangle(screen, (x0, y0), (x1, y1), (232, 115, 26), -1)
    if tick is not None:
        ink = WHITE if tick == "filled" else (40, 40, 40)
        cv2.line(screen, (x0 + 3, y0 + side // 2), (x0 + side // 2, y1 - 4), ink, 2)
        cv2.line(screen, (x0 + side // 2, y1 - 4), (x1 - 3, y0 + 3), ink, 2)
    cv2.putText(screen, "Remember me", (x1 + 10, y1 - 3), cv2.FONT_HERSHEY_SIMPLEX,
                side / 30, (30, 30, 30), 1, cv2.LINE_AA)
    return screen, (x0, y0, x1, y1)


@pytest.mark.parametrize("w,h,at", SCREENS)
def test_unticked_box_is_clicked_at_its_centre(run, w, h, at):
    mod, gui, _ = run
    gui.screen, (x0, y0, x1, y1) = _row(w, h, at)
    mod.sync_display_buffer()
    assert len(gui.clicks) == 1, f"O1567-CLICKER-MISSED: {w}x{h} clicks {gui.clicks}"
    cx, cy = gui.clicks[0]
    assert x0 <= cx <= x1 and y0 <= cy <= y1, f"O1567-CLICKER-OFF-BOX: click {cx},{cy} box {x0},{y0}-{x1},{y1}"
    # the script clicks the reference PNG's centre; its margin is not quite even, so
    # "the centre" is the middle half of the box, well clear of its edges
    assert abs(cx - (x0 + x1) / 2) <= (x1 - x0) / 4 and abs(cy - (y0 + y1) / 2) <= (y1 - y0) / 4, (
        f"O1567-CLICKER-OFF-BOX: click {cx},{cy} not in the middle of {x0},{y0}-{x1},{y1}")
    assert gui.moves == [(10, 10)], "O1567-CLICKER-MAIN: pointer not parked after the click"


@pytest.mark.parametrize("tick", ["dark", "filled"])
@pytest.mark.parametrize("w,h,at", SCREENS)
def test_ticked_box_is_not_clicked_again(run, w, h, at, tick):
    # The ruled xfail node (FINDING-fx-remember-me-clicker-label-click.md): the label
    # click came from the replaced multi-size matcher; this script matches the whole
    # reference at its own size and passes, so the node is kept as an ordinary control.
    mod, gui, _ = run
    gui.screen, _ = _row(w, h, at, tick=tick)
    mod.sync_display_buffer()
    assert gui.looked == 1 and gui.clicks == [], f"O1567-CLICKER-UNTICKS: {tick} {w}x{h} clicks {gui.clicks}"


@pytest.mark.parametrize("w,h,at", SCREENS)
def test_blank_screen_is_not_clicked_and_logs_no_error(run, caplog, w, h, at):
    mod, gui, _ = run
    gui.screen = np.full((h, w, 3), WHITE, np.uint8)
    mod.sync_display_buffer()
    assert gui.looked == 1 and gui.clicks == [], f"O1567-CLICKER-BLANK-HIT: {w}x{h} clicks {gui.clicks}"
    assert not [r for r in caplog.records if r.levelname == "ERROR"], "O1567-CLICKER-BLANK-ERROR"


def test_missing_reference_waits_without_looking(run, tmp_path):
    mod, gui, sleeps = run
    mod.REFERENCE_FRAME = str(tmp_path / "absent.png")
    gui.screen, _ = _row(1920, 1080, (700, 300))
    mod.sync_display_buffer()
    assert gui.looked == 0 and gui.clicks == [] and sleeps == [mod.INITIAL_WAIT], "O1567-CLICKER-NO-REFERENCE"


def test_sigterm_stops_the_poll_loop(run):
    mod, _, _ = run
    assert mod._running
    mod._handle_signal(mod.signal.SIGTERM, None)
    assert not mod._running, "O1567-CLICKER-SIGNAL: SIGTERM left the loop running"


def test_display_defaults_to_the_vnc_display_when_unset(monkeypatch):
    _load(monkeypatch, {})
    got = {key: os.environ.get(key) for key in _ENV}
    assert got == {"DISPLAY": ":99", "XDG_SESSION_TYPE": "x11"}, f"O1567-CLICKER-DISPLAY-DEFAULT: {got}"


def test_display_already_set_is_kept(monkeypatch):
    want = {"DISPLAY": ":5", "XDG_SESSION_TYPE": "wayland"}
    _load(monkeypatch, want)
    got = {key: os.environ.get(key) for key in _ENV}
    assert got == want, f"O1567-CLICKER-DISPLAY-DEFAULT: overrode a set display {got}"
