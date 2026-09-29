"""stalegate-stream-verifier-lowfps (harness-work/P2-ROW-stalegate-stream-verifier-lowfps.md; STALEGATE-41a70358 triage):
test_stream_verifier's low-fps MKV cut at 50% was judged truncated by the stream verifier, then the Row 868 container
repair remuxed it. The remux declares the shorter span, so it opened, had no expected duration to miss, and replaced the
download: (True, False, '') -- half a file kept as complete. A stream cut short of its own declared duration is missing
bytes; no remux recovers them, so a truncated verdict now skips repair and quarantines. Other stream failures (gaps,
discontinuities) still reach repair. The verifier is stubbed; no ffmpeg, no network.
"""

from __future__ import annotations

import threading

BD_GATE_SCOPE = "module"


def _runner(monkeypatch, sv, repaired):
    from bulk_downloader import container_repair, runner_integrity, stream_verifier

    calls = []

    def fake_repair(path, *a, **k):
        calls.append(path)
        return repaired(path)

    monkeypatch.setattr(runner_integrity, "db_log", lambda *a, **k: None)
    monkeypatch.setattr(runner_integrity, "verify_media_integrity", lambda p: (True, ""))
    monkeypatch.setattr(stream_verifier, "verify_stream", lambda *a, **k: sv)
    monkeypatch.setattr(container_repair, "repair", fake_repair)

    class Runner(runner_integrity.IntegrityMixin):
        config = {"retry_on_corruption": False}
        site_id = "t"
        _lock = threading.Lock()
        jobs = {"u": {}}

        def _update_job(self, *a, **k):
            pass

        def log_event(self, *a, **k):
            pass

    return Runner(), calls


def _remux(path):
    from bulk_downloader.container_repair import RepairResult

    out = path.with_name(path.stem + ".repaired" + path.suffix)
    out.write_bytes(b"remux")
    return RepairResult(recovered=True, output_path=out, reason="")


def test_a_truncated_stream_is_quarantined_not_repaired(monkeypatch, tmp_path):
    """THE ROW: a cut-short stream never reaches the remux that would declare it whole."""
    from bulk_downloader.stream_verifier import StreamVerificationResult

    half = tmp_path / "half.mkv"
    half.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    reason = "truncated media stream: stream 0 covers 9.00s of 20.00s declared"
    sv = StreamVerificationResult(valid=False, errors=[reason], container_duration=20.0, truncated=True)
    r, calls = _runner(monkeypatch, sv, _remux)
    ok, retry, got = r._verify_integrity_or_quarantine("u", half, half.name, 1)
    assert (ok, retry) == (False, False), got
    assert calls == [], f"a truncated stream was sent to container repair: {calls}"
    assert (tmp_path / "_failed" / half.name).exists() and "truncated media stream" in got, got


def test_a_gappy_stream_still_reaches_container_repair(monkeypatch, tmp_path):
    """Negative control: a failure that is not truncation still gets the Row 868 remux."""
    from bulk_downloader import runner_integrity
    from bulk_downloader.stream_verifier import StreamVerificationResult

    f = tmp_path / "gappy.mkv"
    f.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    sv = StreamVerificationResult(valid=False, errors=["packet gap"], container_duration=20.0, truncated=False)
    r, calls = _runner(monkeypatch, sv, _remux)
    monkeypatch.setattr(runner_integrity.IntegrityMixin, "_verify_payload", lambda self, u, p, why: (True, why))
    ok, retry, got = r._verify_integrity_or_quarantine("u", f, f.name, 1)
    assert calls == [f], calls
    assert (ok, retry) == (True, False) and f.read_bytes() == b"remux", got
