"""BP-INT (v3.66.284): download-integrity size gate.

A transfer whose stream ends before the advertised Content-Length is satisfied
is TRUNCATED. The .part must never be promoted to the final name (a short file
must not masquerade as `done`); instead the .part is removed and the URL routes
to `needs_review` so the operator can force a re-download (BP-VH3).

Atomic-rename + size-check are sandbox-verifiable here; real-network integrity
is exercised on stash. Pure staticmethods / real temp files — no SiteRunner
instance needed.

Zero-arg functions; repo root via __file__.
"""
import os
import tempfile
from pathlib import Path

from bulk_downloader import staging_claim
from bulk_downloader.runner import SiteRunner
from bulk_downloader.constants import _HTTPDownloadFailed, _DownloadTruncated

BD_GATE_SCOPE = "module"


def _claim(final: Path, page_url: str):
    identity = staging_claim.job_identity(page_url)
    tmp = staging_claim.claim(final, identity)
    owner = staging_claim.owner_path_for(tmp)
    assert tmp == staging_claim.staging_path_for(final)
    assert owner.is_file(), "precondition: production staging claim was not created"
    return tmp, owner, identity


def test_integrity_size_ok_decision():
    OK = SiteRunner._integrity_size_ok
    # full transfer -> ok
    assert OK(1000, 1000) is True
    assert OK(1001, 1000) is True            # over-read (rare) is not truncation
    # truncated transfer -> not ok
    assert OK(800, 1000) is False
    assert OK(0, 1000) is False
    # unknown length (chunked / no Content-Length) -> fail-open (cannot judge)
    assert OK(500, 0) is True
    assert OK(0, 0) is True
    assert OK(500, -1) is True


def test_truncated_exception_is_distinct_from_http_failed():
    # Must NOT subclass _HTTPDownloadFailed, or the mirror-retry / Playwright
    # fallback handlers in _do_download would swallow it instead of routing
    # the URL to needs_review.
    assert issubclass(_DownloadTruncated, Exception)
    assert not issubclass(_DownloadTruncated, _HTTPDownloadFailed)
    assert not issubclass(_HTTPDownloadFailed, _DownloadTruncated)


def test_clean_run_promotes_part_to_final_atomically():
    d = Path(tempfile.mkdtemp())
    final = d / "video.mp4"
    tmp, owner, identity = _claim(final, "https://example.test/clean")
    meta = tmp.with_suffix(tmp.suffix + ".meta")
    tmp.write_bytes(b"x" * 1000)
    meta.write_text("{}")
    # received == advertised -> promote
    out = SiteRunner._promote_or_abort(tmp, final, downloaded=1000, total=1000,
                                       meta_path=meta, identity=identity)
    assert out == final
    assert final.exists()
    assert final.stat().st_size == 1000
    assert not tmp.exists()          # .part consumed by the atomic replace
    assert not meta.exists()         # sidecar cleaned up on success
    assert not owner.exists()        # matching production claim released


def test_clean_run_with_unknown_length_still_promotes():
    d = Path(tempfile.mkdtemp())
    final = d / "video.mp4"
    tmp, owner, identity = _claim(final, "https://example.test/unknown-length")
    tmp.write_bytes(b"y" * 512)
    # total<=0 means the server gave no length; we promote (fail-open)
    out = SiteRunner._promote_or_abort(
        tmp, final, downloaded=512, total=0, identity=identity)
    assert out == final
    assert final.exists() and final.stat().st_size == 512
    assert not tmp.exists()
    assert not owner.exists()


def test_truncated_run_aborts_no_final_and_part_removed():
    d = Path(tempfile.mkdtemp())
    final = d / "video.mp4"
    tmp, owner, identity = _claim(final, "https://example.test/truncated")
    meta = tmp.with_suffix(tmp.suffix + ".meta")
    tmp.write_bytes(b"z" * 800)      # only 800 of 1000 advertised bytes
    meta.write_text("{}")
    raised = False
    try:
        SiteRunner._promote_or_abort(tmp, final, downloaded=800, total=1000,
                                     meta_path=meta, identity=identity)
    except _DownloadTruncated as e:
        raised = True
        # the message names the shortfall so logs/needs_review are explainable
        assert "800" in str(e) and "1000" in str(e)
    assert raised, "_promote_or_abort must raise on a truncated transfer"
    assert not final.exists(), "a truncated transfer must leave NO final file"
    assert not tmp.exists(), "the truncated .part must be removed"
    assert not meta.exists(), "the meta sidecar must be removed on abort"
    assert not owner.exists(), "the matching production claim must be released"


def test_truncated_run_does_not_clobber_existing_final():
    # A pre-existing good final (e.g. from a prior success) must survive a
    # later truncated attempt — abort must not touch the destination.
    d = Path(tempfile.mkdtemp())
    final = d / "video.mp4"
    final.write_bytes(b"GOODFILE")
    tmp, owner, identity = _claim(final, "https://example.test/existing-final")
    tmp.write_bytes(b"q" * 100)
    try:
        SiteRunner._promote_or_abort(
            tmp, final, downloaded=100, total=1000, identity=identity)
    except _DownloadTruncated:
        pass
    assert final.read_bytes() == b"GOODFILE"
    assert not tmp.exists()
    assert not owner.exists()
