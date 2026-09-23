"""Row 988: Terminal Visual Artifact & Schema Drift Diff Inspector.

Validates terminal visual artifact parsing, ANSI sequence extraction (including OSC and CSI),
anomaly detection, CRLF handling, extended ANSI resets, formatting-vs-content diffing,
hierarchical schema drift inspection (any depth, NaN-stable), embedded-payload extraction by
the PAYLOAD RULE in the terminal_drift module docstring (every depth-0 JSON object/array in
the frame is a payload, compared pairwise; a broken or cut-off payload makes the frame
'unparsable', never lets a value from inside it pass for the payload) with an explicit schema
status, and the product callers observed THROUGH their output: the plain dashboard
(cli_dashboard.run_once) names render anomalies on stderr, and the keystone
(template_keystone.drift_against_gold) returns the schema drift in its result.

The payload rule is pinned clause by clause, each on a frame that clause alone decides
(test_payload_rule_clause_decides_alone), per frame shape the adversarial verify rounds r0-r2
named (test_payload_rule_verify_history_frame_shape) and as properties over valid payloads: every
single-character deletion, insertion and substitution and every cut at the top or the bottom
compares exactly the whole payload or is 'unparsable' (test_payload_rule_holds_for_*).

RED on baseline: every failing test fails with an AssertionError (the capability guard or a
product-output value), not an unhandled ImportError. Passing on baseline by design: the positive
control (proves the probe can say YES), the clean-frame negative control, and the guard that the
plain dashboard still writes each line before a later line fails (base behaviour kept).
"""
from __future__ import annotations

import json

import pytest

BD_GATE_SCOPE = "module"

try:
    from bulk_downloader import terminal_drift
except ImportError:
    terminal_drift = None


def test_positive_control_probe_can_say_yes():
    """Positive control (Rule 7): proves test runner and probe can say YES on baseline capabilities."""
    from bulk_downloader import cli_dashboard, template_keystone

    # Baseline functions exist and return expected boolean/dict types
    assert hasattr(cli_dashboard, "run_once")
    assert hasattr(template_keystone, "keystone_present")
    assert isinstance(template_keystone.keystone_present(), bool)


def test_terminal_drift_capability_and_callers_implemented():
    """RED assertion 1: capability and product callers must be implemented with semantic AssertionError on base."""
    from bulk_downloader import cli_dashboard, template_keystone

    assert terminal_drift is not None, (
        "Row 988 capability missing: Terminal Visual Artifact & Schema Drift Diff Inspector "
        "not implemented in bulk_downloader.terminal_drift"
    )
    assert hasattr(cli_dashboard, "inspect_dashboard_frame"), (
        "Row 988 caller missing: bulk_downloader.cli_dashboard.inspect_dashboard_frame"
    )
    assert hasattr(template_keystone, "inspect_template_schema_drift"), (
        "Row 988 caller missing: bulk_downloader.template_keystone.inspect_template_schema_drift"
    )


def test_module_exports():
    """Verify bulk_downloader.terminal_drift exports all required classes and functional helpers."""
    assert terminal_drift is not None, "terminal_drift capability missing"

    assert hasattr(terminal_drift, "TerminalVisualArtifact")
    assert hasattr(terminal_drift, "VisualAnomaly")
    assert hasattr(terminal_drift, "VisualArtifactDiff")
    assert hasattr(terminal_drift, "SchemaDriftItem")
    assert hasattr(terminal_drift, "SchemaDriftReport")
    assert hasattr(terminal_drift, "TerminalDriftInspector")
    assert hasattr(terminal_drift, "inspect_visual_artifact")
    assert hasattr(terminal_drift, "diff_visual_artifacts")
    assert hasattr(terminal_drift, "inspect_schema_drift")
    assert hasattr(terminal_drift, "strip_ansi")


def test_terminal_visual_artifact_clean_and_dimensions():
    """Verify ANSI escape stripping, width measurement, and line structure extraction."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_visual_artifact

    raw_frame = "\033[1;32m[SUCCESS]\033[0m Download worker #1 active\n\033[34mSpeed:\033[0m 100 MB/s"
    artifact = inspect_visual_artifact(raw_frame)

    assert artifact.has_ansi is True
    assert artifact.line_count == 2
    assert artifact.clean_text == "[SUCCESS] Download worker #1 active\nSpeed: 100 MB/s"
    assert artifact.lines == ["[SUCCESS] Download worker #1 active", "Speed: 100 MB/s"]
    assert artifact.max_width == len("[SUCCESS] Download worker #1 active")
    assert len(artifact.anomalies) == 0


def test_osc_escape_sequence_handling_defect_d1():
    """Verify OSC escape sequences with BEL or ST terminators are stripped cleanly without false anomalies."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_visual_artifact, strip_ansi

    # OSC with BEL terminator
    s_bel = "\x1b]0;my window title\x07hello world"
    assert strip_ansi(s_bel) == "hello world"
    art_bel = inspect_visual_artifact(s_bel)
    assert art_bel.clean_text == "hello world"
    assert len(art_bel.anomalies) == 0

    # OSC with ST terminator (\x1b\)
    s_st = "\x1b]2;another title\x1b\\active frame"
    assert strip_ansi(s_st) == "active frame"
    art_st = inspect_visual_artifact(s_st)
    assert art_st.clean_text == "active frame"
    assert len(art_st.anomalies) == 0


def test_crlf_capture_handling_defect_d2():
    """Verify CRLF Windows and pty captures are normalized without reporting false control char anomalies."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_visual_artifact

    crlf_text = "line one\r\nline two\r\nline three"
    art = inspect_visual_artifact(crlf_text)
    assert art.line_count == 3
    assert art.lines == ["line one", "line two", "line three"]
    assert len(art.anomalies) == 0
    assert "\r" not in art.clean_text


def test_ansi_extended_resets_and_closures_defect_d3():
    """Verify extended reset forms (\033[m, \033[00m, \033[0;0m, \033[39m, \033[49m) avoid false unclosed alerts."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_visual_artifact

    samples = [
        "\033[31mRed text\033[m",
        "\033[32mGreen text\033[00m",
        "\033[33mYellow text\033[0;0m",
        "\033[34mBlue text\033[39m",
        "\033[45mMagenta bg\033[49m",
    ]
    for sample in samples:
        art = inspect_visual_artifact(sample)
        unclosed = [a for a in art.anomalies if a.anomaly_type == "UNCLOSED_ANSI"]
        assert len(unclosed) == 0, f"False UNCLOSED_ANSI on {repr(sample)}"


def test_terminal_visual_anomaly_detection():
    """Detect genuine unclosed ANSI sequences, raw non-printable controls, and line length overflow."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_visual_artifact

    # Case 1: unclosed ANSI sequence (no reset before line end)
    unclosed = "Header \033[31mUnclosed Red Alert"
    art1 = inspect_visual_artifact(unclosed)
    assert any(a.anomaly_type == "UNCLOSED_ANSI" for a in art1.anomalies)

    # Case 2: illegal / unexpected raw control characters (bell \x07 outside OSC, null \x00)
    control_char = "Progress \x07 Corrupted \x00 Payload"
    art2 = inspect_visual_artifact(control_char)
    assert any(a.anomaly_type == "RAW_CONTROL_CHAR" for a in art2.anomalies)

    # Case 3: line width exceeding max_allowed_width
    long_line = "A" * 120
    art3 = inspect_visual_artifact(long_line, max_allowed_width=80)
    assert any(a.anomaly_type == "LINE_OVERFLOW" for a in art3.anomalies)


def test_diff_visual_artifacts_content_and_formatting():
    """Verify classification of visual diffs into content drift vs formatting-only drift."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import diff_visual_artifacts

    base_frame = "\033[32mOK\033[0m Task completed"
    identical_frame = "\033[32mOK\033[0m Task completed"
    formatting_drift = "\033[34mOK\033[0m Task completed"
    content_drift = "\033[32mFAILED\033[0m Task completed"

    # Identical
    diff_identical = diff_visual_artifacts(base_frame, identical_frame)
    assert diff_identical.is_drifted is False
    assert diff_identical.content_drift is False
    assert diff_identical.formatting_only_drift is False

    # Formatting-only drift (same text, different ANSI styling)
    diff_fmt = diff_visual_artifacts(base_frame, formatting_drift)
    assert diff_fmt.is_drifted is True
    assert diff_fmt.formatting_only_drift is True
    assert diff_fmt.content_drift is False

    # Content drift (text changed)
    diff_content = diff_visual_artifacts(base_frame, content_drift)
    assert diff_content.is_drifted is True
    assert diff_content.content_drift is True
    assert diff_content.formatting_only_drift is False
    assert len(diff_content.diff_lines) > 0


def test_schema_drift_field_addition_removal_type():
    """Verify hierarchical schema drift detection across additions, removals, and type changes."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_schema_drift

    baseline_schema = {
        "status": "active",
        "worker_id": 42,
        "config": {
            "retry_limit": 3,
            "timeout_sec": 30.0,
            "tags": ["prod", "fast"],
        },
        "deprecated_token": "abc123xyz",
    }

    target_schema = {
        "status": "active",
        "worker_id": "42-str",  # TYPE_CHANGED (int -> str)
        "config": {
            "retry_limit": 5,   # VALUE_DRIFT
            "tags": ["prod", "fast"],
            "circuit_breaker": True,  # FIELD_ADDED
            # timeout_sec removed -> FIELD_REMOVED
        },
        # deprecated_token removed -> FIELD_REMOVED
        "telemetry_level": 2,   # FIELD_ADDED
    }

    report = inspect_schema_drift(baseline_schema, target_schema)

    assert report.has_drift is True
    assert report.drift_count > 0

    types_changed = [d.path for d in report.drifts if d.drift_type == "TYPE_CHANGED"]
    assert "worker_id" in types_changed

    fields_added = [d.path for d in report.drifts if d.drift_type == "FIELD_ADDED"]
    assert "config.circuit_breaker" in fields_added
    assert "telemetry_level" in fields_added

    fields_removed = [d.path for d in report.drifts if d.drift_type == "FIELD_REMOVED"]
    assert "config.timeout_sec" in fields_removed
    assert "deprecated_token" in fields_removed

    values_drifted = [d.path for d in report.drifts if d.drift_type == "VALUE_DRIFT"]
    assert "config.retry_limit" in values_drifted


def test_schema_drift_list_handling():
    """Verify schema drift inspection within lists of objects and primitives."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_schema_drift

    base = {
        "endpoints": [
            {"host": "edge-1", "port": 8080},
            {"host": "edge-2", "port": 8080},
        ],
        "allowed_codes": [200, 201],
    }
    target = {
        "endpoints": [
            {"host": "edge-1", "port": 8080, "tls": True},  # FIELD_ADDED in list item
            {"host": "edge-2", "port": "8080-str"},          # TYPE_CHANGED in list item
        ],
        "allowed_codes": [200, 201, 204],                   # ELEMENT_ADDED
    }

    report = inspect_schema_drift(base, target)
    assert report.has_drift is True
    assert any(d.path == "endpoints[0].tls" and d.drift_type == "FIELD_ADDED" for d in report.drifts)
    assert any(d.path == "endpoints[1].port" and d.drift_type == "TYPE_CHANGED" for d in report.drifts)
    assert any(d.path == "allowed_codes[2]" and d.drift_type == "ELEMENT_ADDED" for d in report.drifts)


def test_dashboard_and_keystone_wrappers_delegate_to_inspectors():
    """The two module wrappers delegate to terminal_drift (their callers are tested below)."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.cli_dashboard import inspect_dashboard_frame
    from bulk_downloader.template_keystone import inspect_template_schema_drift

    # 1. Wrapper in bulk_downloader/cli_dashboard.py
    dashboard_output = "\033[32m[BulkDownloader Dashboard]\033[0m\nSites: 5 active"
    artifact = inspect_dashboard_frame(dashboard_output, max_width=80)
    assert artifact.line_count == 2
    assert artifact.clean_text == "[BulkDownloader Dashboard]\nSites: 5 active"
    assert len(artifact.anomalies) == 0

    # 2. Wrapper in bulk_downloader/template_keystone.py
    t_base = {"host": "example.com", "version": 1, "selectors": ["a", "b"]}
    t_cand = {"host": "example.com", "version": 2, "selectors": ["a", "b", "c"]}
    s_rep = inspect_template_schema_drift(t_base, t_cand)
    assert s_rep.has_drift is True
    assert any(d.path == "version" and d.drift_type == "VALUE_DRIFT" for d in s_rep.drifts)
    assert any(d.path == "selectors[2]" and d.drift_type == "ELEMENT_ADDED" for d in s_rep.drifts)


_DASH_SITES = {
    "\x1b[31mvixen": {"jobs": {}},  # site id carrying an unclosed ANSI style
    "bell\x07site": {"jobs": {}},  # site id carrying a raw BEL
    "blacked": {"jobs": {"j1": {"status": "running"},
                         "j2": {"status": "pending"},
                         "j3": {"status": "done"}}},
}
_BLACKED_ROW = "    blacked              run=  1 queue=  1 done=    1 fail=  0 review=  0\n"


def _run_plain_dashboard(monkeypatch, capsys, sites):
    """Drive the real plain-text dashboard (run_once without rich) over a canned API."""
    from bulk_downloader import cli_dashboard

    monkeypatch.setattr(cli_dashboard, "_HAS_RICH", False)
    monkeypatch.setattr(cli_dashboard, "_fetch_status", lambda _api: {"sites": sites})
    monkeypatch.setattr(cli_dashboard, "_fetch_capacity", lambda _api: {})
    cli_dashboard.run_once()
    return capsys.readouterr()


def test_plain_dashboard_names_render_anomalies_on_stderr(monkeypatch, capsys):
    """P4-B#E1 / P3-A#E1 dashboard half: the frame run_once renders is inspected and each
    anomaly is visible on stderr; stdout stays the frame, byte-for-byte."""
    out, err = _run_plain_dashboard(monkeypatch, capsys, _DASH_SITES)

    assert err == (
        "dashboard render anomaly: UNCLOSED_ANSI: "
        "Line 3 opens an ANSI style without a reset before line end\n"
        "dashboard render anomaly: RAW_CONTROL_CHAR: "
        "Raw non-printable control char (hex 0x7) at line 4\n"
    )
    header, rest = out.split("\n", 1)
    assert header.startswith("BulkDownloader status @ ")
    assert rest == (
        "  Sites: 3\n"
        "    \x1b[31mvixen           run=  0 queue=  0 done=    0 fail=  0 review=  0\n"
        "    bell\x07site            run=  0 queue=  0 done=    0 fail=  0 review=  0\n"
        + _BLACKED_ROW
    )


def test_plain_dashboard_clean_frame_writes_nothing_to_stderr(monkeypatch, capsys):
    """Negative control: a frame without anomalies adds zero stderr bytes."""
    out, err = _run_plain_dashboard(monkeypatch, capsys, {"blacked": _DASH_SITES["blacked"]})

    assert err == ""
    assert out.split("\n", 1)[1] == "  Sites: 1\n" + _BLACKED_ROW


def test_plain_dashboard_rows_reach_stdout_before_a_later_line_fails(monkeypatch, capsys):
    """Base behaviour kept: each line is written as it is built, so when a later line fails
    (capacity reports free_gb null -> TypeError on the Disk line) the header and site rows are
    already on stdout, as on base; the inspection runs only on a finished frame."""
    from bulk_downloader import cli_dashboard

    monkeypatch.setattr(cli_dashboard, "_HAS_RICH", False)
    monkeypatch.setattr(cli_dashboard, "_fetch_status",
                        lambda _api: {"sites": {"blacked": _DASH_SITES["blacked"]}})
    monkeypatch.setattr(cli_dashboard, "_fetch_capacity", lambda _api: {"disk": {"free_gb": None}})
    with pytest.raises(TypeError):
        cli_dashboard.run_once()
    out, err = capsys.readouterr()

    assert out.startswith("BulkDownloader status @ "), "no frame line reached stdout"
    assert out.split("\n", 1)[1] == "  Sites: 1\n" + _BLACKED_ROW
    assert err == ""


_GOLD = {"host": "example.com", "version": 1, "selectors": {"title": "h1", "date": ".date"}}
_CANDIDATE = {"host": "example.com", "version": "2", "selectors": {"title": "h1"}}


def _reviewed_with_gold(tmp_path):
    reviewed = tmp_path / "reviewed"
    reviewed.mkdir()
    (reviewed / "example.com.template.json.bak").write_text(json.dumps(_GOLD), encoding="utf-8")
    return reviewed


def test_drift_against_gold_returns_schema_drift_items(tmp_path):
    """P4-B#E1 / P3-A#E1 keystone half: a removed field and a retyped field are visible in
    the drift_against_gold result instead of being computed and dropped."""
    from bulk_downloader import template_keystone

    reviewed = _reviewed_with_gold(tmp_path)
    res = template_keystone.drift_against_gold("example.com", _CANDIDATE, reviewed_dir=reviewed)

    assert res["ok"] is True
    assert res.get("schema_drift") == {
        "count": 2,
        "items": [
            {"path": "selectors.date", "type": "FIELD_REMOVED"},
            {"path": "version", "type": "TYPE_CHANGED"},
        ],
    }
    assert res["baseline"] == str(reviewed / "example.com.template.json.bak")


def test_drift_against_gold_identical_template_reports_zero_schema_drift(tmp_path):
    """Negative control / exact count: an unchanged template yields zero schema items."""
    from bulk_downloader import template_keystone

    reviewed = _reviewed_with_gold(tmp_path)
    res = template_keystone.drift_against_gold(
        "example.com", json.loads(json.dumps(_GOLD)), reviewed_dir=reviewed
    )

    assert res["ok"] is True
    assert res.get("schema_drift") == {"count": 0, "items": []}


def test_drift_against_gold_reports_inspector_failure_in_place(tmp_path, monkeypatch):
    """The advisory inspector can never break the keystone: its failure is reported in
    schema_drift and drift/lines/baseline are exactly what the keystone returns without it."""
    from bulk_downloader import template_keystone

    reviewed = _reviewed_with_gold(tmp_path)
    ok_res = template_keystone.drift_against_gold("example.com", _CANDIDATE, reviewed_dir=reviewed)

    def _boom(_baseline, _candidate):
        raise RuntimeError("boom")

    # raising=False: on base (no wrapper yet) this fails on the value assertion below.
    monkeypatch.setattr(template_keystone, "inspect_template_schema_drift", _boom, raising=False)
    try:
        res = template_keystone.drift_against_gold("example.com", _CANDIDATE, reviewed_dir=reviewed)
    except RuntimeError as exc:
        pytest.fail(f"schema inspector failure escaped drift_against_gold: {exc!r}")

    assert res["ok"] is True
    assert res.get("schema_drift") == {"error": "schema drift inspection failed: RuntimeError: boom"}
    assert (res["drift"], res["lines"], res["baseline"]) == (
        ok_res["drift"], ok_res["lines"], ok_res["baseline"])


def test_inspect_terminal_frame_compares_payload_after_stray_brace():
    """B8-B#B/#C: a stray '{ok}' before the payload must not hide real drift (the greedy
    match captured '{ok} {...}', failed to parse and dropped it)."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    rep = TerminalDriftInspector().inspect_terminal_frame(
        'status {ok} {"a": 9, "b": 2} end',
        baseline_frame_text='status {ok} {"a": 1, "b": 2} end',
    )

    assert rep.schema_drift is not None, "embedded payload after a stray brace was not compared"
    assert [(d.path, d.drift_type, d.baseline_value, d.target_value)
            for d in rep.schema_drift.drifts] == [("a", "VALUE_DRIFT", 1, 9)]
    assert rep.schema_drift.drift_count == 1
    assert rep.schema_status == "compared"
    assert rep.visual_diff is not None and rep.visual_diff.content_drift is True


def test_inspect_terminal_frame_compares_colourised_and_prefixed_payloads():
    """B8-B#B: ANSI colour inside the payload (jq -C style) and a trivial '{}' printed
    before it do not stop the real payload being compared."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    rep = insp.inspect_terminal_frame(
        '{\x1b[34m"a"\x1b[0m: 2}', baseline_frame_text='{\x1b[34m"a"\x1b[0m: 1}'
    )
    assert rep.schema_drift is not None, "colourised payload was not compared"
    assert [(d.path, d.drift_type) for d in rep.schema_drift.drifts] == [("a", "VALUE_DRIFT")]

    # '{}' is a payload of its own (compared to the baseline's '{}'), not a
    # replacement for the one after it: payload[1] is the second payload.
    rep = insp.inspect_terminal_frame(
        'retry kwargs {} payload {"a": 1, "b": 3}',
        baseline_frame_text='retry kwargs {} payload {"a": 1, "b": 2}',
    )
    assert rep.schema_drift is not None, "payload printed after '{}' was not compared"
    assert [(d.path, d.baseline_value, d.target_value)
            for d in rep.schema_drift.drifts] == [("payload[1].b", 2, 3)]


def test_inspect_terminal_frame_schema_status_is_explicit():
    """B8-B#B: unparsable, payload-free and compared-without-drift frames are distinguishable
    (all three used to be the same schema_drift=None or look alike to the caller). A plain
    label ('{not json}': no '"', nothing nested) is text by the payload rule, like '{ok}'."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    cases = [
        ('cfg {"a": 1,}', 'cfg {"a": 1,}', "unparsable"),
        ('x {"bad"}', "plain", "unparsable"),
        ("cfg {not json}", "cfg {not json}", "no_payload"),
        ("x {bad}", "plain", "no_payload"),
        ("plain text", "plain text 2", "no_payload"),
        ('{"a": 1}', "payload gone", "no_payload"),
        ('{"a": 1}', '{"a": 1}', "compared"),
    ]
    got = []
    for baseline, frame, _want in cases:
        rep = insp.inspect_terminal_frame(frame, baseline_frame_text=baseline)
        got.append((getattr(rep, "schema_status", None), rep.schema_drift is None))
    assert got == [(want, want != "compared") for _b, _f, want in cases]

    same = insp.inspect_terminal_frame('{"a": 1}', baseline_frame_text='{"a": 1}')
    assert (same.schema_drift.has_drift, same.schema_drift.drift_count) == (False, 0)

    # No baseline: nothing requested; the inspector's default width limit (120) applies.
    solo = insp.inspect_terminal_frame("A" * 121)
    assert (getattr(solo, "schema_status", None), solo.schema_drift, solo.visual_diff) == (
        "not_requested", None, None)
    assert [(a.line_no, a.anomaly_type) for a in solo.visual_artifact.anomalies] == [
        (1, "LINE_OVERFLOW")]


def _schema_outcome(insp, baseline, frame):
    rep = insp.inspect_terminal_frame(frame, baseline_frame_text=baseline)
    drifts = None if rep.schema_drift is None else [
        (d.path, d.drift_type, d.baseline_value, d.target_value) for d in rep.schema_drift.drifts]
    # getattr: where schema_status does not exist yet this fails on the value, not AttributeError
    return getattr(rep, "schema_status", None), drifts


def _extract(frame):
    """extract_json_payloads(frame), or a marker where the function does not exist yet."""
    extract = getattr(terminal_drift, "extract_json_payloads", None)
    return extract(frame) if extract is not None else "no extract_json_payloads"


def test_inspect_terminal_frame_compares_every_payload_in_the_frame():
    """verify-r1 #1: drift in ANY payload of a frame is reported -- inside a JSON array, in a
    later NDJSON record, in the shorter of two objects -- by comparing the payloads pairwise
    in frame order; a different number of payloads is its own status, never a pairing of
    unrelated objects (the longest-object pick reported these 'compared', 0 drift, or made
    up FIELD_ADDED/FIELD_REMOVED when the pick flipped)."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    cases = [  # (baseline frame, frame)
        ('[{"a": 1}, {"a": 2}]', '[{"a": 1}, {"a": 3}]'),
        ('{"id": 1, "rate": 5}\n{"id": 2, "rate": 7}', '{"id": 1, "rate": 5}\n{"id": 2}'),
        ('{"k": 1} {"long_key_xxxxxxxx": 1}', '{"k": 2} {"long_key_xxxxxxxx": 1}'),
        ('{"a": 1}\n{"bb": 22}', '{"a": 1000}\n{"bb": 2}'),
        ("[1, 2, 3]", "[1, 2, 4]"),
        ('{"a": 1}', '{"a": 1}\n{"b": 2}'),
        ('{"id": 1}\n{"id": 2}', '{"id": 1}\n{"id": 2}'),
    ]
    assert [_schema_outcome(insp, b, f) for b, f in cases] == [
        ("compared", [("[1].a", "VALUE_DRIFT", 2, 3)]),
        ("compared", [("payload[1].rate", "FIELD_REMOVED", 7, None)]),
        ("compared", [("payload[0].k", "VALUE_DRIFT", 1, 2)]),
        ("compared", [("payload[0].a", "VALUE_DRIFT", 1, 1000),
                      ("payload[1].bb", "VALUE_DRIFT", 22, 2)]),
        ("compared", [("[2]", "VALUE_DRIFT", 3, 4)]),
        ("payload_count_changed", None),
        ("compared", []),
    ]


def test_inspect_terminal_frame_broken_payload_is_unparsable_not_compared():
    """verify-r1 #2: a truncated or malformed payload makes the frame 'unparsable'; a value
    nested inside it is never promoted to be the payload (that reported 'compared' with
    made-up drift). Stray braces around a payload are still text, not a broken payload."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    broken = [  # (baseline frame, broken frame)
        ('stats {"a": {"b": 1}, "c": 2, "d": 3}', 'stats {"a": {"b": 1}, "c": 2, "d":'),
        ('{"a": 1, "b": {"c": 2}}', '{"a": 1 "b": {"c": 2}}'),
        ('x = [{"a": 1}, {"a": 2}]', 'x = [{"a": 1}, oops]'),
        ('[{"a": 1}, {"a": 2}]', '[{"a": 1}, {"a":'),
        ('{"id": 1, "rate": 5}\n{"id": 2}', '{"id": 1, "rate": }\n{"id": 2}'),
        ("rows [1, 2, 3]", "rows [1, 2,"),
        ('{ok, {"a": 1}}', '{ok, {"a": 2}}'),
    ]
    assert [_schema_outcome(insp, b, f) for b, f in broken] == [("unparsable", None)] * 7

    stray = [  # stray text and plain labels before or after the payload
        ('{"a": 1} {ok}', '{"a": 9} {ok}'),
        ('[INFO] [1/3] {"a": 1}', '[INFO] [1/3] {"a": 9}'),
        ('status {ok} [12:34:56] {"a": 1}', 'status {ok} [12:34:56] {"a": 9}'),
        ("it's [done] {\"a\": 1} (see log)", "it's [done] {\"a\": 9} (see log)"),
    ]
    assert [_schema_outcome(insp, b, f) for b, f in stray] == [
        ("compared", [("a", "VALUE_DRIFT", 1, 9)])] * len(stray)

    by_rule = [  # were compared as 'stray text'; the payload rule reads them as unparsable
        ('cfg { {"a": 1}', 'cfg { {"a": 9}'),  # the frame ends inside the group 'cfg {' opened
        ('log "{x" {"a": 1}', 'log "{x" {"a": 9}'),  # '"' is text at depth 0: the group
        # opened by '{x' holds a string that never ends
        ("kw {'t': {'x': 1}} out {\"a\": 1}",  # a group with a nested '{' that is not JSON
         "kw {'t': {'x': 1}} out {\"a\": 9}"),
    ]
    assert [_schema_outcome(insp, b, f) for b, f in by_rule] == [
        ("unparsable", None)] * len(by_rule)


def test_inspect_terminal_frame_value_nested_in_a_broken_payload_is_never_compared():
    """verify-r2 #1: a value nested in a broken payload is never promoted to be the payload,
    whatever broke it: a ',' or ':' missing before the nested object/array, a lost '[' (so a
    ']' closes a '{'), a stray token after the elements read so far. Each was reported
    'compared' with made-up drift (TYPE_CHANGED, FIELD_ADDED/FIELD_REMOVED, ELEMENT_REMOVED)
    or 'payload_count_changed'. Plain labels next to a payload still leave the payload
    compared."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    jobs = 'jobs [{"id": 1, "v": [1, 2]}, {"id": 2, "v": {"z": 1}}]'
    broken = [  # (intact baseline frame, broken frame)
        ('[1, {"b": 1}]', '[1 {"b": 1}]'),
        ('{"a": {"b": 1}}', '{"a" {"b": 1}}'),
        ("[1, [2]]", "[1 [2]]"),
        ('cfg {"a": {"b": 1}}', 'cfg {"a" {"b": 2}}'),
        (jobs, 'jobs [{"id": 1, "v": 1, 2]}, {"id": 2, "v": {"z": 1}}]'),
        ('["a", "b", {"c": 1}]', '["a" "b" {"c": 1}]'),
        ('[1, 2, 3, {"d": [4]}]', '[1, 2, 3" {"d": [4]}]'),
        ('x [{"id": 1}] {"a": 1}', 'x [{"id": 1, 2}] {"a": 1}'),
    ]
    assert [(_extract(f), _schema_outcome(insp, b, f)) for b, f in broken] == [
        (("unparsable", []), ("unparsable", None))] * len(broken)
    # '{ok "]", {...}}' is one group (the quoted ']' is string content) that is not JSON
    assert _extract('{ok "]", {"a": 1}}') == ("unparsable", [])

    stray = [  # plain labels next to an intact payload: the payload is still compared
        ('[1/3] {"a": 1}', '[1/3] {"a": 9}'),
        ('[C:\\tmp] {"a": 1}', '[C:\\tmp] {"a": 9}'),
    ]
    assert [_schema_outcome(insp, b, f) for b, f in stray] == [
        ("compared", [("a", "VALUE_DRIFT", 1, 9)])] * len(stray)

    by_rule = [  # were compared as 'text brackets'; the payload rule reads them as unparsable
        ("grep -E '[^}]' {\"a\": 1}", "grep -E '[^}]' {\"a\": 9}"),  # two clauses: '}' closes a
        # '[' (wrong type), and the ']' after it is a closer at depth 0
        ('{bad {"a": 1}}', '{bad {"a": 9}}'),  # the {"a": ..} is below depth 0, never a
        # candidate; its group is not JSON and not a plain label
    ]
    assert [_schema_outcome(insp, b, f) for b, f in by_rule] == [
        ("unparsable", None)] * len(by_rule)
    # the wrong type alone decides: '[12:00]' is a plain label (text), '[12:00}' is not; without
    # that clause '[12:00}' reads as a label and the payload after it is compared (VALUE_DRIFT)
    assert _schema_outcome(insp, '[12:00] {"a": 1}', '[12:00} {"a": 9}') == ("unparsable", None)


def test_inspect_terminal_frame_payload_cut_off_at_the_top_is_unparsable():
    """verify-r2 #2: a pane showing only the tail of a pretty-printed payload (its head scrolled
    off) is 'unparsable' -- closers with no opener give it away -- instead of comparing whatever
    nested value survived: a change in the cut-off part was reported 'compared', 0 drift. A
    window of member lines ('"job": {...}') is a fragment the same way. Whole payloads are
    still compared."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    lines5 = json.dumps({"rate": 5, "job": {"id": 7, "tags": ["a", "b"]}}, indent=2).split("\n")
    lines9 = json.dumps({"rate": 9, "job": {"id": 7, "tags": ["a", "b"]}}, indent=2).split("\n")
    # control: the whole payload on screen -> the real drift is reported
    assert _schema_outcome(insp, "\n".join(lines5), "\n".join(lines9)) == (
        "compared", [("rate", "VALUE_DRIFT", 5, 9)])
    # the pane from '    "id": 7,' down: rate 5 -> 9 is off screen
    tail5, tail9 = "\n".join(lines5[3:]), "\n".join(lines9[3:])
    assert _extract(tail9) == ("unparsable", [])
    assert _schema_outcome(insp, tail5, tail9) == ("unparsable", None)
    # every cut through the head leaves a frame that is not a payload
    assert [_extract("\n".join(lines9[k:])) for k in range(1, len(lines9))] == [
        ("unparsable", [])] * (len(lines9) - 1)

    # the tail of an array of records: the last record decodes whole, the '},' above it
    # closes nothing on screen; v 5 -> 9 in the cut-off record was reported clean
    rows5 = json.dumps([{"k": 1, "v": 5}, {"k": 2, "v": 7}], indent=2).split("\n")
    rows9 = json.dumps([{"k": 1, "v": 9}, {"k": 2, "v": 7}], indent=2).split("\n")
    assert _schema_outcome(insp, "\n".join(rows5[3:]), "\n".join(rows9[3:])) == (
        "unparsable", None)
    assert _schema_outcome(insp, "\n".join(rows5), "\n".join(rows9)) == (
        "compared", [("[0].v", "VALUE_DRIFT", 5, 9)])

    # a window of member lines, no closer left over: the member's value is not a payload
    assert _schema_outcome(insp, '  "job": {"id": 7},\n  "rate": 5',
                           '  "job": {"id": 7},\n  "rate": 9') == ("unparsable", None)


def test_inspect_terminal_frame_compares_deep_payloads_without_recursion_error():
    """A payload nested 5000 deep is compared (the drift walk keeps its own stack), and one
    nested past the JSON decoder's depth limit is 'unparsable': nothing raises out."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    deep = '{"a":' * 5000 + "%s" + "}" * 5000
    too_deep = "[" * 100000 + "]" * 100000
    try:
        rep = insp.inspect_terminal_frame(deep % '{"z": 2}', baseline_frame_text=deep % '{"z": 1}')
        too = insp.inspect_terminal_frame(too_deep, baseline_frame_text=too_deep)
    except RecursionError as exc:
        pytest.fail(f"RecursionError escaped inspect_terminal_frame: {exc!r}")

    assert (rep.schema_status, [(d.path, d.drift_type, d.baseline_value, d.target_value)
                                for d in rep.schema_drift.drifts]) == (
        "compared", [(".".join(["a"] * 5000 + ["z"]), "VALUE_DRIFT", 1, 2)])
    assert (too.schema_status, too.schema_drift) == ("unparsable", None)


def test_identical_nan_payload_is_not_schema_drift():
    """NaN != NaN must not turn an unchanged payload into VALUE_DRIFT; a changed one still is."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import TerminalDriftInspector

    insp = TerminalDriftInspector()
    assert _schema_outcome(insp, '{"a": NaN, "b": 1}', '{"a": NaN, "b": 1}') == ("compared", [])
    status, drifts = _schema_outcome(insp, '{"a": NaN}', '{"a": 1.5}')
    assert (status, [(p, t, v) for p, t, _b, v in drifts]) == (
        "compared", [("a", "VALUE_DRIFT", 1.5)])


def test_schema_drift_findings_keep_depth_first_order():
    """Pins the finding order (kept across the switch to an explicit-stack walk): at a dict,
    added then removed fields, then common fields in key order; at a list, common elements
    in index order, then its added/removed tail."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import inspect_schema_drift

    base = {"a": [{"x": 1}, 2, 3], "b": {"c": 1}, "gone": 0}
    target = {"a": [{"x": 2, "y": 0}, 2], "b": {"c": "1"}, "new": 1}
    assert [(d.path, d.drift_type) for d in inspect_schema_drift(base, target).drifts] == [
        ("new", "FIELD_ADDED"),
        ("gone", "FIELD_REMOVED"),
        ("a[0].y", "FIELD_ADDED"),
        ("a[0].x", "VALUE_DRIFT"),
        ("a[2]", "ELEMENT_REMOVED"),
        ("b.c", "TYPE_CHANGED"),
    ]


def test_extract_json_payloads_selection_rules():
    """The payload rule, one clause per line: every depth-0 JSON object or array is a payload,
    in frame order (nested values belong to their payload and are never tried on their own);
    plain labels ('{ok}', '[INFO]': no '"', nothing nested) are text; a closer at depth 0 or
    of the wrong type, a frame ending inside a group or a string, a group that is neither
    JSON nor a label, JSON the decoder cannot hold and a member value make it unparsable;
    text and labels only is absent."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    extract = getattr(terminal_drift, "extract_json_payloads", None)
    assert extract is not None, "terminal_drift.extract_json_payloads missing"

    assert extract('kw {} data {"a": 1}') == ("found", [{}, {"a": 1}])
    assert extract('{"a": 1} {"b": 2}') == ("found", [{"a": 1}, {"b": 2}])
    assert extract('{"id": 1}\n{"id": 2}\n') == ("found", [{"id": 1}, {"id": 2}])
    assert extract('[INFO] rows [{"a": 1}, {"a": 2}]') == ("found", [[{"a": 1}, {"a": 2}]])
    assert extract('[12:34:56] [1/3] {ok} [INFO] {"a": 1}') == ("found", [{"a": 1}])
    assert extract('job: {"a": 1}') == ("found", [{"a": 1}])  # ':' after plain text
    # the {"x": ..} is below depth 0, never a candidate; its group is not JSON, not a label
    assert extract('{bad {"x": {"y": 1}}}') == ("unparsable", [])
    assert extract('stats {"a": {"b": 1}, "c":') == ("unparsable", [])
    assert extract('x = [{"a": 1}, oops]') == ("unparsable", [])
    assert extract('{ok, {"a": 1}}') == ("unparsable", [])
    assert extract('done] {"a": 1}') == ("unparsable", [])  # a closer at depth 0
    assert extract('[{"a": 1}}') == ("unparsable", [])  # wrong type; also not JSON, not a label
    assert extract('[12:00} {"a": 1}') == ("unparsable", [])  # a closer of the wrong type
    assert extract("{INFO]") == ("unparsable", [])  # (closed by '}' it is a label: text)
    assert extract('{"a": [1, 2]') == ("unparsable", [])  # ends inside a group
    assert extract('{"a": "b}') == ("unparsable", [])  # ends inside a string
    assert extract('{"a": 1,}') == ("unparsable", [])  # not JSON, not a label
    assert extract("[" + "1" * 5000 + "]") == ("unparsable", [])  # int() will not take it
    assert extract('said "job": {"a": 1}') == ("unparsable", [])  # a member value
    assert extract('said "job" : {"a": 1}') == ("unparsable", [])  # whitespace around ':'
    assert extract("cfg {not json}") == ("absent", [])  # a plain label is text
    # '"' is text at depth 0; '{x}' and '[1, 2 3]' (no '"', nothing nested) are labels
    assert extract('say "{x}" [1, 2 3]') == ("absent", [])
    assert extract("[INFO] 3/10 done") == ("absent", [])
    assert extract("no braces here") == ("absent", [])


_U, _UX = ("unparsable", None), ("unparsable", [])
_STEP = '["step 2/3 done]", "ok", {"id": 7, "rate": 5}]'
_VFY1_DUMP = '{\n  "job": {\n    "id": 7,\n    "tags": ["a", "b"]\n  },\n  "rate": %d\n}'
_VFY1_TAIL = "\n".join((_VFY1_DUMP % 9).split("\n")[-4:])  # the pane shows the last 4 lines
# Every frame shape the adversarial verify rounds named (VERIFY-HISTORY r0-r2) and every frame
# of their payload probes, as the round wrote it (the 5000-deep and NaN frames are in their own
# tests):
# (id, baseline frame, frame, schema outcome, extract_json_payloads(frame), baseline state)
_VERIFY_HISTORY_SHAPES = [
    # r0: every payload of a frame is compared; a payload cut off at the bottom
    ("r0-array-element", '[{"a": 1}, {"a": 2}]', '[{"a": 1}, {"a": 3}]',
     ("compared", [("[1].a", "VALUE_DRIFT", 2, 3)]), ("found", [[{"a": 1}, {"a": 3}]]), "found"),
    ("r0-ndjson-record", '{"id": 1, "rate": 5}\n{"id": 2, "rate": 7}',
     '{"id": 1, "rate": 5}\n{"id": 2}', ("compared", [("payload[1].rate", "FIELD_REMOVED", 7, None)]),
     ("found", [{"id": 1, "rate": 5}, {"id": 2}]), "found"),
    ("r0-shorter-object", '{"k": 1} {"long_key_xxxxxxxx": 1}', '{"k": 2} {"long_key_xxxxxxxx": 1}',
     ("compared", [("payload[0].k", "VALUE_DRIFT", 1, 2)]),
     ("found", [{"k": 2}, {"long_key_xxxxxxxx": 1}]), "found"),
    ("r0-longest-flips", '{"a": 1}\n{"bb": 22}', '{"a": 1000}\n{"bb": 2}',
     ("compared", [("payload[0].a", "VALUE_DRIFT", 1, 1000), ("payload[1].bb", "VALUE_DRIFT", 22, 2)]),
     ("found", [{"a": 1000}, {"bb": 2}]), "found"),
    ("r0-scalar-array", "[1, 2, 3]", "[1, 2, 4]", ("compared", [("[2]", "VALUE_DRIFT", 3, 4)]),
     ("found", [[1, 2, 4]]), "found"),
    ("r0-cut-at-bottom", 'stats {"a": {"b": 1}, "c": 2, "d": 3}', 'stats {"a": {"b": 1}, "c": 2, "d":',
     _U, _UX, "found"),
    # r0 OK facts: a label after the payload is text; the other two were 'compared' up to r2
    ("r0-ok-label-after-payload", '{"a": 1} {ok}', '{"a": 9} {ok}',
     ("compared", [("a", "VALUE_DRIFT", 1, 9)]), ("found", [{"a": 9}]), "found"),
    ("r0-ok-unclosed-brace-before", 'cfg { {"a": 1}', 'cfg { {"a": 9}', _U, _UX, "unparsable"),
    ("r0-ok-brace-in-quoted-prefix", 'log "{x" {"a": 1}', 'log "{x" {"a": 9}', _U, _UX, "unparsable"),
    # r1: a ',' or ':' missing before a nested value; a lost '['; the tail of a pretty dump
    ("r1-no-comma-object", '[1, {"b": 1}]', '[1 {"b": 1}]', _U, _UX, "found"),
    ("r1-no-colon-object", '{"a": {"b": 1}}', '{"a" {"b": 1}}', _U, _UX, "found"),
    ("r1-no-comma-array", "[1, [2]]", "[1 [2]]", _U, _UX, "found"),
    ("r1-no-colon-framed", 'cfg {"a": {"b": 1}}', 'cfg {"a" {"b": 2}}', _U, _UX, "found"),
    ("r1-lost-bracket", 'jobs [{"id": 1, "v": [1, 2]}, {"id": 2, "v": {"z": 1}}]',
     'jobs [{"id": 1, "v": 1, 2]}, {"id": 2, "v": {"z": 1}}]', _U, _UX, "found"),
    ("r1-cut-at-top", "\n".join((_VFY1_DUMP % 5).split("\n")[-4:]), _VFY1_TAIL, _U, _UX, "unparsable"),
    ("r1-cut-at-top-vs-whole", _VFY1_DUMP % 5, _VFY1_TAIL, _U, _UX, "found"),
    ("r1-whole-dump-control", _VFY1_DUMP % 5, _VFY1_DUMP % 9, ("compared", [("rate", "VALUE_DRIFT", 5, 9)]),
     ("found", [{"job": {"id": 7, "tags": ["a", "b"]}, "rate": 9}]), "found"),
    # r1 probes: python reprs and JS objects are not JSON; prose '[1]' is JSON, so a payload
    ("r1-probe-python-repr-list", "retry kwargs {'ids': [1, 2]} payload {\"a\": 1}",
     "retry kwargs {'ids': [1, 2]} payload {\"a\": 9}", _U, _UX, "unparsable"),
    ("r1-probe-python-repr-dict", "opts {'h': {}} data {\"a\": 1}", "opts {'h': {}} data {\"a\": 9}",
     _U, _UX, "unparsable"),
    ("r1-probe-python-repr-nested-list", "args ['x', [1]] -> {\"a\": 1}", "args ['x', [1]] -> {\"a\": 9}",
     _U, _UX, "unparsable"),
    ("r1-probe-no-colon-both", '{"a" {"b": 1}}', '{"a" {"b": 2}}', _U, _UX, "unparsable"),
    ("r1-probe-no-colon-spaced", '{"a": {"b": 1}}', '{ "a" {"b": 2}}', _U, _UX, "found"),
    ("r1-probe-no-colon-in-array", '[{"a": {"b": 1}}]', '[{"a" {"b": 1}}]', _U, _UX, "found"),
    ("r1-probe-unquoted-key", '{a: {"b": 1}}', '{a: {"b": 2}}', _U, _UX, "unparsable"),
    ("r1-probe-broken-then-payload", '[oops, [1]] {"a": 1}', '[oops, [1]] {"a": 9}', _U, _UX,
     "unparsable"),
    ("r1-probe-js-object-after-payload", '{"a": 1} {x: [1]}', '{"a": 9} {x: [1]}', _U, _UX,
     "unparsable"),
    ("r1-probe-ndjson-cut-record", '{"id": 1}\n{"id": 2}', '{"id": 1}\n{"id": 2}\n{"id": 3, "ra', _U, _UX,
     "found"),
    ("r1-probe-opener-after-payload", '{"a": 1}', '{"a": 9} [', _U, _UX, "found"),
    ("r1-probe-lone-opener", "Loading [", "Loading [", _U, _UX, "unparsable"),
    ("r1-probe-int-too-long", '{"a": ' + "9" * 5000 + "}", '{"a": 1}', _U, ("found", [{"a": 1}]),
     "unparsable"),
    ("r1-probe-ndjson-reordered", '{"id": 1}\n{"id": 2}', '{"id": 2}\n{"id": 1}',
     ("compared", [("payload[0].id", "VALUE_DRIFT", 1, 2), ("payload[1].id", "VALUE_DRIFT", 2, 1)]),
     ("found", [{"id": 2}, {"id": 1}]), "found"),
    ("r1-probe-payload-in-single-quotes", "'{\"a\": 1}'", "'{\"a\": 9}'",
     ("compared", [("a", "VALUE_DRIFT", 1, 9)]), ("found", [{"a": 9}]), "found"),
    ("r1-probe-duplicate-keys", '{"a": 1, "a": 2}', '{"a": 2}', ("compared", []), ("found", [{"a": 2}]),
     "found"),
    ("r1-probe-array-shorter", '[{"a":1},{"a":2}]', '[{"a":1}]',
     ("compared", [("[1]", "ELEMENT_REMOVED", {"a": 2}, None)]), ("found", [[{"a": 1}]]), "found"),
    ("r1-probe-prose-bracket-number", 'Step [1] of 3 {"a": 1}', 'Step [2] of 3 {"a": 1}',
     ("compared", [("payload[0][0]", "VALUE_DRIFT", 1, 2)]), ("found", [[2], {"a": 1}]), "found"),
    ("r1-probe-prose-bracket-appears", '{"a": 1}', 'retry [1]: {"a": 9}', ("payload_count_changed", None),
     ("found", [[1], {"a": 9}]), "found"),
    ("r1-probe-empty-frame", "", "", ("no_payload", None), ("absent", []), "absent"),
    # r1 probes: drift typing (True vs 1 and 1 vs 1.0 are TYPE_CHANGED) and ANSI in a string: an
    # escaped ESC is string content; a raw OSC/CSI is stripped first (formatting, which the visual
    # diff reports), so those payloads compare equal
    ("r1-probe-bool-vs-int", '{"a": true}', '{"a": 1}', ("compared", [("a", "TYPE_CHANGED", True, 1)]),
     ("found", [{"a": 1}]), "found"),
    ("r1-probe-int-vs-float", '{"a": 1}', '{"a": 1.0}', ("compared", [("a", "TYPE_CHANGED", 1, 1.0)]),
     ("found", [{"a": 1.0}]), "found"),
    ("r1-probe-escaped-esc-in-string", '{"m": "\\u001b[31mx"}', '{"m": "\\u001b[32mx"}',
     ("compared", [("m", "VALUE_DRIFT", "\x1b[31mx", "\x1b[32mx")]), ("found", [{"m": "\x1b[32mx"}]),
     "found"),
    ("r1-probe-osc-raw-in-string", '{"m": "x\x1b]0;t\x07y"}', '{"m": "x\x1b]0;u\x07y"}', ("compared", []),
     ("found", [{"m": "xy"}]), "found"),
    ("r1-probe-csi-raw-in-string", '{"m": "\x1b[31mred\x1b[0m"}', '{"m": "\x1b[32mred\x1b[0m"}',
     ("compared", []), ("found", [{"m": "red"}]), "found"),
    # the rule's blind spot: a flat array with no string that lost a ',' has no '"' and nothing
    # nested, so it reads as a plain label ('[1/3]') -- text: nothing is compared
    ("r1-probe-flat-array-lost-comma", "[1, 2, 3]", "[1, 2 3]", ("no_payload", None), ("absent", []),
     "found"),
    # r2: a string element holding ']' (or '}') in a broken array or object
    ("r2-quoted-close-no-comma", '["a]", "b", {"c": 1}]', '["a]" "b", {"c": 1}]', _U, _UX, "found"),
    ("r2-quoted-close-stray-token", '["]", 1, 2, {"a": 1}]', '["]", 1 2, {"a": 1}]', _U, _UX, "found"),
    ("r2-log-array-no-comma", 'events ["[1/2] fetch", "[2/2] save]", "done", {"id": 7, "rate": 5}]',
     'events ["[1/2] fetch", "[2/2] save]" "done", {"id": 7, "rate": 9}]', _U, _UX, "found"),
    ("r2-regex-string-no-comma", '["[^]]", "x", [1, 2]]', '["[^]]" "x", [1, 2]]', _U, _UX, "found"),
    ("r2-bare-first-token", '[oops, "]", {"a": 1}]', '[oops "]", {"a": 1}]', _U, _UX, "unparsable"),
    ("r2-both-frames-broken", '["]", 1 2, {"a": 1}]', '["]", 1 2, {"a": 2}]', _U, _UX, "unparsable"),
    ("r2-escaped-quote-close", '["\\"]", 1, 2, {"b": 1}]', '["\\"]", 1 2, {"b": 1}]', _U, _UX, "found"),
    ("r2-array-in-prose", 'x ["]", 1, [2, 3]] y', 'x ["]" 1, [2, 3]] y', _U, _UX, "found"),
    ("r2-fuzz-inserted-x", _STEP, '[x"step 2/3 done]", "ok", {"id": 7, "rate": 5}]', _U, _UX, "found"),
    ("r2-fuzz-inserted-comma", _STEP, '[,"step 2/3 done]", "ok", {"id": 7, "rate": 5}]', _U, _UX,
     "found"),
    ("r2-fuzz-lost-quote", _STEP, '["step 2/3 done], "ok", {"id": 7, "rate": 5}]', _U, _UX, "found"),
    ("r2-fuzz-doubled-quote", _STEP, '["step 2/3 done]"", "ok", {"id": 7, "rate": 5}]', _U, _UX,
     "found"),
    ("r2-fuzz-lost-comma", _STEP, '["step 2/3 done]" "ok", {"id": 7, "rate": 5}]', _U, _UX, "found"),
    ("r2-object-quoted-close-key", '{"a}": 1, "b": {"c": 1}}', '{"a}": 1 "b": {"c": 1}}', _U, _UX,
     "found"),
    ("r2-object-quoted-close-value", '{"a": "}", "b": [1, 2]}', '{"a": "}" "b": [1, 2]}', _U, _UX,
     "found"),
    ("r2-object-no-colon-after-quoted-close", '{"a": "}", "b": [1, 2], "c": 3}',
     '{"a": "}", "b" [1, 2], "c": 3}', _U, _UX, "found"),
    ("r2-object-quoted-close-in-list", '{"k": ["]"], "z": {"c": 1}}', '{"k": ["]"] "z": {"c": 1}}',
     _U, _UX, "found"),
    ("r2-array-quoted-close-in-object", '[{"a": "]"}, "x", "y", {"b": 1}]',
     '[{"a": "]"}, "x" "y", {"b": 1}]', _U, _UX, "found"),
    ("r2-array-quoted-close-in-list", '[["]"], 1, {"b": 1}]', '[["]"] 1, {"b": 1}]', _U, _UX, "found"),
    # r2 controls: the same breaks with no quoted closer
    ("r2-control-no-comma", '["a", "b", {"c": 1}]', '["a" "b", {"c": 1}]', _U, _UX, "found"),
    ("r2-control-stray-token", '["x", 1, 2, {"a": 1}]', '["x", 1 2, {"a": 1}]', _U, _UX, "found"),
    ("r2-control-log-array", 'events ["[1/2] fetch", "[2/2] save", "done", {"id": 7, "rate": 5}]',
     'events ["[1/2] fetch", "[2/2] save" "done", {"id": 7, "rate": 9}]', _U, _UX, "found"),
    ("r2-control-regex", '["[^x]", "x", [1, 2]]', '["[^x]" "x", [1, 2]]', _U, _UX, "found"),
    ("r2-control-object-no-comma", '{"a": 1, "b": {"c": 1}}', '{"a": 1 "b": {"c": 1}}', _U, _UX,
     "found"),
]


@pytest.mark.parametrize(
    "baseline,frame,outcome,payloads,baseline_state",
    [case[1:] for case in _VERIFY_HISTORY_SHAPES],
    ids=[case[0] for case in _VERIFY_HISTORY_SHAPES])
def test_payload_rule_verify_history_frame_shape(baseline, frame, outcome, payloads, baseline_state):
    """B8-B#B, per frame shape the verify rounds r0-r2 named: the frame's payloads are compared
    exactly as the payload rule says, or the frame is 'unparsable' -- no value from inside a
    broken payload is compared (r2 compared one on 15 of these frames)."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    insp = terminal_drift.TerminalDriftInspector()

    assert (_schema_outcome(insp, baseline, frame), _extract(frame)) == (outcome, payloads)
    # the baseline as the round wrote it: an intact one is read whole (the probe can say 'found')
    assert _extract(baseline)[0] == baseline_state


# Valid payloads for the property pins. Each holds a string or a nested container: a flat,
# string-free array that loses a ',' ('[1 2]') is a plain label by the rule -- text, 'absent'
# (pinned in test_extract_json_payloads_selection_rules).
_RULE_PAYLOADS = [
    {"rate": 5, "job": {"id": 7, "tags": ["a", "b"]}},
    [{"id": 1, "v": [1, 2]}, {"id": 2, "v": {"z": 1}}],
    ["[1/2] fetch", "[2/2] save]", "done", {"id": 7, "rate": 5}],
    {"msg": 'say "hi" {ok} [x]', "path": "C:\\tmp\\", "n": None, "ok": True, "e": []},
    ["a]", "b", {"c": 1}],
    ["[^]]", "x", [1, 2]],
    {"a": {"b": {"c": {"d": [1, [2, [3]]]}}}, "e": "str{with}braces[and]brackets"},
    {"k": '}"]', "z": [{"q": '\\"'}]},
    [[["x"]], {"": {}}],
    # verify-r2's fuzz documents: an unbalanced ']' in a string element
    ["step 2/3 done]", "ok", {"id": 7, "rate": 5}],
    ["[WARN] retry]", 3, [1, 2]],
    [":]", "x", {"a": [1, {"b": 2}]}],
]
_RULE_PRE, _RULE_SUF = "job 7 status\n", "\n-- 3 rows --"
_RULE_CHARS = ' x",:{}[]\\1'


def _decoded_whole(text):
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, (dict, list)) else None


def _rule_cases(op):
    """(intact frame, damaged frame, the payload's text as damaged -- None when cut) for each
    payload, compact and indent=2, between two lines of text."""
    for payload in _RULE_PAYLOADS:
        for text in (json.dumps(payload), json.dumps(payload, indent=2)):
            intact = _RULE_PRE + text + _RULE_SUF
            a, b = len(_RULE_PRE), len(_RULE_PRE) + len(text)
            if op == "delete":  # every character of the frame
                for i in range(len(intact)):
                    body = text[:i - a] + text[i - a + 1:] if a <= i < b else text
                    yield intact, intact[:i] + intact[i + 1:], body
            elif op == "cut":  # every cut through the payload, at the top and at the bottom
                for j in range(1, len(text)):
                    yield intact, text[j:] + _RULE_SUF, None
                    yield intact, _RULE_PRE + text[:j], None
            elif op == "insert":  # a JSON-significant char anywhere between its brackets
                for i in range(a + 1, b):
                    for ch in _RULE_CHARS:
                        yield intact, intact[:i] + ch + intact[i:], text[:i - a] + ch + text[i - a:]
            else:  # "substitute": every character of the payload, its brackets included
                for i in range(a, b):
                    for ch in _RULE_CHARS.replace(intact[i], ""):
                        yield (intact, intact[:i] + ch + intact[i + 1:],
                               text[:i - a] + ch + text[i - a + 1:])


def _rule_check(op):
    """(frames, frames the rule must call 'unparsable', mismatches, first 3 mismatches). The
    rule allows exactly two results: the whole damaged payload compared when its text is still
    JSON, else 'unparsable' -- never a value from inside it compared on its own."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    insp = terminal_drift.TerminalDriftInspector(max_allowed_width=None)
    frames = must_be_unparsable = 0
    bad = []
    for intact, frame, body in _rule_cases(op):
        whole = None if body is None else _decoded_whole(body)
        want = ("unparsable", _UX) if whole is None else ("compared", ("found", [whole]))
        got = (_schema_outcome(insp, intact, frame)[0], _extract(frame))
        frames += 1
        must_be_unparsable += whole is None
        if got != want:
            bad.append((frame, got, want))
    return frames, must_be_unparsable, len(bad), bad[:3]


def test_payload_rule_holds_for_every_deletion_and_every_cut():
    """B8-B#B as a property of the rule: for each valid payload P (compact and indent=2, inside
    text), every single-character deletion anywhere in the frame and every cut through P at the
    top or at the bottom compares exactly the whole damaged payload or is 'unparsable'
    (r2 compared a value from inside the damaged P on 39 of these deletions and read no
    payload on 12)."""
    assert _rule_check("delete") == (2122, 566, 0, [])
    assert _rule_check("cut") == (2948, 2948, 0, [])


def test_payload_rule_holds_for_every_insertion_and_substitution():
    """The same property for one JSON-significant character (' x",:{}[]' backslash '1')
    inserted between P's brackets or put in place of any character of P (r2 compared a
    value from inside the damaged P on 867 + 773 of these and read no payload on 248 + 258)."""
    assert _rule_check("insert") == (16214, 10920, 0, [])
    assert _rule_check("substitute") == (15400, 12096, 0, [])


# The payload rule clause by clause: each row is a frame one clause alone decides -- take that
# clause out of extract_json_payloads and the result becomes the one in the comment above the
# row (measured on a one-site mutant per clause) -- or a control beside it that the clause
# must let through. 'The frame ends inside a string' has no row of its own: a group holding
# an unterminated string can neither decode nor be a plain label, so another clause makes
# that frame unparsable too. 'Too deep' is in the deep-payload test.
_RULE_CLAUSE_ROWS = [  # (id, frame, extract_json_payloads(frame))
    # a '}' or ']' at depth 0 -- without: found [{'a': 1}]
    ("depth0-closer", 'done] {"a": 1}', _UX),
    # a closer of the wrong type: a flat, quote-free group is a plain label when its closer
    # matches ('[12:00]', '{1, 2}') -- without the clause these read as absent or as found
    # [the payloads beside them]
    ("wrong-type-alone", "{INFO]", _UX),
    ("wrong-type-empty", "[}", _UX),
    ("wrong-type-before-payload", '[12:00} {"a": 1}', _UX),
    ("wrong-type-after-payload", '{"a": 1} {1, 2]', _UX),
    ("wrong-type-between-payloads", '{"a": 1} [x} [2]', _UX),
    ("right-type-label-before-payload", '[12:00] {"a": 1}', ("found", [{"a": 1}])),
    ("right-type-label-after-payload", '{"a": 1} {1, 2}', ("found", [{"a": 1}])),
    # the frame ends inside a group -- without: found [{'a': 1}]
    ("ends-inside-group", '{"a": 1} [', _UX),
    # a group json.loads rejects that is not a plain label (no '"', nothing nested) -- without
    # the clause, or with either label test dropped: absent; with no labels: unparsable
    ("not-json-not-label", '{"a": 1,}', _UX),
    ("label-nothing-nested", "[1 [2]]", _UX),
    ("label-is-text", '[INFO] {"a": 1}', ("found", [{"a": 1}])),
    # json.loads is strict: a raw control character in a string is rejected, an escaped one is
    # JSON -- with strict=False: found [the payload]
    ("strict-raw-newline-in-string", '{"msg": "wrapped\nline"}', _UX),
    ("strict-raw-tab-in-string", '{"a": "x\ty"}', _UX),
    ("strict-escaped-newline", '{"msg": "wrapped\\nline"}', ("found", [{"msg": "wrapped\nline"}])),
    # a group json.loads cannot hold (an integer too long for int()) -- without: absent
    ("cannot-hold-int", "[" + "1" * 5000 + "]", _UX),
    # a member value: right after '"' and ':' at depth 0, whitespace allowed around the ':' --
    # without the clause, or without the whitespace skip before or after the ':': found
    ("member", 'said "job": {"a": 1}', _UX),
    ("member-no-space", 'said "job":{"a": 1}', _UX),
    ("member-space-before-colon", 'said "job" : {"a": 1}', _UX),
    ("member-tab-before-colon", '"job"\t: {"a": 1}', _UX),
    ("member-newlines-around-colon", '  "job"\n  :\n  [1, 2]', _UX),
    ("member-needs-quote", 'job : {"a": 1}', ("found", [{"a": 1}])),
    ("member-needs-quote-then-colon", 'said "job" x: {"a": 1}', ("found", [{"a": 1}])),
    # depth 0 is plain text: '"{x"' is no string there -- quote-aware at depth 0: found
    ("depth0-quote-is-text", 'log "{x" {"a": 1}', _UX),
    # inside a group brackets in strings are ignored and a backslash escapes -- without: unparsable
    ("string-brackets-ignored", '["a]", {"c": 1}]', ("found", [["a]", {"c": 1}]])),
    ("string-escape", '["\\"]", {"b": 1}]', ("found", [['"]', {"b": 1}]])),
    # nothing below depth 0 is a candidate; every depth-0 group that decodes, in frame order
    ("below-depth0-never-candidate", '{bad {"x": 1}}', _UX),
    ("every-group-in-frame-order", '{"a": 1} [2]', ("found", [{"a": 1}, [2]])),
]


@pytest.mark.parametrize("frame,payloads", [row[1:] for row in _RULE_CLAUSE_ROWS],
                         ids=[row[0] for row in _RULE_CLAUSE_ROWS])
def test_payload_rule_clause_decides_alone(frame, payloads):
    """B8-B#B, the payload rule clause by clause: a frame where one clause alone decides the
    result, or a control the clause must let through (the wrong-type and whitespace-before-':'
    clauses had no such pin: taking either out of the code failed no test)."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    assert _extract(frame) == payloads


def test_render_diff_and_report_formatting():
    """Verify human-readable and structured text rendering of visual diff and schema drift reports."""
    assert terminal_drift is not None, "terminal_drift capability missing"
    from bulk_downloader.terminal_drift import (
        diff_visual_artifacts,
        inspect_schema_drift,
    )

    vdiff = diff_visual_artifacts("Line 1\nLine 2", "Line 1\nLine 2 modified")
    assert vdiff.render_diff() == "  Line 1\n- Line 2\n+ Line 2 modified"

    sreport = inspect_schema_drift({"k": 1}, {"k": "one", "extra": True})
    assert sreport.render_report() == (
        "Schema Drift Report: 2 drifts detected:\n"
        "  [+] FIELD_ADDED   at 'extra' -> True\n"
        "  [~] TYPE_CHANGED  at 'k' from int to str"
    )
    assert inspect_schema_drift({"k": 1}, {"k": 1}).render_report() == (
        "Schema Drift Report: No drift detected.")
