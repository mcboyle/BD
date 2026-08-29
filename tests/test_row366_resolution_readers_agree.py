"""Row 366: the AI and detector must publish one resolution-label vocabulary.

The probe battery is entirely local.  It exercises the real regex fast path in
``aiassist.normalize_resolution`` and the real ``detect.res_score/res_label``
pair; no model or network transport is replaced on the positive path.
"""
from __future__ import annotations

from types import SimpleNamespace


BD_GATE_SCOPE = "module"


_READER_CASES = (
    ("release.8K.mkv", 4320, "8K"),
    ("release.6K.mkv", 3160, "6K"),
    ("release.5K.mkv", 2880, "5K"),
    ("release.4K.mkv", 2160, "4K"),
    ("release.2K.mkv", 1440, "1440p"),
    ("release.1200p.mkv", 1200, "1200p"),
    ("release.1080p.mkv", 1080, "1080p"),
    ("release.900p.mkv", 900, "900p"),
    ("release.720p.mkv", 720, "720p"),
    ("release.540p.mkv", 540, "540p"),
    ("release.480p.mkv", 480, "480p"),
    ("release.360p.mkv", 360, "360p"),
    ("release.353p.mkv", 353, "353p (preview)"),
    ("release.240p.mkv", 240, "240p"),
)


def _reader_rows() -> list[dict]:
    from bulk_downloader import aiassist, detect

    rows = []
    for text, expected_score, expected_label in _READER_CASES:
        # Every literal is a known fast-path label, so this call cannot reach
        # the model even on the defective base (proved below by ``via``).
        ai_result = aiassist.normalize_resolution(text)
        detect_score = detect.res_score(text)
        detect_label = detect.res_label(detect_score)
        rows.append(
            {
                "input": text,
                "expected_score": expected_score,
                "expected_label": expected_label,
                "ai_result": ai_result,
                "detect_score": detect_score,
                "detect_label": detect_label,
            }
        )
    return rows


def test_the_real_ai_and_detection_readers_publish_the_same_labels() -> None:
    rows = _reader_rows()
    assert len(_READER_CASES) == 14, "precondition: the canonical label denominator changed"
    assert len(rows) == 14, "precondition: every canonical reader case was exercised"
    assert [row["detect_score"] for row in rows] == [
        4320, 3160, 2880, 2160, 1440, 1200, 1080, 900,
        720, 540, 480, 360, 353, 240,
    ], "precondition: the detector measured every quality label"
    assert all(row["ai_result"].get("via") == "regex" for row in rows), (
        "precondition: every AI-side case used the local deterministic reader"
    )

    mismatches = [
        {
            "input": row["input"],
            "ai": row["ai_result"].get("label"),
            "detection": row["detect_label"],
        }
        for row in rows
        if row["ai_result"].get("label") != row["detect_label"]
    ]
    assert mismatches == [], f"resolution label disagreement: {mismatches!r}"
    assert [row["detect_label"] for row in rows] == [
        row["expected_label"] for row in rows
    ]


def test_the_agreement_gate_measures_its_complete_real_subject() -> None:
    from bulk_downloader.dev_suite.capture_diag import resolution_scoring_test

    result = resolution_scoring_test()
    assert result.get("fixed_probe_count") == 20, (
        "precondition: fixed probe denominator changed"
    )
    assert result["probe_count"] == 20, "precondition: verdict denominator changed"
    assert len(result["probes"]) == 20, "precondition: every fixed probe produced a row"
    assert result["expected_canonical_label_count"] == 14, result
    assert result["canonical_labels_measured"] == [
        "8K", "6K", "5K", "4K", "1440p", "1200p", "1080p",
        "900p", "720p", "540p", "480p", "360p",
        "353p (preview)", "240p",
    ], result
    assert result["canonical_label_count"] == 14, result
    assert result["canonical_label_coverage_complete"] is True, result
    assert result["measured_probe_count"] == 20, result
    assert result["unmeasured_probe_count"] == 0, result
    assert result["label_mismatch_count"] == 0, result
    assert result["label_mismatches"] == [], result
    assert result["verdict"] == "OK", result
    assert result["ok"] is True, result
    assert result["diagnostic"] == (
        "OK: both resolution readers agreed on all 20 measured probes "
        "covering all 14 canonical labels"
    )


def test_an_unmeasurable_requested_probe_is_unknown_never_ok() -> None:
    from bulk_downloader.dev_suite.capture_diag import resolution_scoring_test

    result = resolution_scoring_test(text="opaque-without-quality")
    assert result["ad_hoc"]["input"] == "opaque-without-quality", (
        "precondition: the gate evaluated the exact requested input"
    )
    assert result["ad_hoc"]["both_readers_measured"] is False, (
        "precondition: neither reader could measure the opaque requested input"
    )
    assert result.get("fixed_probe_count") == 20, (
        "precondition: the complete fixed battery ran before the requested probe"
    )
    assert result["probe_count"] == 21, result
    assert result["measured_probe_count"] == 20, result
    assert result["unmeasured_probe_count"] == 1, result
    assert result["label_mismatch_count"] == 0, result
    assert result["verdict"] == "UNKNOWN", result
    assert result["ok"] is False, result
    assert result["diagnostic"] == (
        "UNKNOWN: 1 of 21 resolution probes could not measure both readers: "
        "opaque-without-quality (ai: no measured label); "
        "opaque-without-quality (detection: no measured label)"
    )


def test_model_results_are_re_read_through_the_detector(monkeypatch) -> None:
    from bulk_downloader import aiassist, detect

    resolution_payload = {
        "resolution": "1080p", "label": "1080",
        "width": 1920, "height": 1080, "confidence": 88,
    }
    filename_payload = {
        "title": "Opaque", "resolution": "1440p", "label": "2K",
        "width": 2560, "height": 1440, "confidence": 87,
    }
    assert [resolution_payload["label"], filename_payload["label"]] == [
        "1080", "2K"
    ], "precondition: both synthetic model labels use the old divergent vocabulary"
    assert [
        detect.res_label(detect.res_score(resolution_payload["resolution"])),
        detect.res_label(detect.res_score(filename_payload["resolution"])),
    ] == ["1080p", "1440p"], "precondition: detector labels are independently known"

    calls = []

    def model_result(payload):
        def invoke(*_args, **_kwargs):
            calls.append(payload["resolution"])
            return SimpleNamespace(
                ok=True,
                text=__import__("json").dumps(payload),
                latency_ms=1,
                provider="synthetic",
                error="",
                error_kind="",
            )
        return invoke

    monkeypatch.setitem(aiassist._config, "enabled", True)
    monkeypatch.setattr(aiassist, "_call_model", model_result(resolution_payload))
    resolution = aiassist.normalize_resolution("opaque-row366-resolution")
    filename = aiassist.normalize_filename(
        "opaque-row366-filename", _call=model_result(filename_payload)
    )

    assert calls == ["1080p", "1440p"], (
        "precondition: each synthetic model path ran exactly once"
    )
    assert resolution["via"] == "ai" and filename["via"] == "ai"
    assert [resolution["label"], filename["label"]] == ["1080p", "1440p"]


def test_a_one_label_negative_control_is_refused_distinctly(monkeypatch) -> None:
    from bulk_downloader import aiassist
    from bulk_downloader.dev_suite.capture_diag import resolution_scoring_test

    real_reader = aiassist.normalize_resolution
    calls = []

    def disagree_once(text, *, allow_model=True):
        calls.append(text)
        result = dict(real_reader(text, allow_model=allow_model))
        if text == "1080p":
            result["label"] = "WRONG-LABEL"
        return result

    monkeypatch.setattr(aiassist, "normalize_resolution", disagree_once)
    result = resolution_scoring_test()

    assert len(calls) == 20, "precondition: the gate invoked the mutated AI reader 20 times"
    assert calls.count("1080p") == 1, "precondition: exactly one probe was mutated"
    assert result["measured_probe_count"] == 20, result
    assert result["unmeasured_probe_count"] == 0, result
    assert result["label_mismatch_count"] == 1, result
    assert result["label_mismatches"] == [
        {
            "input": "1080p",
            "ai_label": "WRONG-LABEL",
            "detect_label": "1080p",
        }
    ], result
    assert result["verdict"] == "FAIL", result
    assert result["ok"] is False, result
    assert result["diagnostic"] == (
        "FAIL: resolution label disagreement on 1 of 20 measured probes: "
        "1080p (AI='WRONG-LABEL', detection='1080p')"
    )


def test_an_unavailable_reader_is_unknown_never_ok(monkeypatch) -> None:
    from bulk_downloader import aiassist
    from bulk_downloader.dev_suite.capture_diag import resolution_scoring_test

    real_reader = aiassist.normalize_resolution
    calls = []

    def unavailable_once(text, *, allow_model=True):
        calls.append(text)
        if text == "1080p":
            raise RuntimeError("synthetic reader outage")
        return real_reader(text, allow_model=allow_model)

    monkeypatch.setattr(aiassist, "normalize_resolution", unavailable_once)
    result = resolution_scoring_test()

    assert len(calls) == 20, "precondition: the gate attempted all 20 probes"
    assert calls.count("1080p") == 1, "precondition: exactly one measurement was unavailable"
    assert result["measured_probe_count"] == 19, result
    assert result["unmeasured_probe_count"] == 1, result
    assert result["label_mismatch_count"] == 0, result
    assert result["unmeasured_inputs"] == [
        {"input": "1080p", "reader": "ai", "error": "synthetic reader outage"}
    ], result
    assert result["verdict"] == "UNKNOWN", result
    assert result["ok"] is False, result
    assert result["diagnostic"] == (
        "UNKNOWN: 1 of 20 resolution probes could not measure both readers: "
        "1080p (ai: synthetic reader outage)"
    )


def test_detector_failure_does_not_escape_the_ai_reader(monkeypatch) -> None:
    from bulk_downloader import aiassist, detect

    calls = []

    def unavailable(text):
        calls.append(text)
        raise RuntimeError("synthetic detector outage")

    model_calls = []

    def model_must_not_run(*_args, **_kwargs):
        model_calls.append("called")
        raise AssertionError("model fallback must stay disabled in this control")

    monkeypatch.setattr(detect, "res_score", unavailable)
    monkeypatch.setitem(aiassist._config, "enabled", False)
    leaked = []
    resolution = filename = None
    try:
        resolution = aiassist.normalize_resolution(
            "opaque-row366-detector-outage-resolution", allow_model=False
        )
    except Exception as exc:  # the defective candidate reaches this arm
        leaked.append(("normalize_resolution", type(exc).__name__, str(exc)))
    try:
        filename = aiassist.normalize_filename(
            "opaque-row366-detector-outage-filename", _call=model_must_not_run
        )
    except Exception as exc:  # the defective candidate reaches this arm
        leaked.append(("normalize_filename", type(exc).__name__, str(exc)))

    assert calls == [
        "opaque-row366-detector-outage-resolution",
        "opaque-row366-detector-outage-filename",
    ], "precondition: both public AI readers reached the failed detector exactly once"
    assert model_calls == [], "precondition: no model path obscured the detector failure"
    assert leaked == [], f"AI resolution reader leaked detector failure: {leaked!r}"
    assert resolution["via"] == "no-match" and filename["via"] == "no-match"
    assert [resolution["label"], filename["label"]] == [None, None]
