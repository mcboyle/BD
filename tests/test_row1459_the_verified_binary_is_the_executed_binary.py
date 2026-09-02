"""Row 1459: the ffmpeg binary BD VERIFIES is the ffmpeg binary BD RUNS.

``ffmpeg_bin`` exists because a check against a pinned build is worthless when
the exec that follows it resolves a *different* build through ``PATH``.  The
motivating failure is recorded in the resolver's own docstring: the johnvansickle
7.0.2 static build SEGFAULTS on HLS+HTTPS, so an operator pins the distro build.
A caller that gates on the pin and then hands ``subprocess`` the bare string
``"ffmpeg"`` gets that segfaulting build back, and on a host where ffmpeg lives
ONLY under the pin the availability check answers True over an exec that can
never run -- the A7 fail-open shape.

Rows 440/441/442 fixed that in thumbnail_sheets, thumbnail_gen and dedup
(tests/test_row440_443_ffmpeg_probe_boundary.py observes those argv).  Every fix
tends to reproduce the defect's shape, and this one did: ``live_recorder``
resolves its ffmpeg backend through the pin at ``_detect_backends`` and then
returns a bare ``"ffmpeg"`` argv0 from ``_build_cmd`` -- for a LIVE RECORDING of
an HLS stream over HTTPS, which is precisely the case the pin exists for.

So the per-module observation is not enough.  This file adds the tree-wide
denominator that no diff can select: EVERY module in ``bulk_downloader/`` is
parsed, and none of them may hand a bare pinned binary name to a process.

No real ffmpeg, no media file and no network: the stubs are two-line ``/bin/sh``
scripts that print their own identity, which is what makes the substitution
directly observable.
"""
from __future__ import annotations

import ast
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bulk_downloader import ffmpeg_bin, live_recorder  # noqa: E402

BD_GATE_SCOPE = "repo-wide"

PKG = ROOT / "bulk_downloader"

# The binaries the ffmpeg_path pin governs. ffprobe is in the set because the
# resolver takes it from the SAME build as the pinned ffmpeg -- mixing builds is
# the inconsistency the pin exists to remove.
PINNED_NAMES = ("ffmpeg", "ffprobe")


# ── the tree-wide rule ───────────────────────────────────────────────────
def _bare_argv0_sequences(source: str, label: str) -> list[tuple[int, str]]:
    """Every list/tuple literal in `source` that is an ARGV whose argv[0] is a
    bare pinned binary name.

    A sequence is an argv (rather than a listing of tool NAMES, which
    doctor.py and diagnostics_bundle.py both legitimately hold) when a later
    element is a string constant beginning with ``-``: that is a command-line
    flag and nothing else looks like one.
    """
    found: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source, filename=label)):
        if not isinstance(node, (ast.List, ast.Tuple)) or not node.elts:
            continue
        first = node.elts[0]
        if not (isinstance(first, ast.Constant) and first.value in PINNED_NAMES):
            continue
        if any(isinstance(e, ast.Constant) and isinstance(e.value, str)
               and e.value.startswith("-") for e in node.elts[1:]):
            found.append((node.lineno, first.value))
    return found


def test_the_rule_itself_fires_and_discriminates():
    """Mutation catcher for the gate below: prove the predicate is LIVE against
    a synthetic argv and SILENT against a synthetic tool-name listing, so a
    green tree cannot be a rule that stopped matching anything."""
    positive = _bare_argv0_sequences(
        'cmd = ["ffmpeg", "-hide_banner", "-i", url, out]\n', "<positive>")
    assert [name for _line, name in positive] == ["ffmpeg"]

    probe_positive = _bare_argv0_sequences(
        'subprocess.run(["ffprobe", "-v", "error", path])\n', "<positive2>")
    assert [name for _line, name in probe_positive] == ["ffprobe"]

    # A resolved argv0 is the SHAPE THE FIX PRODUCES and must not fire.
    assert _bare_argv0_sequences(
        'cmd = [resolved, "-hide_banner", "-i", url]\n', "<resolved>") == []
    # A listing of tool NAMES is not an argv and must not fire.
    assert _bare_argv0_sequences(
        'for tool in ("ffmpeg", "ffprobe"):\n    pass\n', "<listing>") == []


def test_no_shipped_module_hands_a_bare_pinned_name_to_a_process():
    """THE TREE-WIDE GATE. Its subject is every shipped module, so no changed
    path can select it and no per-module test can replace it: the pin is only
    worth anything if EVERY exec site honours it, and the one site that does
    not is the one an attacker-controlled or merely wrong PATH entry captures.
    """
    modules = sorted(PKG.rglob("*.py"))
    assert len(modules) > 50, (
        f"denominator collapsed: only {len(modules)} modules under {PKG}")

    parsed = 0
    offenders: list[str] = []
    for path in modules:
        source = path.read_text(encoding="utf-8")
        parsed += 1
        for line, name in _bare_argv0_sequences(source, str(path)):
            offenders.append(
                f"{path.relative_to(ROOT)}:{line} execs a bare {name!r} -- "
                f"the ffmpeg_path pin cannot reach it")
    assert parsed == len(modules), "a module was skipped without failing"
    assert offenders == [], (
        "a shipped module gates on the pinned binary and runs another:\n  "
        + "\n  ".join(offenders))


# ── the behavioural half: live_recorder's recording argv ─────────────────
def _stub(path: Path, identity: str) -> None:
    path.write_text(f"#!/bin/sh\necho {identity}\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def decoy_and_pin(monkeypatch, tmp_path):
    """A DECOY ffmpeg first on PATH and a pinned ffmpeg elsewhere. Both are
    real, executable, and print which one ran."""
    pin_dir = tmp_path / "pinned"
    decoy_dir = tmp_path / "decoy-on-path"
    pin_dir.mkdir()
    decoy_dir.mkdir()
    _stub(pin_dir / "ffmpeg", "PINNED")
    _stub(decoy_dir / "ffmpeg", "DECOY")

    monkeypatch.setenv("PATH", str(decoy_dir))
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(pin_dir))
    ffmpeg_bin.reset()
    live_recorder._reset_backend_cache_for_tests()
    yield pin_dir, decoy_dir
    ffmpeg_bin.reset()
    live_recorder._reset_backend_cache_for_tests()


def _recording(tmp_path):
    return live_recorder.Recording(
        recording_id="rec-1459",
        site="chaturbate",
        room="goodroom",
        url="https://chaturbate.com/goodroom/",
        output_path=str(tmp_path / "out.ts"),
        started_at=time.time(),
    )


def _which_binary_actually_runs(argv0: str, env_path: str) -> str:
    """Execute argv0 exactly as Popen would and report which stub answered."""
    proc = subprocess.run([argv0], capture_output=True, text=True,
                          timeout=20, env={"PATH": env_path})
    return (proc.stdout or "").strip()


def test_the_recording_argv_carries_the_pinned_binary_not_the_path_one(
        decoy_and_pin, monkeypatch, tmp_path):
    pin_dir, decoy_dir = decoy_and_pin
    # Preconditions: both populations resolve, and they are distinguishable.
    assert ffmpeg_bin.ffmpeg() == str(pin_dir / "ffmpeg")
    assert shutil.which("ffmpeg") == str(decoy_dir / "ffmpeg")
    assert pin_dir != decoy_dir
    assert live_recorder._detect_backends()["ffmpeg"] == str(pin_dir / "ffmpeg")
    assert live_recorder.is_available() is True

    cmd = live_recorder._build_cmd("ffmpeg", _recording(tmp_path))
    assert cmd is not None, "the availability gate said True; the build refused"
    assert cmd[0] == str(pin_dir / "ffmpeg"), (
        f"argv0 is {cmd[0]!r}: the gate verified the pinned build and the exec "
        f"would resolve a different one")

    # The substitution is not hypothetical -- run argv0 and read its identity.
    assert _which_binary_actually_runs(cmd[0], str(decoy_dir)) == "PINNED"
    assert _which_binary_actually_runs("ffmpeg", str(decoy_dir)) == "DECOY", (
        "fixture precondition: the decoy must be what a bare name resolves to")


def test_no_pin_still_records_through_the_path_binary(monkeypatch, tmp_path):
    """NEGATIVE CONTROL. A pin fix that refuses legitimate work is the mirror
    of the defect. With no pin, the PATH build must still be selected and the
    argv must still be built."""
    path_dir = tmp_path / "ambient"
    path_dir.mkdir()
    _stub(path_dir / "ffmpeg", "AMBIENT")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: "")
    ffmpeg_bin.reset()
    live_recorder._reset_backend_cache_for_tests()
    try:
        assert ffmpeg_bin.ffmpeg() == str(path_dir / "ffmpeg")
        assert live_recorder.is_available() is True
        cmd = live_recorder._build_cmd("ffmpeg", _recording(tmp_path))
        assert cmd is not None, "no pin must not refuse a legitimate recording"
        assert cmd[0] == str(path_dir / "ffmpeg")
        assert _which_binary_actually_runs(cmd[0], str(path_dir)) == "AMBIENT"
        # the rest of the argv is unchanged by the fix
        assert "-hide_banner" in cmd
        assert cmd[-1] == str(tmp_path / "out.ts")
        assert "https://chaturbate.com/goodroom/" in cmd
    finally:
        ffmpeg_bin.reset()
        live_recorder._reset_backend_cache_for_tests()


def test_an_unresolvable_backend_refuses_with_its_own_diagnostic(
        monkeypatch, tmp_path):
    """A7: several refusals share this return value, so the caller must name
    the step that failed. An unresolved backend and a rejected room both make
    _build_cmd return None; 'unable to build command' sends the operator to
    inspect the URL when the real cause is a missing binary."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: "")
    ffmpeg_bin.reset()
    live_recorder._reset_backend_cache_for_tests()
    try:
        assert ffmpeg_bin.ffmpeg() is None
        assert live_recorder._detect_backends()["ffmpeg"] is None
        rec = _recording(tmp_path)
        assert live_recorder._build_cmd("ffmpeg", rec) is None

        unresolved = live_recorder._refusal_reason("ffmpeg", rec)
        assert unresolved == "backend_unresolved: ffmpeg", unresolved

        # The OTHER refusal keeps its own distinct wording, so the two cannot
        # launder each other. streamlink resolves nowhere here either, so the
        # rejected-room case is proved on a backend that IS resolvable.
        _stub(empty / "streamlink", "STREAMLINK")
        live_recorder._reset_backend_cache_for_tests()
        assert live_recorder._detect_backends()["streamlink"] is not None
        bad = _recording(tmp_path)
        bad.room = "foo;rm"
        assert live_recorder._build_cmd("streamlink", bad) is None
        assert live_recorder._refusal_reason("streamlink", bad) == (
            "unable to build command")
    finally:
        ffmpeg_bin.reset()
        live_recorder._reset_backend_cache_for_tests()
