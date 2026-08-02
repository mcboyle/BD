"""v3.66.840 -- the extractor completion paths crash before they can complete.

`detect.safe_dest(path)` calls ``path.exists()``: it takes a Path. Six sites in
runner_extractors.py hand it ``rendered``, a str from
``resolve_filename_template`` (or a ``title_root + ext`` fallback). Measured:

    safe_dest('Scene Title.mp4')
    -> AttributeError: 'str' object has no attribute 'exists'

Every caller in runner.py wraps the attempt in ``except Exception`` and falls
through to the teach/JD/qB path, so the failure is SILENT -- five download
backends (jsonapi, vixen, dl8, aylo, and both library-extractor arms) degrade
instead of erroring, and nothing above notices.

THE OBVIOUS FIX IS WRONG, and this file pins that too. ``safe_dest(Path(rendered))``
stops the crash but resolves a bare relative name against the process CWD, so
collision detection runs in the wrong directory: it under-detects, returns the
un-suffixed name, and the download then overwrites an existing file. The
destination must be resolved INSIDE the download dir --
``safe_dest(Path(dl_dir) / rendered)`` -- which is exactly what
``_try_plugin_extractor`` already does and is the in-tree reference.

Two claims are kept apart deliberately, because they are different:
  * "cannot work as written" -- what this file proves, by execution.
  * "has never worked on this box" -- NOT asserted here. See register 15.12.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "bulk_downloader" / "runner_extractors.py"


# ── canary: the premise must be real, or every rule below is vacuous ─────────


def test_canary_safe_dest_really_requires_a_path():
    from bulk_downloader.detect import safe_dest

    with pytest.raises(AttributeError):
        safe_dest("Scene Title.mp4")

    # ...and accepts a Path, so the fix direction is the one claimed.
    assert isinstance(safe_dest(Path("/nonexistent-xyz/Scene Title.mp4")), Path)


# ── the structural rule ─────────────────────────────────────────────────────


def _safe_dest_calls(src: str) -> list[ast.Call]:
    tree = ast.parse(src)
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else None)
            if name == "safe_dest":
                out.append(n)
    return out


def test_canary_the_scan_finds_safe_dest_calls():
    """A zero denominator would make the rule below certify an empty set."""
    calls = _safe_dest_calls(TARGET.read_text(encoding="utf-8"))
    assert calls, (
        "no safe_dest call found in runner_extractors.py -- this gate cannot "
        "see its subject and therefore cannot answer"
    )


def test_every_safe_dest_call_resolves_inside_the_download_dir():
    """The argument must be a path JOIN, never a bare rendered name.

    Asserting on the argument's SHAPE rather than on a type (which AST cannot
    know) is the durable form: `Path(dl_dir) / rendered` is a BinOp with Div,
    and a bare `rendered` is a Name. The second is the defect.
    """
    src = TARGET.read_text(encoding="utf-8")
    offenders = []
    for call in _safe_dest_calls(src):
        arg = call.args[0] if call.args else None
        joined = isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Div)
        if not joined:
            offenders.append(f"{TARGET.name}:{call.lineno}: "
                             f"safe_dest({ast.unparse(arg) if arg else '?'})")
    assert not offenders, (
        "safe_dest is called without joining the download dir, so the "
        "argument is a bare name (a str -> AttributeError) or a CWD-relative "
        "path (collision detection in the wrong directory):\n  "
        + "\n  ".join(offenders)
    )


# ── the behavioural rule: drive the real method ─────────────────────────────


class _Res:
    """The ExtractResult shape _try_library_extractor actually reads.

    Unlisted attributes resolve to None: the real ExtractResult carries a long
    tail of optional metadata (thumbnail_url, tags, ...) that this test is not
    about. Enumerating them would make the stub a second, drifting copy of a
    dataclass -- and a missing one would surface as an AttributeError that
    reads exactly like the defect under test, which is why the assertion below
    discriminates on the message rather than the exception type.
    """
    def __getattr__(self, _name):  # only called for attributes not set below
        return None

    ok = True
    is_hls = False
    url = "https://example.invalid/v.mp4"
    file_url = "https://example.invalid/v.mp4"
    title = "Scene Title"
    quality = "1080p"
    extractor = "probe"
    author = "Studio"
    upload_date = "2026-01-02"
    duration_sec = 61.0
    error = None
    error_detail = None


def _runner(tmp_path, template="{filename}{ext}"):
    from bulk_downloader.db import db_init
    from bulk_downloader.runner import SiteRunner

    db_init()
    return SiteRunner("extdest", {
        "name": "ExtDest",
        "download_dir": str(tmp_path / "dl"),
        "filename_template": template,
        "quality_preference": "best",
    })


def test_the_library_extractor_path_reaches_its_completion(clean_workdir, tmp_path):
    """End to end through the real method: it must not die at safe_dest.

    Only the extractor library and the byte-mover are stubbed -- the
    destination-resolution code under test runs for real.
    """
    from unittest import mock

    r = _runner(tmp_path)
    (tmp_path / "dl").mkdir(parents=True, exist_ok=True)

    captured = {}

    # Signature read from runner_transport.py:144, not guessed:
    #   _do_direct_http_download(self, page_url, file_url, output_path, referer="")
    # patch.object on the CLASS installs a MagicMock, which is not a descriptor,
    # so `self` is NOT passed through -- the first arg here is page_url.
    def _fake_download(page_url, file_url, output_path, referer=""):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"\0" * 2048)
        captured["dest"] = output_path
        return True

    with mock.patch("bulk_downloader.extractors.is_supported_url",
                    return_value="probe"), \
         mock.patch("bulk_downloader.extractors.is_available", return_value=True), \
         mock.patch("bulk_downloader.extractors.extract", return_value=_Res()), \
         mock.patch.object(type(r), "_do_direct_http_download",
                           side_effect=_fake_download, create=True):
        try:
            r._try_library_extractor("https://example.invalid/scene")
        except AttributeError as e:
            # Discriminate: only the .exists() shape is the defect under test.
            # Any other AttributeError is this harness's own stub being
            # incomplete, and must surface as an error rather than be reported
            # as the bug -- otherwise the gate would "prove" the defect on a
            # tree where it had already been fixed.
            if "exists" not in str(e):
                raise
            pytest.fail(
                "the library-extractor path raised before it could complete: "
                f"{e}. This is the defect: safe_dest() is handed a str, and "
                "runner.py's caller swallows it so the failure is silent."
            )

    assert "dest" in captured, (
        "the extractor path never reached the download step -- it fell "
        "through before resolving a destination"
    )
    dest = captured["dest"]
    assert os.path.isabs(dest), f"destination is not absolute: {dest!r}"
    assert str(tmp_path / "dl") in dest, (
        f"destination {dest!r} is not inside the configured download_dir "
        f"{str(tmp_path / 'dl')!r} -- resolving against the CWD is the "
        "under-detecting variant of this bug, not a fix for it"
    )
