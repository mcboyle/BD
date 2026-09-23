"""Unit tests for Row 993: Resident Memory Page Fault & Heap Growth Telemetry.

Guards:
- Resident memory metrics (RSS, VMS, peak HWM, VmData) read from the process
- Page fault counts and rates move when the process actually faults pages in
  (one fault per base page of a THP-exempt probe region; exact at the getrusage boundary)
- Heap growth is the VmData delta, so one large allocation is a spike
- A field the platform cannot read is None and named in ``unavailable``, never 0;
  a readable status with an undecodable process name is still read
- Bounded history retention (no unevicted memory growth)
- Surfaced through the diagnostics bundle as ``process_memory``
"""
import errno
import io
import json
import mmap
import re
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

BD_GATE_SCOPE = "repo-wide"

MiB = 1024 * 1024
_LINUX_PROC = Path("/proc/self/status").exists()
needs_proc = pytest.mark.skipif(not _LINUX_PROC, reason="needs /proc/self/status")


def _collector(**kw):
    from bulk_downloader.memory_telemetry import ResidentMemoryPageFaultTelemetry
    return ResidentMemoryPageFaultTelemetry(**kw)


def _touch(buf):
    for i in range(0, len(buf), 4096):
        buf[i] = 1


def _fresh_base_pages(size):
    """Never-touched private anonymous memory that faults in one base page per first write.

    A bytearray is malloc'd, and under transparent huge pages one fault maps 2 MiB: the CI
    runner counted 17 faults for 32 MiB -- 16 huge pages plus the malloc chunk's trailing
    4 KiB page (T69 drop, ``assert 17 >= 4096``). MADV_NOHUGEPAGE keeps this region out of
    every THP size in every THP mode.
    """
    region = mmap.mmap(-1, size, flags=mmap.MAP_PRIVATE)
    try:
        region.madvise(mmap.MADV_NOHUGEPAGE)
    except OSError as exc:
        if exc.errno != errno.EINVAL:  # EINVAL: kernel built without THP, nothing to opt out of
            raise
    return region


def test_capability_presence_and_initialization():
    collector = _collector(max_history=50)
    assert collector.max_history == 50
    assert collector.get_history() == []


@needs_proc
def test_resident_metrics_are_the_process_values():
    collector = _collector()
    before = collector.sample()
    buf = bytearray(64 * MiB)
    _touch(buf)
    after = collector.sample()
    assert before.unavailable == []
    assert before.resident.rss_bytes > 0
    assert before.resident.vms_bytes >= before.resident.rss_bytes
    assert before.resident.peak_rss_bytes >= before.resident.rss_bytes
    # 64 MiB of touched pages must show in RSS, not a constant.
    assert after.resident.rss_bytes - before.resident.rss_bytes >= 48 * MiB
    del buf


@needs_proc
def test_page_faults_counted_when_pages_are_touched():
    collector = _collector()
    region = _fresh_base_pages(32 * MiB)
    pages = len(region) // mmap.PAGESIZE
    first = collector.sample()
    assert first.page_faults.minor_fault_rate is None  # no previous sample: no rate
    time.sleep(0.01)
    for offset in range(0, len(region), mmap.PAGESIZE):
        region[offset] = 1
    second = collector.sample()
    region.close()
    faulted = second.page_faults.minor_faults - first.page_faults.minor_faults
    assert faulted >= pages, f"{pages} base pages faulted in, the counter moved {faulted}"
    assert second.page_faults.minor_fault_rate > 0
    assert second.page_faults.major_faults >= first.page_faults.major_faults


def test_fault_counters_are_the_exact_kernel_counter_moves():
    """Exact at the getrusage boundary: totals, deltas and rates are the kernel counters'
    own moves -- not scaled, not swapped, per sample interval."""
    from bulk_downloader import memory_telemetry as mt
    pages = 32 * MiB // 4096
    usage = iter([SimpleNamespace(ru_minflt=1000, ru_majflt=7, ru_maxrss=2048),
                  SimpleNamespace(ru_minflt=1000 + pages, ru_majflt=9, ru_maxrss=2048)])
    fake_resource = SimpleNamespace(RUSAGE_SELF=0, getrusage=lambda who: next(usage))
    collector = _collector()
    with patch.object(mt, "resource", fake_resource):
        first = collector.sample()
        time.sleep(0.01)
        second = collector.sample()
    assert (first.page_faults.minor_faults, first.page_faults.major_faults) == (1000, 7)
    assert (second.page_faults.minor_faults, second.page_faults.major_faults) == (1000 + pages, 9)
    dt = second.timestamp - first.timestamp
    assert second.page_faults.minor_fault_rate == round(pages / dt, 2)
    assert second.page_faults.major_fault_rate == round(2 / dt, 2)


@needs_proc
def test_heap_growth_follows_one_large_allocation():
    collector = _collector(growth_spike_threshold_bytes=16 * MiB)
    collector.sample()
    quiet = collector.sample()
    assert quiet.heap.spike_detected is False
    blob = bytearray(64 * MiB)
    grown = collector.sample()
    assert grown.heap.heap_growth_delta >= 60 * MiB
    assert grown.heap.growth_rate_bytes_sec > 0
    assert grown.heap.spike_detected is True
    del blob


def test_spike_threshold_is_applied_to_the_data_segment_delta():
    collector = _collector(growth_spike_threshold_bytes=1 * MiB)
    with patch.object(collector, "_read_proc_status", return_value={"VmData": 100 * MiB}):
        collector.sample()
    with patch.object(collector, "_read_proc_status", return_value={"VmData": 102 * MiB}):
        snap = collector.sample()
    assert snap.heap.heap_growth_delta == 2 * MiB
    assert snap.heap.spike_detected is True
    with patch.object(collector, "_read_proc_status", return_value={"VmData": 102 * MiB + 1}):
        assert collector.sample().heap.spike_detected is False


def test_unreadable_process_reports_unavailable_not_zero():
    from bulk_downloader import memory_telemetry as mt
    collector = _collector()
    with patch.object(collector, "_read_proc_status", return_value={}), \
            patch.object(mt, "resource", None):
        collector.sample()
        snap = collector.sample()
    for value in (snap.resident.rss_bytes, snap.resident.vms_bytes, snap.resident.peak_rss_bytes,
                  snap.resident.heap_data_bytes, snap.page_faults.minor_faults,
                  snap.page_faults.major_faults, snap.page_faults.minor_fault_rate,
                  snap.heap.heap_growth_delta):
        assert value is None
    assert snap.heap.spike_detected is False
    assert sorted(snap.unavailable) == sorted([
        "resident.rss_bytes", "resident.vms_bytes", "resident.peak_rss_bytes",
        "resident.heap_data_bytes", "page_faults.minor_faults", "page_faults.major_faults"])
    json.dumps(snap.to_dict())



def test_unreadable_proc_status_file_reports_unavailable_not_zero():
    """N5-A E1: the real unreadable path -- open('/proc/self/status') raising -- leaves fields absent, never 0."""
    from bulk_downloader import memory_telemetry as mt
    real_open = open

    def refusing_open(path, *args, **kwargs):
        if str(path) == "/proc/self/status":
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *args, **kwargs)

    collector = _collector()
    with patch.object(mt, "open", refusing_open, create=True), patch.object(mt, "resource", None):
        status = collector._read_proc_status()
        collector.sample()
        snap = collector.sample()
    assert status == {}, f"unreadable /proc/self/status reported as a measurement: {status}"
    for value in (snap.resident.rss_bytes, snap.resident.vms_bytes, snap.resident.peak_rss_bytes,
                  snap.resident.heap_data_bytes, snap.heap.heap_growth_delta):
        assert value is None, f"unreadable /proc/self/status surfaced as {value!r}, not None"
    assert snap.heap.spike_detected is False
    assert {"resident.rss_bytes", "resident.vms_bytes", "resident.peak_rss_bytes",
            "resident.heap_data_bytes"} <= set(snap.unavailable)


@needs_proc
def test_undecodable_process_name_does_not_hide_the_memory_fields():
    """/proc/self/status prints the raw 15-byte comm, so a multi-byte name cut mid-character
    is not UTF-8; the Vm* lines are still the process's memory and must be read."""
    from bulk_downloader import memory_telemetry as mt
    real_open = open
    with real_open("/proc/self/status", "rb") as f:
        # "worker-dl-ééé" is 16 bytes; the kernel keeps 15, so the last é loses its 2nd byte.
        torn, named = re.subn(rb"(?m)^Name:.*$", lambda m: b"Name:\tworker-dl-\xc3\xa9\xc3\xa9\xc3",
                              f.read())
    assert named == 1

    def torn_name_open(path, *args, **kwargs):
        if str(path) == "/proc/self/status":
            return io.TextIOWrapper(io.BytesIO(torn), encoding=kwargs.get("encoding"),
                                    errors=kwargs.get("errors"))
        return real_open(path, *args, **kwargs)

    with patch.object(mt, "open", torn_name_open, create=True):
        snap = _collector().sample()
    assert snap.unavailable == [], f"an undecodable process name hid readable fields: {snap.unavailable}"
    assert snap.resident.rss_bytes > 0 and snap.resident.heap_data_bytes > 0


@needs_proc
def test_rusage_failure_marks_only_fault_fields():
    collector = _collector()
    with patch("resource.getrusage", side_effect=OSError("getrusage unsupported")):
        snap = collector.sample()
    assert snap.page_faults.minor_faults is None
    assert snap.page_faults.major_faults is None
    assert sorted(snap.unavailable) == ["page_faults.major_faults", "page_faults.minor_faults"]
    assert snap.resident.rss_bytes > 0


def test_bounded_history_ring_buffer():
    collector = _collector(max_history=5)
    for _ in range(12):
        collector.sample()
    assert len(collector.get_history()) == 5
    collector.reset()
    assert collector.get_history() == []
    assert collector.sample().heap.heap_growth_delta is None


@needs_proc
def test_diagnostics_bundle_carries_process_memory():
    from bulk_downloader import diagnostics_bundle
    snap = diagnostics_bundle.bundle(include_history=False)["process_memory"]
    assert "error" not in snap
    assert snap["resident"]["rss_bytes"] > 0
    assert snap["page_faults"]["minor_faults"] > 0
    assert snap["unavailable"] == []
    json.dumps(snap)
