"""Cut 845: tests/test_row845_prom_latency_histograms.py -- Prometheus histogram exporter.

PROMETHEUS-HISTOGRAM-DOWNLINK-BANDWIDTH-AND-LATENCY-EXPORTER:
(1) Prometheus /metrics exposition includes valid OpenMetrics histogram buckets,
(2) cumulative bucket counts match recorded events,
(3) in-process calculation adds <0.1ms per chunk.
Partitioned by site_id and transfer_mode.
"""
from __future__ import annotations

import re
import threading
import time
import pytest

from bulk_downloader import metrics_prom

BD_GATE_SCOPE = "module"


def setup_function():
    """Ensure clean histogram metrics state before each test."""
    if hasattr(metrics_prom, "reset_chunk_metrics"):
        metrics_prom.reset_chunk_metrics()


def teardown_function():
    if hasattr(metrics_prom, "reset_chunk_metrics"):
        metrics_prom.reset_chunk_metrics()


def test_row845_prometheus_exposition_includes_valid_histogram_buckets():
    """Acceptance (1): Prometheus /metrics exposition includes valid OpenMetrics
    histogram buckets for bd_chunk_download_duration_seconds partitioned by
    site_id and transfer_mode."""
    metrics_prom.record_chunk_download(
        duration_seconds=0.042,
        size_bytes=1048576,
        site_id="yt_site",
        transfer_mode="http",
    )

    text = metrics_prom.render(s_cfg={}, runners={})

    # HELP and TYPE preambles
    assert "# HELP bd_chunk_download_duration_seconds " in text
    assert "# TYPE bd_chunk_download_duration_seconds histogram" in text

    # Extract all duration bucket lines for yt_site
    bucket_lines = [
        line for line in text.splitlines()
        if line.startswith("bd_chunk_download_duration_seconds_bucket")
        and 'site_id="yt_site"' in line
        and 'transfer_mode="http"' in line
    ]
    assert len(bucket_lines) >= 5, f"Expected multiple bucket lines, got {bucket_lines}"

    # Verify le ordering and formatting
    upper_bounds = []
    has_inf = False
    for line in bucket_lines:
        m = re.search(r'le="([^"]+)"', line)
        assert m, f"Bucket line missing le label: {line}"
        bound_str = m.group(1)
        if bound_str == "+Inf":
            has_inf = True
        else:
            val = float(bound_str)
            upper_bounds.append(val)

    assert has_inf, "Histogram buckets must include le='+Inf'"
    # Verify upper bounds are strictly monotonically ascending
    assert upper_bounds == sorted(upper_bounds), "Histogram buckets must be monotonically ascending"
    assert len(upper_bounds) == len(set(upper_bounds)), "Histogram bucket bounds must be unique"

    # Count and Sum lines
    count_lines = [
        line for line in text.splitlines()
        if line.startswith("bd_chunk_download_duration_seconds_count")
        and 'site_id="yt_site"' in line
    ]
    sum_lines = [
        line for line in text.splitlines()
        if line.startswith("bd_chunk_download_duration_seconds_sum")
        and 'site_id="yt_site"' in line
    ]
    assert len(count_lines) == 1, f"Expected 1 count line, got {count_lines}"
    assert len(sum_lines) == 1, f"Expected 1 sum line, got {sum_lines}"

    # Also verify throughput/bandwidth histogram is exported
    assert "# HELP bd_chunk_download_bandwidth_bytes_per_second " in text
    assert "# TYPE bd_chunk_download_bandwidth_bytes_per_second histogram" in text

    bw_bucket_lines = [
        line for line in text.splitlines()
        if line.startswith("bd_chunk_download_bandwidth_bytes_per_second_bucket")
        and 'site_id="yt_site"' in line
        and 'transfer_mode="http"' in line
    ]
    assert len(bw_bucket_lines) == len(metrics_prom.DEFAULT_BANDWIDTH_BUCKETS) + 1
    expected_bw_les = [metrics_prom._format_bound(b) for b in metrics_prom.DEFAULT_BANDWIDTH_BUCKETS] + ["+Inf"]
    actual_bw_les = []
    for line in bw_bucket_lines:
        m = re.search(r'le="([^"]+)"', line)
        assert m, f"Missing le label in line: {line}"
        actual_bw_les.append(m.group(1))
    assert actual_bw_les == expected_bw_les, f"Bandwidth le mismatch: {actual_bw_les} != {expected_bw_les}"
    # Verify integer formatting from _format_bound: "50000", not "50000.0"
    assert 'le="50000"' in text


def test_row845_cumulative_bucket_counts_match_recorded_events():
    """Acceptance (2): cumulative bucket counts match recorded events and are
    properly partitioned by site_id and transfer_mode."""
    # Partition A: site1 / hls
    # 5 events: 0.01s, 0.04s, 0.09s, 0.20s, 1.5s
    durations_a = [0.01, 0.04, 0.09, 0.20, 1.5]
    for d in durations_a:
        metrics_prom.record_chunk_download(
            duration_seconds=d,
            size_bytes=500_000,
            site_id="site1",
            transfer_mode="hls",
        )

    # Partition B: site2 / direct
    # 2 events: 0.005s, 0.5s
    durations_b = [0.005, 0.5]
    for d in durations_b:
        metrics_prom.record_chunk_download(
            duration_seconds=d,
            size_bytes=1_000_000,
            site_id="site2",
            transfer_mode="direct",
        )

    text = metrics_prom.render(s_cfg={}, runners={})

    # Helper to parse bucket counts for a partition
    def parse_partition_buckets(sid: str, mode: str) -> tuple[dict[str, int], float, int]:
        buckets = {}
        total_sum = 0.0
        total_count = 0
        for line in text.splitlines():
            if f'site_id="{sid}"' in line and f'transfer_mode="{mode}"' in line:
                parts = line.split()
                if line.startswith("bd_chunk_download_duration_seconds_bucket"):
                    m = re.search(r'le="([^"]+)"', parts[0])
                    if m:
                        buckets[m.group(1)] = int(parts[1])
                elif line.startswith("bd_chunk_download_duration_seconds_sum"):
                    total_sum = float(parts[1])
                elif line.startswith("bd_chunk_download_duration_seconds_count"):
                    total_count = int(parts[1])
        return buckets, total_sum, total_count

    buckets_a, sum_a, count_a = parse_partition_buckets("site1", "hls")
    assert count_a == 5
    assert pytest.approx(sum_a, rel=1e-4) == sum(durations_a)
    assert buckets_a["+Inf"] == 5

    # Check cumulative property: count for bucket >= previous bucket count
    last_c = 0
    for b_le, c in sorted(buckets_a.items(), key=lambda x: (float('inf') if x[0] == '+Inf' else float(x[0]))):
        assert c >= last_c, f"Cumulative count decreased at le={b_le}: {c} < {last_c}"
        last_c = c

    # Events in A: 0.01, 0.04, 0.09, 0.20, 1.5
    # For le >= 1.5, count must be 5
    # For le between 0.20 and 1.5, count must be 4
    # For le between 0.09 and 0.20, count must be 3
    # For le between 0.04 and 0.09, count must be 2
    # For le between 0.01 and 0.04, count must be 1
    # For le < 0.01, count must be 0
    for le_str, count in buckets_a.items():
        if le_str == "+Inf":
            continue
        bound = float(le_str)
        expected = sum(1 for d in durations_a if d <= bound)
        assert count == expected, f"Bucket le={le_str} count={count} != expected {expected}"

    # Partition B verification
    buckets_b, sum_b, count_b = parse_partition_buckets("site2", "direct")
    assert count_b == 2
    assert pytest.approx(sum_b, rel=1e-4) == sum(durations_b)
    assert buckets_b["+Inf"] == 2
    for le_str, count in buckets_b.items():
        if le_str == "+Inf":
            continue
        bound = float(le_str)
        expected = sum(1 for d in durations_b if d <= bound)
        assert count == expected, f"Bucket B le={le_str} count={count} != expected {expected}"


def test_row845_in_process_calculation_adds_under_0_1ms_per_chunk():
    """Acceptance (3): in-process calculation adds <0.1ms (100us) per chunk."""
    n_iterations = 20_000

    # Measure time for n_iterations of record_chunk_download
    t0 = time.perf_counter()
    for i in range(n_iterations):
        # Vary durations and sites across iterations
        metrics_prom.record_chunk_download(
            duration_seconds=(i % 1000) * 0.005,
            size_bytes=65536 * (1 + (i % 10)),
            site_id="perf_site",
            transfer_mode="direct",
        )
    elapsed = time.perf_counter() - t0

    time_per_chunk = elapsed / n_iterations
    # 0.1ms = 0.0001 seconds = 100 microseconds
    assert time_per_chunk < 0.0001, (
        f"In-process calculation took {time_per_chunk*1000:.4f}ms per chunk, "
        f"must be < 0.1ms"
    )


def test_row845_concurrent_recording_thread_safe():
    """Concurrent multi-threaded chunk records execute cleanly with no lost events."""
    threads = []
    chunks_per_thread = 500
    n_threads = 8

    def worker(tid: int):
        for i in range(chunks_per_thread):
            metrics_prom.record_chunk_download(
                duration_seconds=0.05,
                size_bytes=100_000,
                site_id=f"site_{tid % 2}",
                transfer_mode="worker_mode",
            )

    for tid in range(n_threads):
        t = threading.Thread(target=worker, args=(tid,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    text = metrics_prom.render()
    total_expected = n_threads * chunks_per_thread

    # Count across both site partitions
    count_matches = re.findall(
        r'bd_chunk_download_duration_seconds_count\{[^}]*transfer_mode="worker_mode"[^}]*\}\s+(\d+)',
        text,
    )
    observed_total = sum(int(c) for c in count_matches)
    assert observed_total == total_expected, f"Expected {total_expected} total counts, got {observed_total}"


def test_row845_reset_chunk_metrics_clears_state():
    """reset_chunk_metrics clears in-memory partitions."""
    metrics_prom.record_chunk_download(0.1, 1024, "reset_site", "http")
    rendered_before = metrics_prom.render()
    assert 'site_id="reset_site"' in rendered_before

    metrics_prom.reset_chunk_metrics()

    rendered_after = metrics_prom.render()
    assert 'site_id="reset_site"' not in rendered_after
    assert "bd_chunk_download_duration_seconds" not in rendered_after
