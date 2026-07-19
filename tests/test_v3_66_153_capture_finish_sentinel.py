"""v3.66.153 — capture_session non-interactive finish (cockpit / noVNC path).

A cockpit-launched capture runs as a subprocess with no controlling terminal,
so the interactive ``input()`` stop-trigger would get EOF and save instantly —
the browser flashes open and closed before the operator can log in. The fix
keeps the browser alive and waits for a small sentinel the operator drops from
a second shell (``touch <out_dir>/FINISH`` to save, ``.../CANCEL`` to discard),
bounded by ``--max-seconds`` so an abandoned capture still saves before the
cockpit runner's 1800s kill.

The browser leg is not runtime-testable in the sandbox; the finish logic is
pure and poll-based, so that is what is exercised here. No browser, no network.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from tools import capture_session as cs  # noqa: E402


def _reset() -> None:
    cs._FINISH["signalled"] = False


def _td() -> Path:
    return Path(tempfile.mkdtemp(prefix="cap153_"))


def _args(out: Path, finish_file=None, max_seconds: int = 2):
    return types.SimpleNamespace(
        out=str(out), finish_file=finish_file, max_seconds=max_seconds
    )


def test_finish_sentinel_detected() -> None:
    _reset()
    d = _td()
    try:
        (d / "FINISH").write_text("")
        assert cs._wait_for_finish(d, max_wait=5) == "finish"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_cancel_sentinel_detected() -> None:
    _reset()
    d = _td()
    try:
        (d / "CANCEL").write_text("")
        assert cs._wait_for_finish(d, max_wait=5) == "cancel"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_timeout_when_no_sentinel() -> None:
    _reset()
    d = _td()
    try:
        # max_wait clamps to >= 1s; one poll then timeout (~1s).
        assert cs._wait_for_finish(d, max_wait=1) == "timeout"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_signal_flag_finishes() -> None:
    _reset()
    d = _td()
    try:
        cs._FINISH["signalled"] = True
        assert cs._wait_for_finish(d, max_wait=5) == "signal"
    finally:
        _reset()
        shutil.rmtree(d, ignore_errors=True)


def test_explicit_finish_file_override() -> None:
    _reset()
    d = _td()
    try:
        custom = d / "nested" / "DONE"
        custom.parent.mkdir(parents=True)
        custom.write_text("")
        # default <out_dir>/FINISH is absent; the override path triggers.
        assert cs._wait_for_finish(d, max_wait=5, finish_file=str(custom)) == "finish"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_finish_takes_priority_over_timeout() -> None:
    _reset()
    d = _td()
    try:
        (d / "FINISH").write_text("")
        # an already-present sentinel wins immediately, even on a tiny budget
        assert cs._wait_for_finish(d, max_wait=1) == "finish"
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_await_discards_on_cancel_and_cleans_up() -> None:
    _reset()
    d = _td()
    try:
        out = d / "cap.wacz"
        (d / "CANCEL").write_text("")
        discarded = cs._await_noninteractive_finish(_args(out), "https://x/movie", False)
        assert discarded is True
        # sentinels removed so a stale file can't trigger the next capture
        assert not (d / "CANCEL").exists()
        assert not (d / "FINISH").exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_await_saves_on_finish_and_cleans_up() -> None:
    _reset()
    d = _td()
    try:
        out = d / "cap.wacz"
        (d / "FINISH").write_text("")
        discarded = cs._await_noninteractive_finish(_args(out), "https://x/movie", False)
        assert discarded is False
        assert not (d / "FINISH").exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_default_max_seconds_under_runner_kill() -> None:
    # The default must stay below the cockpit runner's 1800s subprocess timeout
    # so the capture saves gracefully instead of being SIGKILLed mid-write.
    p = cs._build_parser().parse_args(["--url", "https://x", "--out", "o.wacz"])
    assert p.max_seconds < 1800
    assert p.finish_file is None


def test_tty_branch_preserved_in_source() -> None:
    # Terminal use must be unchanged: the TTY path still calls input(). Guard
    # against a refactor silently removing the ENTER-to-save behaviour.
    src = (_REPO / "tools" / "capture_session.py").read_text(encoding="utf-8")
    assert "sys.stdin.isatty()" in src
    assert "input()" in src
