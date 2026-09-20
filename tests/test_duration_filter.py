"""Acceptance test for Row 908: PAYLOAD-DURATION-AND-SIZE-VERIFICATION.

Register Row 908:
  SCOPE: implement duration and size qualification in bulk_downloader/detect.py
  rejecting items with duration < 180s or size < 50MB when full-length content
  is requested; 0 site logins touched (Rule 21).
  ACCEPTANCE: tests/test_duration_filter.py verifying (1) filtering of short
  preview clips, (2) admission of complete candidate payloads, (3) structured
  diagnostic logging.
"""
from __future__ import annotations

import importlib

detect = importlib.import_module("bulk_downloader.detect")

BD_GATE_SCOPE = "module"


def _qualify(*args, **kwargs):
    fn = getattr(detect, "qualifies_as_full_length", None)
    if fn is not None:
        return fn(*args, **kwargs)
    # On unmodified base, duration qualification is absent (fails open),
    # returning ok=True so assertions asserting rejection fail behaviorally with AssertionError.
    return {"ok": True, "duration_s": 0, "size_bytes": 0, "reason": None}


def _parse_duration(text):
    fn = getattr(detect, "parse_duration_seconds", None)
    if fn is not None:
        return fn(text)
    return -1


# ---------------------------------------------------------------------------
# Duration Parsing Helpers
# ---------------------------------------------------------------------------

def test_parse_duration_seconds_reads_mm_ss():
    assert _parse_duration("Trailer (2:30)") == 150
    assert _parse_duration("Clip 0:45") == 45


def test_parse_duration_seconds_reads_h_mm_ss():
    assert _parse_duration("Full movie 1:32:15") == 1 * 3600 + 32 * 60 + 15


def test_parse_duration_seconds_reads_unit_shapes():
    assert _parse_duration("Runtime: 90 min") == 5400
    assert _parse_duration("2h feature") == 7200
    assert _parse_duration("45s clip") == 45
    assert _parse_duration("1h 30m") == 5400


def test_parse_duration_seconds_returns_zero_when_absent():
    assert _parse_duration("no timing info here") == 0
    assert _parse_duration("") == 0
    assert _parse_duration(None) == 0


# ---------------------------------------------------------------------------
# Acceptance 1: Filtering of short preview clips (< 180s or < 50MB)
# ---------------------------------------------------------------------------

def test_short_duration_preview_clip_is_rejected():
    result = _qualify("Preview clip 1:30 (720p) 200MB")
    assert result["ok"] is False, "duration < 180s must be rejected"
    assert result["duration_s"] == 90
    assert "duration" in str(result["reason"]).lower()


def test_small_size_preview_clip_is_rejected():
    result = _qualify("Sample 25:00 (720p) 12MB")
    assert result["ok"] is False, "size < 50MB must be rejected"
    assert result["size_bytes"] == 12 * 1024 * 1024
    assert "size" in str(result["reason"]).lower()


def test_both_short_and_small_reports_both_reasons():
    result = _qualify("Teaser 0:45 3MB")
    assert result["ok"] is False
    reason = str(result["reason"]).lower()
    assert "duration" in reason and "size" in reason


def test_promo_sample_clip_without_duration_rejected_when_full_length_requested():
    """E3 remedy: unparseable promo/preview titles must NOT fail open."""
    result = _qualify("Promotional sample clip (1080p)")
    assert result["ok"] is False, "promotional clip without verified full length must be rejected"
    assert any(k in str(result["reason"]).lower() for k in ("promo", "preview", "sample"))


def test_container_stream_metadata_rejects_short_duration():
    """Container / stream metadata inspection rejects short streams."""
    meta = {"duration": 90, "size": 150 * 1024 * 1024}
    result = _qualify("Stream candidate", stream_meta=meta)
    assert result["ok"] is False
    assert result["duration_s"] == 90


def test_container_stream_metadata_rejects_undersized_stream():
    """Container / stream metadata inspection rejects undersized streams."""
    meta = {"duration": 1800, "size": 10 * 1024 * 1024}
    result = _qualify("Stream candidate", stream_meta=meta)
    assert result["ok"] is False
    assert result["size_bytes"] == 10 * 1024 * 1024


def test_candidate_admission_filters_short_preview(monkeypatch):
    """Integration with detect._candidate_admission."""
    monkeypatch.setenv("REQUIRE_FULL_LENGTH", "1")
    reason = detect._candidate_admission(None, "Bonus Scene 1:30 (720p) 200MB")
    assert reason == "short_preview", f"expected 'short_preview', got {reason!r}"


# ---------------------------------------------------------------------------
# Acceptance 2: Admission of complete candidate payloads (>= 180s and >= 50MB)
# ---------------------------------------------------------------------------

def test_complete_candidate_payload_is_admitted():
    result = _qualify("Full movie 1:45:00 (1080p) 2.1GB")
    assert result["ok"] is True, "payload >= 180s and >= 50MB must be admitted"
    assert result["duration_s"] == 6300
    assert result["size_bytes"] == int(2.1 * 1073741824)
    assert result["reason"] is None


def test_boundary_180s_and_50mb_is_admitted():
    """Boundary test: exactly 180s and 50MB qualify (strict < gate)."""
    result = _qualify("Feature 3:00 50MB")
    assert result["ok"] is True
    assert result["duration_s"] == 180
    assert result["size_bytes"] == 50 * 1024 * 1024


def test_container_stream_metadata_admits_complete_stream():
    meta = {"duration": 7200, "size": 1024 * 1024 * 1024}
    result = _qualify("Complete stream", stream_meta=meta)
    assert result["ok"] is True
    assert result["duration_s"] == 7200
    assert result["size_bytes"] == 1024 * 1024 * 1024


def test_full_length_not_requested_skips_filter():
    result = _qualify("Trailer 0:30 5MB", full_length_requested=False)
    assert result["ok"] is True
    assert result["duration_s"] == 30


def test_unmeasurable_non_promo_does_not_spuriously_reject():
    result = _qualify("Watch Now (1080p)")
    assert result["ok"] is True
    assert result["duration_s"] == 0
    assert result["size_bytes"] == 0


def test_candidate_admission_admits_complete_payload(monkeypatch):
    """Integration with detect._candidate_admission."""
    monkeypatch.setenv("REQUIRE_FULL_LENGTH", "1")
    reason = detect._candidate_admission(None, "Full movie 1:45:00 (1080p) 2.1GB")
    assert reason is None


# ---------------------------------------------------------------------------
# Acceptance 3: Structured diagnostic logging
# ---------------------------------------------------------------------------

class _FakeRunner:
    def __init__(self):
        self.events = []
        self.config = {"require_full_length": True}

    def log_event(self, kind, message, extra=None):
        self.events.append((kind, message, extra))


def test_rejection_emits_structured_diagnostic_log():
    runner = _FakeRunner()
    result = _qualify("Preview 1:00 5MB", runner=runner)
    assert result["ok"] is False
    assert len(runner.events) == 1, "must emit exactly 1 log event on rejection"
    kind, message, extra = runner.events[0]
    assert kind == "duration_size_qualify_reject"
    assert extra["duration_s"] == 60
    assert extra["size_bytes"] == 5 * 1024 * 1024
    assert extra["reason"] is not None
    assert "duration_size_qualify_reject" in message


def test_admission_emits_structured_diagnostic_log():
    runner = _FakeRunner()
    result = _qualify("Full movie 2:00:00 3GB", runner=runner)
    assert result["ok"] is True
    assert len(runner.events) == 1, "must emit exactly 1 log event on admission"
    kind, _message, extra = runner.events[0]
    assert kind == "duration_size_qualify_admit"
    assert extra["reason"] is None


def test_broken_log_event_fails_open_to_stderr(capsys):
    class _BrokenRunner:
        def log_event(self, *a, **kw):
            raise RuntimeError("telemetry broken")

    result = _qualify("Preview 1:00 5MB", runner=_BrokenRunner())
    assert result["ok"] is False
    captured = capsys.readouterr()
    assert "duration_size_qualify_reject" in captured.err


def test_require_full_length_env_controls(monkeypatch):
    """Verifies REQUIRE_FULL_LENGTH env behavior, negative controls, and zero BD_ prefix."""
    import pathlib
    import subprocess
    from bulk_downloader import detect

    # Positive control: REQUIRE_FULL_LENGTH=1 enables full length mode
    monkeypatch.setenv("REQUIRE_FULL_LENGTH", "1")
    assert detect._full_length_mode() is True

    # Negative control 1: REQUIRE_FULL_LENGTH=0 disables full length mode
    monkeypatch.setenv("REQUIRE_FULL_LENGTH", "0")
    assert detect._full_length_mode() is False

    # Negative control 2: REQUIRE_FULL_LENGTH unset disables full length mode
    monkeypatch.delenv("REQUIRE_FULL_LENGTH", raising=False)
    assert detect._full_length_mode() is False

    # Negative control 3: old name is NOT honored (FG-ENV-TRANCHE-BD-LITERAL compliance)
    old_key = "BD_" + "REQUIRE_FULL_LENGTH"
    monkeypatch.setenv(old_key, "1")
    assert detect._full_length_mode() is False

    # Exact count assertion: 0 occurrences of old key in shipped production code (bulk_downloader/)
    repo = pathlib.Path(__file__).resolve().parent.parent
    cmd = ["git", "-C", str(repo), "grep", "-n", old_key, "bulk_downloader/"]
    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert res.returncode != 0, f"Found unexpected {old_key} occurrences in production code:\n{res.stdout}"
    assert len(res.stdout.strip()) == 0, f"Exact count: 0 occurrences of {old_key} allowed in bulk_downloader/"

