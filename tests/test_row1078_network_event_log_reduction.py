"""Row 1078: Structured Network Event Log Reduction & Trace Archival.

Validates:
1. Parsing and normalization of raw network event entries.
2. Structured reduction and deduplication of high-volume repetitive network events.
3. Full-fidelity preservation of error events (4xx, 5xx, timeouts) and latency outliers.
4. URL pattern extraction and token scrubbing during log compaction.
5. Trace archival to compressed/structured JSONL files with manifest headers.
6. Trace retrieval, querying, and summary metric aggregation.
7. reduce_network_log accepts a capture dict (bdctl's entry point).
8. Integration with bdctl CLI companion (netlog reduce and stats subcommands).
9. Mutation resistance against relaxed reduction criteria and unasserted data loss.
10. The bdctl-written archive carries no credential (URL query/userinfo, auth headers).

RED on baseline: fails with AssertionError (bulk_downloader lacks network_log_reducer).
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

BD_GATE_SCOPE = "module"


def test_red_baseline_capability_probe():
    """Verify baseline lacks Row 1078 NetworkEventLogReducer and NetworkTraceArchiver.

    RED on baseline (7a99076e): fails with AssertionError.
    """
    try:
        from bulk_downloader import network_log_reducer
        has_reducer = hasattr(network_log_reducer, "NetworkEventLogReducer")
        has_archiver = hasattr(network_log_reducer, "NetworkTraceArchiver")
    except ImportError:
        has_reducer = False
        has_archiver = False

    assert has_reducer and has_archiver, (
        "Row 1078 capability missing: NetworkEventLogReducer and NetworkTraceArchiver not implemented in bulk_downloader"
    )


def test_baseline_positive_control():
    """Positive control proving probe distinguishes existing netlog/log capabilities from missing ones."""
    from bulk_downloader import netlog_classify, log

    assert hasattr(netlog_classify, "classify_network_log"), "Positive control failed: classify_network_log missing"
    assert hasattr(log, "get_logger"), "Positive control failed: get_logger missing"
    assert callable(netlog_classify.classify_network_log)
    assert callable(log.get_logger)


def test_network_event_normalization():
    """Verify normalization of heterogeneous raw network log items into NetworkEvent objects."""
    from bulk_downloader.network_log_reducer import NetworkEvent, normalize_network_event

    raw = {
        "url": "https://cdn.example.com/stream/segment001.ts?token=secret123&expires=9999",
        "method": "GET",
        "response_status": 200,
        "elapsed_ms": 124.5,
        "bytes": 524288,
        "content_type": "video/mp2t",
        "timestamp": 1700000000.0,
    }

    event = normalize_network_event(raw)
    assert isinstance(event, NetworkEvent)
    assert event.url.startswith("https://cdn.example.com/stream/segment001.ts")
    assert event.status_code == 200
    assert event.duration_ms == 124.5
    assert event.bytes_transferred == 524288
    assert event.content_type == "video/mp2t"
    assert event.is_error is False


def test_repetitive_event_reduction():
    """Verify high-volume repetitive segment events are aggregated into a compact summary."""
    from bulk_downloader.network_log_reducer import NetworkEvent, NetworkEventLogReducer

    reducer = NetworkEventLogReducer(min_group_size=5)

    events = []
    # 50 segment requests to the same stream pattern
    for i in range(50):
        events.append(NetworkEvent(
            url=f"https://video.example.org/hls/1080p/seg_{i:04d}.ts",
            method="GET",
            status_code=200,
            duration_ms=50.0 + (i % 10),
            bytes_transferred=100000,
            content_type="video/mp2t",
            timestamp=1700000000.0 + i,
        ))

    result = reducer.reduce_events(events)

    # All 50 identical pattern requests should be compacted into 1 reduced summary
    assert len(result.reduced_summaries) == 1
    summary = result.reduced_summaries[0]
    assert summary.event_count == 50
    assert summary.total_bytes == 5000000
    assert summary.status_distribution == {200: 50}
    assert summary.min_duration_ms == 50.0
    assert summary.max_duration_ms == 59.0
    assert summary.first_timestamp == 1700000000.0
    assert summary.last_timestamp == 1700000049.0
    assert len(result.individual_events) == 0


def test_error_and_outlier_fidelity_preservation():
    """Verify errors and severe latency outliers are never lost in reduction."""
    from bulk_downloader.network_log_reducer import NetworkEvent, NetworkEventLogReducer

    reducer = NetworkEventLogReducer(min_group_size=3, outlier_latency_ms=500.0)

    events = [
        # Normal fast segments
        NetworkEvent(url="https://cdn.x/s1.ts", method="GET", status_code=200, duration_ms=40.0, bytes_transferred=1000),
        NetworkEvent(url="https://cdn.x/s2.ts", method="GET", status_code=200, duration_ms=45.0, bytes_transferred=1000),
        NetworkEvent(url="https://cdn.x/s3.ts", method="GET", status_code=200, duration_ms=42.0, bytes_transferred=1000),
        # 403 Forbidden error
        NetworkEvent(url="https://cdn.x/s4.ts", method="GET", status_code=403, duration_ms=50.0, bytes_transferred=120, error="HTTP 403 Forbidden"),
        # Severe latency outlier (2500ms)
        NetworkEvent(url="https://cdn.x/s5.ts", method="GET", status_code=200, duration_ms=2500.0, bytes_transferred=1000),
    ]

    result = reducer.reduce_events(events)

    # 403 error and 2500ms outlier must be retained as individual events
    assert len(result.individual_events) == 2
    statuses = [e.status_code for e in result.individual_events]
    assert 403 in statuses
    assert any(e.duration_ms >= 2000.0 for e in result.individual_events)


def test_url_pattern_extraction_and_token_scrubbing():
    """Verify URL query tokens and IDs are sanitized into generalized pattern templates."""
    from bulk_downloader.network_log_reducer import extract_url_pattern

    url1 = "https://cdn.aylo.com/media/720p/chunk_0042.m4s?sig=abcdef123456&exp=170009999"
    url2 = "https://cdn.aylo.com/media/720p/chunk_0043.m4s?sig=fedcba654321&exp=170009999"

    pat1 = extract_url_pattern(url1)
    pat2 = extract_url_pattern(url2)

    assert pat1 == pat2
    assert "abcdef123456" not in pat1
    assert "<token>" in pat1 or "<scrubbed>" in pat1 or "chunk" in pat1
    # urlparse raises ValueError on a malformed IPv6 host: the fallback must
    # still be the redacted URL, never the raw one.
    bad = extract_url_pattern("https://[bad/x/seg_01.ts?token=SECRET_BAD_LLL&a=1")
    assert "SECRET_BAD_LLL" not in bad and bad.endswith("token=<scrubbed>&a=1")


def test_trace_archival_and_retrieval(tmp_path):
    """Verify NetworkTraceArchiver writes compressed JSONL trace files and reads them back."""
    from bulk_downloader.network_log_reducer import (
        NetworkEvent,
        NetworkEventLogReducer,
        NetworkTraceArchiver,
    )

    archiver = NetworkTraceArchiver(archive_dir=tmp_path)
    reducer = NetworkEventLogReducer()

    events = [
        NetworkEvent(url="https://site.org/index.html", method="GET", status_code=200, duration_ms=100.0, bytes_transferred=5000),
        NetworkEvent(url="https://site.org/api/login", method="POST", status_code=401, duration_ms=250.0, bytes_transferred=200, error="Unauthorized"),
    ]
    reduced = reducer.reduce_events(events)

    archive_info = archiver.archive_trace("trace_session_001", reduced, metadata={"site": "site_org"})
    assert archive_info.archive_file.exists()
    assert archive_info.total_events == 2

    # Read back and inspect
    loaded_manifest, loaded_records = archiver.read_trace("trace_session_001")
    assert loaded_manifest["trace_id"] == "trace_session_001"
    assert loaded_manifest["metadata"]["site"] == "site_org"
    assert len(loaded_records) > 0


def test_reduce_network_log_accepts_capture_dict():
    """Verify reduce_network_log (bdctl's entry point) reads a capture's network_log."""
    from bulk_downloader.network_log_reducer import reduce_network_log

    capture = {
        "host": "test.com",
        "network_log": [
            {"url": "https://test.com/seg1.ts", "response_status": 200, "elapsed_ms": 30.0, "bytes": 1000},
            {"url": "https://test.com/seg2.ts", "response_status": 200, "elapsed_ms": 35.0, "bytes": 1000},
            {"url": "https://test.com/seg3.ts", "response_status": 200, "elapsed_ms": 32.0, "bytes": 1000},
        ],
    }

    reduced = reduce_network_log(capture, min_group_size=2)
    assert reduced is not None
    assert reduced.total_original_events == 3


def test_bdctl_netlog_cli_integration(tmp_path, capsys):
    """Verify bdctl CLI netlog reduce and stats subcommands."""
    import bdctl
    from bulk_downloader.network_log_reducer import NetworkTraceArchiver

    # Prepare sample raw network log file
    raw_file = tmp_path / "recon_netlog.json"
    raw_data = [
        {"url": "https://cdn.test/1.ts", "response_status": 200, "elapsed_ms": 25.0, "bytes": 500},
        {"url": "https://cdn.test/2.ts", "response_status": 200, "elapsed_ms": 28.0, "bytes": 500},
        {"url": "https://cdn.test/error.ts", "response_status": 500, "elapsed_ms": 50.0, "bytes": 0, "error": "Internal Error"},
    ]
    raw_file.write_text(json.dumps(raw_data), encoding="utf-8")

    out_archive = tmp_path / "reduced.trace.jsonl"

    parser = bdctl.build_parser()
    args_reduce = parser.parse_args(["netlog", "reduce", str(raw_file), "--out", str(out_archive)])
    assert args_reduce.cmd == "netlog"
    assert args_reduce.netcmd == "reduce"

    ret_reduce = bdctl.cmd_netlog_reduce(args_reduce)
    assert ret_reduce == 0
    assert out_archive.exists()

    # Test netlog stats
    args_stats = parser.parse_args(["netlog", "stats", str(out_archive)])
    assert args_stats.netcmd == "stats"

    ret_stats = bdctl.cmd_netlog_stats(args_stats)
    assert ret_stats == 0
    captured = capsys.readouterr()
    assert "Original Events: 3" in captured.out


def test_mutation_resistance_reduction_ratio_strictness():
    """Verify reduction requires genuine repetitive groupings and preserves exact counts."""
    from bulk_downloader.network_log_reducer import NetworkEvent, NetworkEventLogReducer

    # If min_group_size is 5, 4 events must NOT be reduced
    reducer = NetworkEventLogReducer(min_group_size=5)
    events = [
        NetworkEvent(url=f"https://cdn.y/chunk_{i}.m4s", method="GET", status_code=200, duration_ms=10.0, bytes_transferred=100)
        for i in range(4)
    ]

    res = reducer.reduce_events(events)
    assert len(res.reduced_summaries) == 0
    assert len(res.individual_events) == 4

    # Adding a 5th event crosses threshold and collapses them
    events.append(NetworkEvent(url="https://cdn.y/chunk_4.m4s", method="GET", status_code=200, duration_ms=10.0, bytes_transferred=100))
    res2 = reducer.reduce_events(events)
    assert len(res2.reduced_summaries) == 1
    assert res2.reduced_summaries[0].event_count == 5
    assert len(res2.individual_events) == 0


# ── Bounce R1 (N1-A HIGH): the ARCHIVE must not persist credentials ────
_ARCHIVE_SECRETS = (
    "SECRET_TOKEN_AAA", "SECRET_KEY_DDD", "SECRET_SIG_FFF", "SECRET_ACCESS_GGG",
    "USERINFO_PASS_HHH", "COOKIE_SECRET_EEE", "AUTH_SECRET_III",
    "SETCOOKIE_SECRET_JJJ", "APIKEY_SECRET_KKK",
)


def _secret_bearing_raw_log():
    """5 benign segment fetches (compacted into a summary), a 403 login with
    credential headers and an outlier signed URL (both archived individually)."""
    events = [
        {"url": f"https://cdn.test/hls/seg_{i:04d}.ts?token=SECRET_TOKEN_AAA&access_token=SECRET_ACCESS_GGG",
         "response_status": 200, "elapsed_ms": 30.0, "bytes": 1000, "timestamp": 1700000000.0 + i}
        for i in range(5)
    ]
    events.append({
        "url": "https://user:USERINFO_PASS_HHH@site.test/login?key=SECRET_KEY_DDD&next=/home",
        "method": "POST", "response_status": 403, "elapsed_ms": 80.0, "bytes": 10,
        "timestamp": 1700000010.0,
        "headers": {"Cookie": "sid=COOKIE_SECRET_EEE", "Authorization": "Bearer AUTH_SECRET_III",
                    "Set-Cookie": "sid=SETCOOKIE_SECRET_JJJ", "X-Api-Key": "APIKEY_SECRET_KKK",
                    "Accept": "text/html"},
    })
    events.append({
        "url": "https://cdn.test/video/big.mp4?sig=SECRET_SIG_FFF&quality=720",
        "response_status": 200, "elapsed_ms": 5000.0, "bytes": 99, "timestamp": 1700000020.0,
    })
    return events


def test_archive_written_by_bdctl_contains_no_synthetic_secret(tmp_path):
    """RED on 134c1371: the archive carried the preserved events' raw URLs and
    Cookie/Authorization/Set-Cookie/X-Api-Key values verbatim."""
    import bdctl

    raw_file = tmp_path / "raw.json"
    raw_file.write_text(json.dumps(_secret_bearing_raw_log()), encoding="utf-8")
    out_archive = tmp_path / "out.jsonl"
    args = bdctl.build_parser().parse_args(["netlog", "reduce", str(raw_file), "--out", str(out_archive)])
    assert bdctl.cmd_netlog_reduce(args) == 0

    text = out_archive.read_text(encoding="utf-8")
    records = [json.loads(line) for line in text.splitlines()[1:]]
    individual = [r for r in records if r.get("type") != "reduced_summary"]
    summaries = [r for r in records if r.get("type") == "reduced_summary"]
    # The fixture built the shape: one summary of 5, two preserved events.
    assert (len(summaries), summaries[0]["event_count"], len(individual)) == (1, 5, 2)
    # Evidence survives: header NAMES, the concrete path and benign params.
    login = next(r for r in individual if r["status_code"] == 403)
    assert set(login["headers"]) == {"Cookie", "Authorization", "Set-Cookie", "X-Api-Key", "Accept"}
    assert login["headers"]["Accept"] == "text/html"
    assert "site.test/login?" in login["url"] and "next=/home" in login["url"]
    assert any("big.mp4" in r["url"] and "quality=720" in r["url"] for r in individual)

    leaked = {s: text.count(s) for s in _ARCHIVE_SECRETS if s in text}
    assert sum(text.count(s) for s in _ARCHIVE_SECRETS) == 0, f"archive leaks secrets: {leaked}"


def test_b1_default_cli_invocation_out_path_exists_and_stats_reads_it(tmp_path, capsys):
    """B1 RED: without --out, cmd_netlog_reduce must write to in_path.with_suffix('.reduced.jsonl'),
    the printed path must exist, and cmd_netlog_stats must successfully read it."""
    import bdctl

    raw_file = tmp_path / "sample_netlog.json"
    raw_data = [
        {"url": "https://cdn.test/1.ts", "response_status": 200, "elapsed_ms": 25.0, "bytes": 500},
        {"url": "https://cdn.test/2.ts", "response_status": 200, "elapsed_ms": 28.0, "bytes": 500},
    ]
    raw_file.write_text(json.dumps(raw_data), encoding="utf-8")

    parser = bdctl.build_parser()
    args_reduce = parser.parse_args(["netlog", "reduce", str(raw_file)])
    ret_reduce = bdctl.cmd_netlog_reduce(args_reduce)
    assert ret_reduce == 0

    captured = capsys.readouterr()
    expected_out = raw_file.with_suffix(".reduced.jsonl")
    assert f"Archive written to: {expected_out}" in captured.out
    assert expected_out.exists(), f"Default output file {expected_out} does not exist!"

    args_stats = parser.parse_args(["netlog", "stats", str(expected_out)])
    ret_stats = bdctl.cmd_netlog_stats(args_stats)
    assert ret_stats == 0
    stats_captured = capsys.readouterr()
    assert "Original Events: 2" in stats_captured.out


def test_b2_error_string_url_credentials_are_redacted_in_archive(tmp_path):
    """B2 RED: an error string containing a URL with ?token= or signing credentials
    must have those credentials redacted before writing to the trace archive."""
    import bdctl

    error_token = "ERRURL_TOKEN_10"
    raw_file = tmp_path / "raw_error.json"
    raw_data = [
        {
            "url": "https://site.test/api/fetch",
            "method": "GET",
            "response_status": 500,
            "elapsed_ms": 120.0,
            "bytes": 0,
            "error": f"net::ERR_FAILED fetching https://site.test/b?token={error_token}",
        }
    ]
    raw_file.write_text(json.dumps(raw_data), encoding="utf-8")
    out_archive = tmp_path / "error_out.jsonl"
    args = bdctl.build_parser().parse_args(["netlog", "reduce", str(raw_file), "--out", str(out_archive)])
    assert bdctl.cmd_netlog_reduce(args) == 0

    text = out_archive.read_text(encoding="utf-8")
    assert error_token not in text, f"Archive leaked secret from error field: {error_token}"



def test_r3_credentials_in_url_free_error_prose_are_scrubbed_and_prose_kept(tmp_path):
    """r3 (ORDERS-2233): the error string is scrubbed as prose, not as one URL. A credential
    with no URL around it (key=value, bare user:pass@host) must still be scrubbed, and the
    words around it kept. Fixture values are synthetic FAKE-TOKEN shapes (secret scan, ORDERS-2312)."""
    import bdctl

    secrets = ("PROSE_SID_19", "PROSE_KEY_20", "PROSE_PW_22", "FAKE-TOKEN-PROSE-21")
    error = ("upstream refused sid=PROSE_SID_19 key=PROSE_KEY_20 via bob:PROSE_PW_22@proxy.test "
             "access_token=FAKE-TOKEN-PROSE-21 -- giving up")
    raw = tmp_path / "prose.json"
    raw.write_text(json.dumps([{"url": "https://s.t/e", "response_status": 500,
                                "elapsed_ms": 5.0, "bytes": 0, "error": error}]), encoding="utf-8")
    out = tmp_path / "prose_out.jsonl"
    args = bdctl.build_parser().parse_args(["netlog", "reduce", str(raw), "--out", str(out)])
    assert bdctl.cmd_netlog_reduce(args) == 0

    text = out.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 2  # manifest + the one error event
    assert [s for s in secrets if s in text] == []
    archived = json.loads(text.splitlines()[1])["error"]
    assert archived.startswith("upstream refused ") and archived.endswith(" -- giving up"), archived
