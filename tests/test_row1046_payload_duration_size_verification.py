"""Row 1046: Payload Duration & Size Verification.

Ensures downloaded media and payload files match expected byte sizes and media
durations within configurable tolerances, detecting truncation, corrupt streams,
overflows, and incomplete chunk assemblies.
"""
from __future__ import annotations

from pathlib import Path
import tempfile
import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import payload_verifier
except ImportError:
    payload_verifier = None


def test_positive_control_existing_integrity_baseline():
    """Verify test harness integrity and positive baseline probe in bulk_downloader.integrity."""
    from bulk_downloader.integrity import verify_media_integrity
    assert callable(verify_media_integrity), "verify_media_integrity missing on base"


def test_payload_verifier_capability_implemented():
    """Verify bulk_downloader.payload_verifier module and core symbols exist."""
    assert payload_verifier is not None, (
        "Row 1046 capability missing: Payload Duration & Size Verification "
        "not implemented in bulk_downloader.payload_verifier"
    )
    from bulk_downloader.payload_verifier import (
        PayloadVerifier,
        PayloadVerificationStatus,
        PayloadVerificationResult,
        verify_payload_duration_and_size,
    )
    assert issubclass(PayloadVerificationStatus, object)
    assert issubclass(PayloadVerificationResult, object)
    assert callable(verify_payload_duration_and_size)
    pv = PayloadVerifier()
    assert hasattr(pv, "verify_size")
    assert hasattr(pv, "verify_duration")
    assert hasattr(pv, "verify_payload")


def test_payload_size_verification_exact_and_tolerance():
    """Verify size verification accurately validates expected byte counts and detects truncation."""
    assert payload_verifier is not None, "capability missing"
    from bulk_downloader.payload_verifier import PayloadVerifier, PayloadVerificationStatus

    pv = PayloadVerifier(size_tolerance_bytes=10)

    # In-memory byte array matching expected size
    sample_data = b"X" * 1024
    res_exact = pv.verify_size(sample_data, expected_bytes=1024)
    assert res_exact.ok is True
    assert res_exact.status == PayloadVerificationStatus.VERIFIED
    assert res_exact.actual_size_bytes == 1024
    assert res_exact.expected_size_bytes == 1024
    assert res_exact.size_delta_bytes == 0

    # Within allowable tolerance delta (1024 vs 1030)
    res_tol = pv.verify_size(sample_data, expected_bytes=1030)
    assert res_tol.ok is True
    assert res_tol.status == PayloadVerificationStatus.VERIFIED
    assert res_tol.size_delta_bytes == -6

    # Truncated payload: expected 2048, got 1024
    res_trunc = pv.verify_size(sample_data, expected_bytes=2048)
    assert res_trunc.ok is False
    assert res_trunc.status == PayloadVerificationStatus.SIZE_TRUNCATED
    assert res_trunc.size_delta_bytes == -1024

    # Empty payload
    res_empty = pv.verify_size(b"", expected_bytes=500)
    assert res_empty.ok is False
    assert res_empty.status == PayloadVerificationStatus.PAYLOAD_EMPTY


def test_payload_duration_verification_tolerance():
    """Verify duration verification compares media durations against expected bounds."""
    assert payload_verifier is not None, "capability missing"
    from bulk_downloader.payload_verifier import PayloadVerifier, PayloadVerificationStatus

    pv = PayloadVerifier(duration_tolerance_seconds=1.5, duration_tolerance_pct=0.05)

    # Exact duration match
    res_match = pv.evaluate_duration(actual_duration=120.0, expected_duration=120.0)
    assert res_match.ok is True
    assert res_match.status == PayloadVerificationStatus.VERIFIED
    assert res_match.actual_duration_seconds == 120.0
    assert res_match.expected_duration_seconds == 120.0

    # Within absolute tolerance (120.8 vs 120.0)
    res_near = pv.evaluate_duration(actual_duration=120.8, expected_duration=120.0)
    assert res_near.ok is True
    assert res_near.status == PayloadVerificationStatus.VERIFIED

    # Truncated media stream (60s vs 120s expected)
    res_trunc = pv.evaluate_duration(actual_duration=60.0, expected_duration=120.0)
    assert res_trunc.ok is False
    assert res_trunc.status == PayloadVerificationStatus.DURATION_TRUNCATED
    assert res_trunc.duration_delta_seconds == -60.0


def test_file_payload_verification_from_disk():
    """Verify disk file size and integrity verification."""
    assert payload_verifier is not None, "capability missing"
    from bulk_downloader.payload_verifier import PayloadVerifier, PayloadVerificationStatus

    pv = PayloadVerifier()

    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tf:
        tf.write(b"SAMPLE-PAYLOAD-DATA-FOR-TESTING" * 100)
        tf_path = Path(tf.name)

    try:
        expected_size = tf_path.stat().st_size
        res = pv.verify_payload(tf_path, expected_bytes=expected_size)
        assert res.ok is True
        assert res.status == PayloadVerificationStatus.VERIFIED
        assert res.actual_size_bytes == expected_size
        assert "file_path" in res.metadata

        # File does not exist
        res_missing = pv.verify_payload(Path("/nonexistent/payload.mp4"))
        assert res_missing.ok is False
        assert res_missing.status == PayloadVerificationStatus.FILE_NOT_FOUND
    finally:
        if tf_path.exists():
            tf_path.unlink()


def test_unified_functional_entrypoint():
    """Verify verify_payload_duration_and_size functional convenience interface."""
    assert payload_verifier is not None, "capability missing"
    from bulk_downloader.payload_verifier import (
        verify_payload_duration_and_size,
        PayloadVerificationStatus,
    )

    with tempfile.NamedTemporaryFile(suffix=".dat", delete=False) as tf:
        tf.write(b"DATA" * 50)
        tf_path = Path(tf.name)

    try:
        res = verify_payload_duration_and_size(tf_path, expected_bytes=200)
        assert res.ok is True
        assert res.status == PayloadVerificationStatus.VERIFIED
    finally:
        if tf_path.exists():
            tf_path.unlink()


def test_integrity_caller_integration():
    """Verify bulk_downloader.integrity integrates payload dimension and size verification."""
    assert payload_verifier is not None, "capability missing"
    from bulk_downloader import integrity

    assert hasattr(integrity, "verify_payload_size_and_duration"), (
        "integrity module missing verify_payload_size_and_duration integration"
    )

    with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tf:
        tf.write(b"hello world")
        tf_path = tf.name

    try:
        res = integrity.verify_payload_size_and_duration(tf_path, expected_bytes=11)
        assert res.ok is True
    finally:
        p = Path(tf_path)
        if p.exists():
            p.unlink()


# ── Bounce ORDERS-2208 (REFUTEs N6-A E1-E4, N1-A R1) ─────────────────────────────

import struct
import threading


def _mvhd_mp4(path, version, timescale, duration):
    """Minimal ftyp + moov/mvhd file; mvhd fields laid out per ISO/IEC 14496-12 8.2.2."""
    if version == 1:
        body = struct.pack(">B3xQQIQ", 1, 0, 0, timescale, duration)
    else:
        body = struct.pack(">B3xIIII", 0, 0, 0, timescale, duration)
    body += b"\x00" * 80  # rate, volume, matrix, next_track_id: unread
    mvhd = struct.pack(">I4s", 8 + len(body), b"mvhd") + body
    moov = struct.pack(">I4s", 8 + len(mvhd), b"moov") + mvhd
    ftyp = struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0)
    Path(path).write_bytes(ftyp + moov)
    return Path(path)


def test_mvhd_parser_reads_both_versions_and_honours_timescale(tmp_path):
    """E3 (+N1-A M3): v1 timescale sits at offset 20 and a 64-bit duration at 24..32."""
    from bulk_downloader.payload_verifier import _probe_mp4_duration

    assert _probe_mp4_duration(_mvhd_mp4(tmp_path / "v0.mp4", 0, 1000, 5000)) == 5.0
    assert _probe_mp4_duration(_mvhd_mp4(tmp_path / "v1.mp4", 1, 1000, 5000)) == 5.0
    # a duration that only fits the 64-bit field, at a non-unit timescale
    assert _probe_mp4_duration(_mvhd_mp4(tmp_path / "v1big.mp4", 1, 90000, 2**33)) == 2**33 / 90000


def test_unmeasurable_duration_is_unchecked_never_verified(tmp_path, monkeypatch):
    """E1 (O1224 three-state): an expected duration that cannot be measured is UNCHECKED, ok=False."""
    from bulk_downloader import payload_verifier as pv_mod
    from bulk_downloader.payload_verifier import PayloadVerifier, PayloadVerificationStatus as S

    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: None)  # no ffprobe on this host
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x00" * 1000)

    res = PayloadVerifier().verify_payload(blob, expected_bytes=1000, expected_duration=100)
    assert (res.ok, res.status) == (False, S.UNCHECKED), res
    assert res.actual_duration_seconds is None and res.actual_size_bytes == 1000
    assert "duration" in res.error and res.error

    unmeasured = PayloadVerifier().evaluate_duration(None, 100.0)
    assert (unmeasured.ok, unmeasured.status) == (False, S.UNCHECKED)
    # no duration claim at all is not a check that failed to run
    assert PayloadVerifier().evaluate_duration(None, None).status == S.VERIFIED
    assert PayloadVerifier().evaluate_duration(12.0, None).status == S.VERIFIED


def test_verify_payload_duration_path_is_consulted(tmp_path):
    """E4: verify_payload must act on the measured duration (mutant 'no_duration_check' escaped)."""
    from bulk_downloader.payload_verifier import PayloadVerifier, PayloadVerificationStatus as S

    for version in (0, 1):
        f = _mvhd_mp4(tmp_path / f"clip{version}.mp4", version, 1000, 5000)
        size = f.stat().st_size
        good = PayloadVerifier().verify_payload(f, expected_bytes=size, expected_duration=5.0)
        assert (good.ok, good.status, good.actual_duration_seconds) == (True, S.VERIFIED, 5.0), good
        cut = PayloadVerifier().verify_payload(f, expected_bytes=size, expected_duration=6.5)
        assert (cut.ok, cut.status) == (False, S.DURATION_TRUNCATED), cut
        assert cut.duration_delta_seconds == -1.5 and cut.actual_size_bytes == size


def test_integrity_wrapper_passes_tolerances_through(tmp_path):
    """E4: the integrity wrapper's kwargs reach the verifier (mutant 'wrapper_drops_kwargs' escaped)."""
    from bulk_downloader import integrity
    from bulk_downloader.payload_verifier import PayloadVerificationStatus as S

    f = _mvhd_mp4(tmp_path / "w.mp4", 0, 1000, 5000)
    size = f.stat().st_size
    strict = integrity.verify_payload_size_and_duration(f, expected_bytes=size + 20)
    loose = integrity.verify_payload_size_and_duration(f, expected_bytes=size + 20, size_tolerance_bytes=20)
    assert (strict.status, loose.status) == (S.SIZE_TRUNCATED, S.VERIFIED)
    dflt = integrity.verify_payload_size_and_duration(f, expected_duration=5.9)
    tight = integrity.verify_payload_size_and_duration(f, expected_duration=5.9, duration_tolerance_seconds=0.1)
    assert (dflt.status, tight.status) == (S.VERIFIED, S.DURATION_TRUNCATED)


def _runner(monkeypatch, jobs):
    from bulk_downloader import runner_integrity

    monkeypatch.setattr(runner_integrity, "db_log", lambda *a, **k: None)
    # isolate the payload check: the container opens and the stream verifier passes it
    monkeypatch.setattr(runner_integrity, "verify_media_integrity", lambda p: (True, ""))
    monkeypatch.setattr(runner_integrity.stream_verifier, "verify_stream",
                        lambda *a, **k: runner_integrity.stream_verifier.StreamVerificationResult(valid=True))

    class Runner(runner_integrity.IntegrityMixin):
        config = {"retry_on_corruption": False}
        site_id = "t"
        _lock = threading.Lock()

        def _update_job(self, *a, **k): pass
        def log_event(self, *a, **k): pass

    r = Runner()
    r.jobs = jobs
    return r


def test_completion_path_quarantines_duration_truncated_payload(tmp_path, monkeypatch):
    """R1/E2 (O1223): the post-download verify site consults the payload verifier."""
    from bulk_downloader import container_repair

    monkeypatch.setattr(container_repair, "repair",
                        lambda p: container_repair.RepairResult(recovered=False, output_path=None, reason="n/a"))
    f = _mvhd_mp4(tmp_path / "short.mp4", 1, 1000, 5000)
    size = f.stat().st_size
    ok, retry, reason = _runner(monkeypatch, {"u": {"duration_sec": 6.5}})._verify_integrity_or_quarantine(
        "u", f, f.name, size)
    assert (ok, retry) == (False, False), reason
    assert (tmp_path / "_failed" / f.name).exists() and not f.exists()
    assert "duration truncated" in reason, reason

    # negative control: the same shape at the right length passes and stays put
    g = _mvhd_mp4(tmp_path / "whole.mp4", 1, 1000, 5000)
    ok, retry, reason = _runner(monkeypatch, {"u": {"duration_sec": 5.0}})._verify_integrity_or_quarantine(
        "u", g, g.name, g.stat().st_size)
    assert (ok, retry, reason) == (True, False, ""), reason
    assert g.exists()

    # no expected duration on the job: nothing is claimed, nothing is quarantined
    h = _mvhd_mp4(tmp_path / "any.mp4", 0, 1000, 5000)
    ok, retry, reason = _runner(monkeypatch, {"u": {}})._verify_integrity_or_quarantine("u", h, h.name, 1)
    assert (ok, retry, reason) == (True, False, "") and h.exists(), reason


def test_container_repair_cannot_launder_a_truncated_payload(tmp_path, monkeypatch):
    """A remux that opens is not a remux of the missing minutes: the payload check re-runs on it."""
    from bulk_downloader import container_repair

    f = _mvhd_mp4(tmp_path / "short.mp4", 0, 1000, 5000)
    remux = _mvhd_mp4(tmp_path / "short.remux.mp4", 0, 1000, 5000)
    monkeypatch.setattr(container_repair, "repair",
                        lambda p: container_repair.RepairResult(recovered=True, output_path=remux, reason="remuxed"))
    ok, retry, reason = _runner(monkeypatch, {"u": {"duration_sec": 6.5}})._verify_integrity_or_quarantine(
        "u", f, f.name, f.stat().st_size)
    assert ok is False and "duration truncated" in reason, reason
    assert (tmp_path / "_failed" / f.name).exists()


def test_completion_path_unmeasurable_duration_fails_open_with_reason(tmp_path, monkeypatch):
    """UNCHECKED proceeds (like verify_media_integrity without ffprobe) but never earns the checkmark."""
    from bulk_downloader import payload_verifier as pv_mod

    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: None)
    blob = tmp_path / "clip.bin"
    blob.write_bytes(b"\x00" * 1000)
    ok, retry, reason = _runner(monkeypatch, {"u": {"duration_sec": 100}})._verify_integrity_or_quarantine(
        "u", blob, blob.name, 1000)
    assert (ok, retry) == (True, False) and blob.exists()
    assert "payload duration unverified" in reason, reason
    # T79-S-A: the operator-visible reason names the probe step that failed
    assert reason == ("payload duration unverified: Media duration could not be measured "
                      "(expected 100.00s): ffprobe not available on system PATH"), reason

    # zips and images carry no media duration: not checked, not reported
    for name in ("a.zip", "p.jpg"):
        other = tmp_path / name
        other.write_bytes(b"x")
        assert _runner(monkeypatch, {"u": {"duration_sec": 100}})._verify_integrity_or_quarantine(
            "u", other, name, 1) == (True, False, "")


# ── T79 drop (bd-integrator-S-A 09:02Z, ratchet defect_DP_total +3): DP-03@129, DP-13@99,
#    DP-13@132 in payload_verifier. A duration probe that cannot measure names the failed step
#    (and the exception's own words) in the UNCHECKED reason instead of swallowing it. ─────────

def test_unmeasured_duration_names_the_failed_ffprobe_step(tmp_path, monkeypatch):
    """T79-S-A DP-13@132 + DP-03@129: every way the ffprobe leg fails is named in the UNCHECKED
    reason -- timed out, un-executable, refused, unparseable, non-finite, non-positive, missing."""
    import subprocess

    from bulk_downloader import payload_verifier as pv_mod
    from bulk_downloader.payload_verifier import PayloadVerificationStatus as S
    from bulk_downloader.payload_verifier import PayloadVerifier

    blob = tmp_path / "clip.bin"
    blob.write_bytes(b"\x00" * 1000)
    pre = "Media duration could not be measured (expected 100.00s): "
    calls = []

    def verify(stdout="", rc=0, stderr="", exc=None):
        def fake_run(cmd, **kw):
            calls.append(cmd)
            if exc is not None:
                raise exc
            return subprocess.CompletedProcess(cmd, rc, stdout, stderr)
        monkeypatch.setattr(pv_mod.subprocess, "run", fake_run)
        return PayloadVerifier().verify_payload(blob, expected_duration=100)

    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: "/opt/ff/ffprobe")
    # precondition: the faked ffprobe is the one consulted -- a reading passes or truncates as before
    assert verify(stdout="100.0\n").status == S.VERIFIED
    short = verify(stdout="12.5\n")
    assert (short.ok, short.status, short.actual_duration_seconds, short.error) == (
        False, S.DURATION_TRUNCATED, 12.5,
        "Media stream duration truncated: expected 100.00s, got 12.50s (delta: -87.50s)"), short
    assert len(calls) == 2 and calls[-1][0] == "/opt/ff/ffprobe" and calls[-1][-1] == str(blob)

    for probe, why in (
        ({"exc": subprocess.TimeoutExpired(["ffprobe"], 10)}, "ffprobe duration probe timed out after 10s"),
        ({"exc": PermissionError(13, "Permission denied")},
         "ffprobe invocation failed (PermissionError: [Errno 13] Permission denied)"),
        ({"rc": 1, "stderr": "clip.bin: Invalid data found when processing input\n"},
         "ffprobe reported error (exit 1): clip.bin: Invalid data found when processing input"),
        ({"rc": 1}, "ffprobe reported error (exit 1): no detail"),
        ({"stdout": "N/A\n"}, "unparseable ffprobe duration: 'N/A'"),
        ({"stdout": ""}, "unparseable ffprobe duration: ''"),
        ({"stdout": "inf\n"}, "ffprobe duration is not finite: 'inf'"),  # DP-03: never a measured inf
        ({"stdout": "nan\n"}, "ffprobe duration is not finite: 'nan'"),
        ({"stdout": "0.000000\n"}, "ffprobe duration is not positive: '0.000000'"),
    ):
        res = verify(**probe)
        assert (res.ok, res.status, res.actual_duration_seconds) == (False, S.UNCHECKED, None), (probe, res)
        assert res.error == pre + why, (probe, res.error)
    assert len(calls) == 2 + 9

    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: None)
    missing = verify()
    assert (missing.ok, missing.status, missing.error) == (
        False, S.UNCHECKED, pre + "ffprobe not available on system PATH"), missing
    assert len(calls) == 2 + 9  # no ffprobe, no call


def test_unreadable_mp4_header_is_named_not_swallowed(tmp_path, monkeypatch):
    """T79-S-A DP-13@99: an MP4 header the parser cannot read names why (exception type and
    words); so does a readable header that carries no duration, and a path that is no file."""
    import errno

    from bulk_downloader import payload_verifier as pv_mod
    from bulk_downloader.payload_verifier import PayloadVerificationStatus as S
    from bulk_downloader.payload_verifier import PayloadVerifier

    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: None)
    pre = "Media duration could not be measured (expected 5.00s): "
    tail = "; ffprobe not available on system PATH"

    def verify(f):
        res = PayloadVerifier().verify_payload(f, expected_duration=5.0)
        assert (res.ok, res.status, res.actual_duration_seconds) == (False, S.UNCHECKED, None), res
        return res.error

    # a 64-bit atom size cut short by EOF: struct.error inside the parser
    cut = tmp_path / "cut.mp4"
    cut.write_bytes(struct.pack(">I4s", 1, b"free") + b"\x00" * 4)
    with pytest.raises(struct.error) as short_read:
        struct.unpack(">Q", b"\x00" * 4)
    assert verify(cut) == f"{pre}mp4 header unreadable (error: {short_read.value}){tail}"

    # the disk read itself fails
    good = _mvhd_mp4(tmp_path / "eio.mp4", 0, 1000, 5000)

    def eio(*a, **k):
        raise OSError(errno.EIO, "Input/output error")

    with monkeypatch.context() as m:
        m.setattr(pv_mod, "open", eio, raising=False)
        assert verify(good) == f"{pre}mp4 header unreadable (OSError: [Errno 5] Input/output error){tail}"
    assert pv_mod._probe_mp4_duration(good) == 5.0  # control: the same file reads once the disk does

    # readable headers that carry no duration
    ftyp_only = tmp_path / "ftyp.mp4"
    ftyp_only.write_bytes(struct.pack(">I4s4sI", 16, b"ftyp", b"isom", 0))
    assert verify(ftyp_only) == f"{pre}mp4 header has no mvhd atom{tail}"
    no_scale = _mvhd_mp4(tmp_path / "ts0.mp4", 0, 0, 5000)
    assert verify(no_scale) == f"{pre}mp4 mvhd atom gives no duration (version 0, 32 bytes read){tail}"
    zero = _mvhd_mp4(tmp_path / "zero.mp4", 1, 1000, 0)
    assert verify(zero) == f"{pre}mp4 mvhd duration is 0{tail}"

    # a directory passes the existence check but is no media file
    d = tmp_path / "dir.mp4"
    d.mkdir()
    (d / "x").write_bytes(b"x")
    assert verify(d) == f"{pre}not a regular file: {d}"


def test_mp4_atom_smaller_than_its_header_ends_the_parse(tmp_path, monkeypatch):
    """SELF-LENS (T79 fix): an atom declaring fewer bytes than its own header never advances the
    reader -- a 64-bit size of 0 re-read the same atom forever (the post-download verify hung on
    a host without ffprobe); a 32-bit size under 8 sought backwards and read to EOF."""
    from bulk_downloader import payload_verifier as pv_mod
    from bulk_downloader.payload_verifier import PayloadVerificationStatus as S
    from bulk_downloader.payload_verifier import PayloadVerifier

    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: None)
    pre = "Media duration could not be measured (expected 5.00s): "
    loop = tmp_path / "loop.mp4"
    loop.write_bytes(struct.pack(">I4sQ", 1, b"free", 0) + b"\x00" * 64)
    done = []
    worker = threading.Thread(
        target=lambda: done.append(PayloadVerifier().verify_payload(loop, expected_duration=5.0)),
        daemon=True)
    worker.start()
    worker.join(10)
    assert done, "mp4 header parse did not return within 10s on an atom declaring a 64-bit size of 0"
    assert (done[0].ok, done[0].status) == (False, S.UNCHECKED), done[0]
    assert done[0].error == (f"{pre}mp4 atom 'free' declares 0 bytes, under its 16-byte header; "
                             "ffprobe not available on system PATH")

    small = tmp_path / "small.mp4"
    small.write_bytes(struct.pack(">I4s", 4, b"mvhd") + b"\x00" * 64)
    res = PayloadVerifier().verify_payload(small, expected_duration=5.0)
    assert (res.ok, res.status, res.error) == (
        False, S.UNCHECKED,
        f"{pre}mp4 atom 'mvhd' declares 4 bytes, under its 8-byte header; ffprobe not available on system PATH"), res


def test_ffprobe_duration_child_reads_eof_not_the_worker_stdin(tmp_path, monkeypatch):
    """RULING-2338 (row1053's stdin census on main counts every bulk_downloader subprocess site):
    the duration probe's ffprobe child gets stdin=DEVNULL and never inherits the worker's stdin."""
    import subprocess

    from bulk_downloader import payload_verifier as pv_mod

    seen = []
    monkeypatch.setattr(pv_mod.shutil, "which", lambda name: "/opt/ff/ffprobe")
    monkeypatch.setattr(pv_mod.subprocess, "run",
                        lambda cmd, **kw: seen.append(kw) or subprocess.CompletedProcess(cmd, 0, "7.0\n", ""))
    blob = tmp_path / "clip.bin"
    blob.write_bytes(b"\x00" * 10)
    assert pv_mod.probe_file_duration(blob) == 7.0
    assert len(seen) == 1 and seen[0].get("stdin") is subprocess.DEVNULL, seen
