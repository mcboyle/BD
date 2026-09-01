"""Rows 440-443: ffmpeg-family probes honor one executable boundary.

The older MOD-4 gate proves that callers import ``ffmpeg_bin`` and do not call
``which`` themselves.  That is not enough: a caller can pass those checks and
still hand the literal ``ffmpeg`` or ``ffprobe`` to ``subprocess``.  These tests
observe the argv that production code actually hands to that boundary.

No real ffmpeg process or media file is used.  The boundary doubles return
hand-derived probe output and create the output file that a successful ffmpeg
invocation would have created.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from bulk_downloader import (
    dedup,
    enrichment,
    ffmpeg_bin,
    thumbnail_gen,
    thumbnail_sheets,
)


BD_GATE_SCOPE = "module"


def _stub_binary(path: Path) -> None:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture
def binary_layout(monkeypatch, tmp_path):
    """A pin and PATH population that are both nonempty and distinguishable."""
    pin_dir = tmp_path / "pinned"
    path_dir = tmp_path / "ambient-path"
    pin_dir.mkdir()
    path_dir.mkdir()
    for directory in (pin_dir, path_dir):
        for name in ("ffmpeg", "ffprobe"):
            _stub_binary(directory / name)

    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: str(pin_dir))
    ffmpeg_bin.reset()
    yield pin_dir, path_dir
    ffmpeg_bin.reset()


@dataclass
class _Boundary:
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)

    def _record(self, kind: str, argv) -> tuple[str, ...]:
        call = tuple(str(part) for part in argv)
        self.calls.append((kind, call))
        return call

    def check_output(self, argv, **_kwargs):
        call = self._record("check_output", argv)
        if "stream=height" in call:
            return b"224\n"
        return b"20.0\n"

    def check_call(self, argv, **_kwargs):
        call = self._record("check_call", argv)
        Path(call[-1]).write_bytes(b"generated-image")
        return 0

    def run(self, argv, **_kwargs):
        call = self._record("run", argv)
        if Path(call[0]).name == "ffprobe":
            if "stream=codec_name:format=duration" in call:
                stdout = "h264\n12.5\n"
            else:
                stdout = "20.0\n"
            return subprocess.CompletedProcess(call, 0, stdout=stdout, stderr="")
        Path(call[-1]).write_bytes(b"generated-image")
        return subprocess.CompletedProcess(call, 0, stdout="", stderr="")

    @property
    def argv0(self) -> list[str]:
        return [call[0] for _kind, call in self.calls]


def _assert_pin_and_path_preconditions(pin_dir: Path, path_dir: Path) -> None:
    """Prove both competing populations resolve before judging precedence."""
    assert ffmpeg_bin.ffmpeg() == str(pin_dir / "ffmpeg")
    assert ffmpeg_bin.ffprobe() == str(pin_dir / "ffprobe")
    assert shutil.which("ffmpeg") == str(path_dir / "ffmpeg")
    assert shutil.which("ffprobe") == str(path_dir / "ffprobe")
    assert pin_dir != path_dir


def _empty_binary_resolution(monkeypatch, tmp_path) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: "")
    ffmpeg_bin.reset()
    assert ffmpeg_bin.ffmpeg() is None
    assert ffmpeg_bin.ffprobe() is None


# Row 440: thumbnail_sheets


def test_row440_every_thumbnail_sheets_process_gets_the_pinned_argv0(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    assert thumbnail_sheets.is_available() is True

    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    assert source.is_file() and source.stat().st_size > 0

    boundary = _Boundary()
    monkeypatch.setattr(thumbnail_sheets.subprocess, "check_output",
                        boundary.check_output)
    monkeypatch.setattr(thumbnail_sheets.subprocess, "check_call",
                        boundary.check_call)

    before = len(boundary.calls)
    single = thumbnail_sheets.single_thumb(
        str(source), out_path=str(tmp_path / "single.jpg"))
    single_calls = boundary.calls[before:]
    assert single["ok"] is True, single
    assert single["timestamp_seconds"] == 10.0
    assert [kind for kind, _argv in single_calls] == [
        "check_output", "check_call"]
    assert [argv[0] for _kind, argv in single_calls] == [
        str(pin_dir / "ffprobe"), str(pin_dir / "ffmpeg")]

    before = len(boundary.calls)
    contact = thumbnail_sheets.contact_sheet(
        str(source), rows=2, cols=2,
        out_path=str(tmp_path / "contact.jpg"))
    contact_calls = boundary.calls[before:]
    assert contact["ok"] is True, contact
    assert contact["duration_seconds"] == 20.0
    assert contact["frame_count"] == 4
    assert [kind for kind, _argv in contact_calls] == [
        "check_output", "check_call"]
    assert [argv[0] for _kind, argv in contact_calls] == [
        str(pin_dir / "ffprobe"), str(pin_dir / "ffmpeg")]

    before = len(boundary.calls)
    sprite = thumbnail_sheets.sprite_sheet(str(source), count=2)
    sprite_calls = boundary.calls[before:]
    assert sprite["ok"] is True, sprite
    assert sprite["duration_seconds"] == 20.0
    assert sprite["tile_height"] == 112
    assert [kind for kind, _argv in sprite_calls] == [
        "check_output", "check_call", "check_output"]
    assert [argv[0] for _kind, argv in sprite_calls] == [
        str(pin_dir / "ffprobe"), str(pin_dir / "ffmpeg"),
        str(pin_dir / "ffprobe")]

    assert len(boundary.calls) == 7
    assert all(not os.path.basename(argv0) == argv0
               for argv0 in boundary.argv0)


def test_row440_absent_binaries_refuse_before_any_subprocess(
        monkeypatch, tmp_path):
    _empty_binary_resolution(monkeypatch, tmp_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    assert source.is_file()

    launched = []

    def unexpected(argv, **_kwargs):
        launched.append(tuple(argv))
        raise AssertionError("subprocess must not launch when binaries are absent")

    monkeypatch.setattr(thumbnail_sheets.subprocess, "check_output", unexpected)
    monkeypatch.setattr(thumbnail_sheets.subprocess, "check_call", unexpected)
    results = [
        thumbnail_sheets.single_thumb(str(source)),
        thumbnail_sheets.contact_sheet(str(source)),
        thumbnail_sheets.sprite_sheet(str(source)),
    ]
    assert len(results) == 3
    assert all(result["ok"] is False for result in results)
    assert all("PATH" in result["error"] for result in results)
    assert launched == []


def test_row440_a_binary_lost_after_the_gate_names_the_failed_probe(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    assert thumbnail_sheets.is_available() is True
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")

    calls = []

    def missing(argv, **_kwargs):
        calls.append(tuple(argv))
        raise FileNotFoundError("pinned ffprobe disappeared")

    monkeypatch.setattr(thumbnail_sheets.subprocess, "check_output", missing)
    result = thumbnail_sheets.single_thumb(str(source))
    assert len(calls) == 1
    assert calls[0][0] == str(pin_dir / "ffprobe")
    assert result["ok"] is False
    assert result["error"].startswith("ffprobe_exec_failed:"), result


# Row 441: thumbnail_gen


def test_row441_thumbnail_gen_hands_every_process_the_pinned_argv0(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    assert thumbnail_gen.is_available() is True
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    assert source.is_file() and source.stat().st_size > 0

    boundary = _Boundary()
    monkeypatch.setattr(thumbnail_gen.subprocess, "run", boundary.run)
    duration = thumbnail_gen._probe_duration(str(source))
    single = thumbnail_gen.generate_single_frame(
        str(source), str(tmp_path / "single-gen.jpg"))
    sheet = thumbnail_gen.generate_contact_sheet(
        str(source), str(tmp_path / "sheet-gen.jpg"), rows=2, cols=2)

    assert duration == 20.0
    assert single.ok is True and single.duration_s == 20.0, single
    assert sheet.ok is True and sheet.duration_s == 20.0, sheet
    assert len(boundary.calls) == 5
    assert boundary.argv0 == [
        str(pin_dir / "ffprobe"),
        str(pin_dir / "ffprobe"), str(pin_dir / "ffmpeg"),
        str(pin_dir / "ffprobe"), str(pin_dir / "ffmpeg"),
    ]
    assert boundary.argv0.count(str(pin_dir / "ffprobe")) == 3
    assert boundary.argv0.count(str(pin_dir / "ffmpeg")) == 2
    assert boundary.argv0.count(str(path_dir / "ffprobe")) == 0
    assert boundary.argv0.count(str(path_dir / "ffmpeg")) == 0


def test_row441_empty_pin_uses_the_path_population_at_the_boundary(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    monkeypatch.setattr(ffmpeg_bin, "_pinned_dir", lambda: "")
    ffmpeg_bin.reset()
    assert ffmpeg_bin.ffmpeg() == str(path_dir / "ffmpeg")
    assert ffmpeg_bin.ffprobe() == str(path_dir / "ffprobe")
    assert pin_dir != path_dir
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")

    boundary = _Boundary()
    monkeypatch.setattr(thumbnail_gen.subprocess, "run", boundary.run)
    result = thumbnail_gen.generate_single_frame(
        str(source), str(tmp_path / "path-single.jpg"))
    assert result.ok is True, result
    assert boundary.argv0 == [
        str(path_dir / "ffprobe"), str(path_dir / "ffmpeg")]
    assert all(not argv0.startswith(str(pin_dir)) for argv0 in boundary.argv0)


def test_row441_a_missing_cached_ffprobe_is_a_named_error_not_zero(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    calls = []

    def missing(argv, **_kwargs):
        calls.append(tuple(argv))
        raise FileNotFoundError("pinned ffprobe disappeared")

    monkeypatch.setattr(thumbnail_gen.subprocess, "run", missing)
    with pytest.raises(thumbnail_gen.FFprobeError,
                       match=r"^ffprobe_exec_failed:"):
        thumbnail_gen._probe_duration(str(source))
    assert len(calls) == 1
    assert calls[0][0] == str(pin_dir / "ffprobe")


# Row 442: dedup metadata


def test_row442_dedup_hands_ffprobe_the_pinned_argv0(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    assert dedup.is_ffmpeg_available() is True
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    assert source.is_file() and source.stat().st_size > 0

    boundary = _Boundary()
    monkeypatch.setattr(subprocess, "run", boundary.run)
    meta = dedup._ffprobe_meta(str(source))
    assert len(boundary.calls) == 1
    assert boundary.argv0 == [str(pin_dir / "ffprobe")]
    assert boundary.argv0.count(str(path_dir / "ffprobe")) == 0
    assert meta == {"duration_sec": 12.5, "codec": "h264", "error": ""}


def test_row442_unavailable_metadata_is_stored_as_null_not_measured_zero(
        monkeypatch, tmp_path):
    _empty_binary_resolution(monkeypatch, tmp_path)
    calls = []

    def unexpected(argv, **_kwargs):
        calls.append(tuple(argv))
        raise AssertionError("unresolved ffprobe must not launch")

    monkeypatch.setattr(subprocess, "run", unexpected)
    meta = dedup._ffprobe_meta(str(tmp_path / "source.mp4"))
    assert calls == []
    assert meta == {
        "duration_sec": None,
        "codec": None,
        "error": "ffprobe_unavailable",
    }

    result = dedup.HashResult(
        ok=True,
        path=str(tmp_path / "source.mp4"),
        hash_hex="0123456789abcdef",
        duration_sec=meta["duration_sec"],
        codec=meta["codec"],
        metadata_error=meta["error"],
    )
    registry = dedup.HashRegistry(str(tmp_path / "hashes.sqlite"))
    assert registry.add(result) is True
    stored = registry.lookup(result.path)
    assert stored is not None
    assert stored["duration_sec"] is None
    assert stored["ffprobe_codec"] is None


def test_row442_corrupt_input_fails_open_through_the_pinned_path(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"corrupt")
    calls = []

    def corrupt(argv, **_kwargs):
        call = tuple(str(part) for part in argv)
        calls.append(call)
        return subprocess.CompletedProcess(
            call, 1, stdout="", stderr="Invalid data found")

    monkeypatch.setattr(subprocess, "run", corrupt)
    meta = dedup._ffprobe_meta(str(source))
    assert len(calls) == 1
    assert calls[0][0] == str(pin_dir / "ffprobe")
    assert meta == {
        "duration_sec": None,
        "codec": None,
        "error": "ffprobe_rc_1:Invalid data found",
    }


# Row 443: enrichment decode boundary


def _reset_enrichment_tools(monkeypatch) -> None:
    monkeypatch.setattr(enrichment, "_FFMPEG", None)
    monkeypatch.setattr(enrichment, "_FFPROBE", None)
    monkeypatch.setattr(enrichment, "_TOOLS_WARNED", False)


def test_row443_valid_utf8_probe_is_independent_of_ambient_ascii(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    _reset_enrichment_tools(monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    payload = json.dumps(
        {"format": {"tags": {"title": "café"}}, "streams": []},
        ensure_ascii=False,
    ).encode("utf-8")
    calls = []

    def locale_sensitive_run(argv, **kwargs):
        calls.append((tuple(str(part) for part in argv), dict(kwargs)))
        encoding = kwargs.get("encoding") or "ascii"
        stdout = payload.decode(encoding)
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(enrichment.subprocess, "run", locale_sensitive_run)
    escaped = None
    result = None
    try:
        result = enrichment.probe(str(source))
    except UnicodeDecodeError as exc:
        escaped = exc

    # Preconditions precede the verdict: the boundary fired once, its payload
    # was nonempty valid UTF-8, and the forced ambient coding cannot decode it.
    assert len(calls) == 1
    assert calls[0][0][0] == str(pin_dir / "ffprobe")
    assert len(payload) > 0
    with pytest.raises(UnicodeDecodeError):
        payload.decode("ascii")

    assert escaped is None, f"probe leaked {type(escaped).__name__}: {escaped}"
    assert result is not None
    assert result["format"]["tags"]["title"] == "café"
    assert calls[0][1].get("encoding") == "utf-8"


def test_row443_undecodable_utf8_is_unknown_not_an_escaped_exception(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    _reset_enrichment_tools(monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    payload = b'{"format":{"title":"\xff"}}'
    calls = []

    def undecodable(argv, **kwargs):
        calls.append((tuple(str(part) for part in argv), dict(kwargs)))
        encoding = kwargs.get("encoding") or "ascii"
        payload.decode(encoding)
        raise AssertionError("payload unexpectedly decoded")

    monkeypatch.setattr(enrichment.subprocess, "run", undecodable)
    result = enrichment.probe(str(source))
    assert len(calls) == 1
    assert calls[0][0][0] == str(pin_dir / "ffprobe")
    assert len(payload) > 0
    with pytest.raises(UnicodeDecodeError):
        payload.decode("utf-8")
    assert result is None


def test_row443_malformed_json_is_still_unknown_via_json_error_branch(
        binary_layout, monkeypatch, tmp_path):
    pin_dir, path_dir = binary_layout
    _assert_pin_and_path_preconditions(pin_dir, path_dir)
    _reset_enrichment_tools(monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    calls = []

    def malformed(argv, **kwargs):
        calls.append((tuple(str(part) for part in argv), dict(kwargs)))
        return subprocess.CompletedProcess(
            argv, 0, stdout="{not-json", stderr="")

    monkeypatch.setattr(enrichment.subprocess, "run", malformed)
    result = enrichment.probe(str(source))
    assert len(calls) == 1
    assert calls[0][0][0] == str(pin_dir / "ffprobe")
    assert calls[0][1].get("encoding") == "utf-8"
    assert result is None


def test_row443_unavailable_ffprobe_returns_unknown_without_launch(
        monkeypatch, tmp_path):
    _empty_binary_resolution(monkeypatch, tmp_path)
    _reset_enrichment_tools(monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not-real-media")
    calls = []

    def unexpected(argv, **_kwargs):
        calls.append(tuple(argv))
        raise AssertionError("unavailable ffprobe must not launch")

    monkeypatch.setattr(enrichment.subprocess, "run", unexpected)
    assert enrichment.probe(str(source)) is None
    assert calls == []
