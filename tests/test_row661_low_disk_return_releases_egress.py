"""Row 661 -- _spawn_recording's low-disk return leaks prepared_egress.

ACCEPTANCE (register, row 661): bulk_downloader/live_recorder.py's
_spawn_recording, around its low-disk-refusal return, does not release a
caller-supplied prepared_egress -- while the sibling "no backend" branch a
few lines above it does. Both branches return without launching a subprocess,
so both owe the caller the same release; the low-disk branch silently does
not pay it. This is a bounded resource leak (an unreleased PreparedHttpProxy,
which owns a loopback bridge thread/socket when the resolved proxy was
SOCKS5), independently seen by both w2/egressc lenses per the row.

RED-first: on the intended defect, exactly one close() must fire (the
caller's own fallback release, since _spawn_recording issued none) --
asserted by an exact call count, not a truthy check, so a branch that
double-frees or double-skips is equally caught.

Every test that rebinds a module-level live_recorder function (preferred_
backend, _build_cmd, _refusal_reason) restores it in a finally block --
those are process-global rebindings, not per-test state _reset_for_tests
clears, and this file must not leak them into any other test collected in
the same pytest process.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

BD_GATE_SCOPE = "repo-wide"


class _FakeEgress:
    """Stands in for PreparedHttpProxy: records close() call count."""

    def __init__(self) -> None:
        self.proxy_url = "http://127.0.0.1:1"
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def _make_recording(live_recorder, tmp_path: Path):
    return live_recorder.Recording(
        recording_id="rid-661",
        site="chaturbate",
        room="somemodel",
        url="https://chaturbate.com/somemodel",
        output_path=str(tmp_path / "out" / "somemodel.mp4"),
        started_at=0.0,
    )


def _reset(live_recorder):
    live_recorder._reset_for_tests()
    live_recorder._reset_backend_cache_for_tests()


@contextlib.contextmanager
def _patched_backend(live_recorder, backend):
    """Rebind preferred_backend for the duration of one test, then restore."""
    original = live_recorder.preferred_backend
    live_recorder.preferred_backend = backend
    try:
        yield
    finally:
        live_recorder.preferred_backend = original


def test_low_disk_return_releases_the_caller_supplied_egress(tmp_path):
    """The defect: low-disk return does not call prepared_egress.close()."""
    from bulk_downloader import live_recorder

    _reset(live_recorder)
    with _patched_backend(live_recorder, lambda: "ffmpeg"):
        live_recorder.init(str(tmp_path / "state"), disk_check=lambda _out_dir: 0.5)

        rec = _make_recording(live_recorder, tmp_path)
        egress = _FakeEgress()

        live_recorder._spawn_recording("rid-661", rec, prepared_egress=egress)

        assert rec.state == "failed", "low-disk path must still fail the recording"
        assert egress.close_calls == 1, (
            f"expected exactly one close() on the low-disk return, got "
            f"{egress.close_calls} -- prepared_egress must be released the "
            f"same way the sibling no-backend branch releases it"
        )


def test_process_recording_reaches_the_release_through_its_real_caller(tmp_path):
    """Self-mutation seam: every other test in this file calls
    _spawn_recording directly, so deleting _process_recording's own call to
    it (the actual production call site this row's fix protects) would not
    fail any of them. Drive the low-disk scenario through _process_recording
    itself -- deleting that call leaves rec.state at "pending" and the
    egress unclosed, so this must fail if that call is removed."""
    from bulk_downloader import live_recorder

    _reset(live_recorder)
    egress = _FakeEgress()
    with _patched_backend(live_recorder, lambda: "ffmpeg"):
        live_recorder.init(
            str(tmp_path / "state"),
            disk_check=lambda _out_dir: 0.5,
            egress_prepare=lambda _site: egress,
        )
        rec = _make_recording(live_recorder, tmp_path)
        rec.state = "pending"

        live_recorder._process_recording("rid-661e", rec)

        assert rec.state == "failed", (
            "low-disk path reached through _process_recording's own call "
            "site must still fail the recording"
        )
        assert egress.close_calls == 1, (
            f"expected exactly one close() on the low-disk return reached "
            f"through _process_recording's call to _spawn_recording, got "
            f"{egress.close_calls}"
        )


def test_positive_control_disk_check_hook_actually_fires(tmp_path):
    """Prove the probe can say yes: disk_check is wired and its return value
    is what drives the low-disk branch, before trusting the zero above."""
    from bulk_downloader import live_recorder

    _reset(live_recorder)
    calls = []

    def _fake_disk_check(out_dir):
        calls.append(out_dir)
        return 0.5

    with _patched_backend(live_recorder, lambda: "ffmpeg"):
        live_recorder.init(str(tmp_path / "state"), disk_check=_fake_disk_check)

        rec = _make_recording(live_recorder, tmp_path)
        live_recorder._spawn_recording("rid-661b", rec, prepared_egress=_FakeEgress())

        assert len(calls) == 1, "disk_check hook must fire exactly once per spawn"
        assert calls[0] == str(Path(rec.output_path).parent)
        assert rec.last_error is not None and rec.last_error.startswith("low_disk:")


def test_negative_control_no_backend_branch_already_releases_once(tmp_path):
    """Distinctive diagnostic: the sibling branch this gate is modeled on
    already releases exactly once -- if this ever regresses to zero or two,
    that is a different bug than the one this gate exists to catch."""
    from bulk_downloader import live_recorder

    _reset(live_recorder)
    with _patched_backend(live_recorder, lambda: None):
        live_recorder.init(str(tmp_path / "state"), disk_check=lambda _out_dir: 0.5)

        rec = _make_recording(live_recorder, tmp_path)
        egress = _FakeEgress()
        live_recorder._spawn_recording("rid-661c", rec, prepared_egress=egress)

        assert rec.last_error == "no backend"
        assert egress.close_calls == 1, (
            f"no-backend branch's own release regressed: expected exactly "
            f"1, got {egress.close_calls}"
        )


def test_negative_control_build_cmd_refusal_already_releases_once(tmp_path):
    """Exact-count negative control on a THIRD early-return branch (past the
    disk check, cmd build refused) that already releases correctly today.
    _build_cmd/_refusal_reason are monkeypatched directly rather than relying
    on whether a real ffmpeg/streamlink binary happens to resolve on this
    host -- that dependency would make the control flaky by environment, not
    by the code under test. This is the control that proves the low-disk
    branch is the ONLY one missing its close(): every other early return in
    this function -- no backend (above), and build-refused (here) -- already
    pays it exactly once. If this ever regressed to zero or two, that would
    be a distinct defect from row 661's."""
    from bulk_downloader import live_recorder

    _reset(live_recorder)
    orig_build_cmd = live_recorder._build_cmd
    orig_refusal_reason = live_recorder._refusal_reason
    try:
        with _patched_backend(live_recorder, lambda: "ffmpeg"):
            live_recorder._build_cmd = lambda *a, **k: None
            live_recorder._refusal_reason = lambda *a, **k: "unable to build command"
            live_recorder.init(str(tmp_path / "state"), disk_check=lambda _out_dir: 500.0)

            rec = _make_recording(live_recorder, tmp_path)
            egress = _FakeEgress()
            live_recorder._spawn_recording("rid-661d", rec, prepared_egress=egress)

            assert rec.state == "failed"
            assert rec.last_error == "unable to build command"
            assert egress.close_calls == 1, (
                f"build-cmd-refusal branch's own release regressed: expected "
                f"exactly 1, got {egress.close_calls}"
            )
    finally:
        live_recorder._build_cmd = orig_build_cmd
        live_recorder._refusal_reason = orig_refusal_reason
