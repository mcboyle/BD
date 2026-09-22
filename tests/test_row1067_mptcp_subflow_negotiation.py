"""Row 1067: Multipath TCP (MPTCP) Kernel Subflow Negotiation (RFC 8684).

Validates MPTCP kernel capability detection with three-state outcome (SUPPORTED /
UNSUPPORTED / UNVERIFIABLE per O1224 / RULING-row1068-failopen), fail-closed handling
when unverifiable, IPPROTO_MPTCP socket factory with RFC 8684 standard TCP fallback,
multi-subflow registration, backup priority signaling (MP_PRIO), per-subflow latency/
throughput telemetry, and real production caller integration in bulk_downloader.multi_conn.

RED on baseline: bulk_downloader.mptcp_subflow does not exist.
"""
from __future__ import annotations

import socket
import threading
from unittest.mock import patch

import pytest

BD_GATE_SCOPE = "module"


def test_module_exports():
    """RED assertion 1: Base must provide MPTCP kernel subflow negotiator and three-state capability."""
    try:
        from bulk_downloader import mptcp_subflow
    except ImportError:
        pytest.fail(
            "Base lacks Multipath TCP (MPTCP) kernel subflow negotiation (bulk_downloader.mptcp_subflow)"
        )

    assert hasattr(mptcp_subflow, "IPPROTO_MPTCP")
    assert hasattr(mptcp_subflow, "MptcpCapabilityState")
    assert hasattr(mptcp_subflow, "MptcpCapabilityResult")
    assert hasattr(mptcp_subflow, "MptcpSubflowState")
    assert hasattr(mptcp_subflow, "MptcpSubflowInfo")
    assert hasattr(mptcp_subflow, "MptcpConnectionStats")
    assert hasattr(mptcp_subflow, "MptcpSubflowNegotiator")
    assert hasattr(mptcp_subflow, "get_mptcp_manager")


def test_kernel_mptcp_three_state_capability():
    """Verify kernel MPTCP capability detection returns honest three-state outcome (O1224)."""
    from bulk_downloader.mptcp_subflow import (
        MptcpCapabilityState,
        MptcpSubflowNegotiator,
    )

    negotiator = MptcpSubflowNegotiator()
    res = negotiator.check_capability()
    assert res.state in (
        MptcpCapabilityState.SUPPORTED,
        MptcpCapabilityState.UNSUPPORTED,
        MptcpCapabilityState.UNVERIFIABLE,
    )
    assert isinstance(res.reason, str)
    assert len(res.reason) > 0


def test_unverifiable_capability_never_fails_open():
    """Verify permission or measurement errors return UNVERIFIABLE and never True (FLEET_RULE 6)."""
    from bulk_downloader.mptcp_subflow import (
        MptcpCapabilityState,
        MptcpSubflowNegotiator,
    )

    negotiator = MptcpSubflowNegotiator()

    with (
        patch("os.path.exists", return_value=True),
        patch("builtins.open", side_effect=PermissionError("EACCES /proc/sys/net/mptcp/enabled")),
    ):
        res = negotiator.check_capability(force_refresh=True)
        assert res.state == MptcpCapabilityState.UNVERIFIABLE
        assert "permission denied" in res.reason
        # is_kernel_supported must be False when unverifiable (fail closed)
        assert negotiator.is_kernel_supported() is False



def test_socket_creation_and_rfc8684_fallback():
    """Verify socket creation attempts MPTCP and falls back to standard TCP on failure."""
    from bulk_downloader.mptcp_subflow import MptcpSubflowNegotiator

    negotiator = MptcpSubflowNegotiator()
    sock, is_mptcp, cap = negotiator.create_socket(family=socket.AF_INET, type=socket.SOCK_STREAM)
    try:
        assert isinstance(sock, socket.socket)
        assert isinstance(is_mptcp, bool)
        assert cap.state in ("supported", "unsupported", "unverifiable")
    finally:
        sock.close()


def test_subflow_negotiation_and_registration():
    """Verify primary connection establishment and secondary subflow negotiation."""
    from bulk_downloader.mptcp_subflow import MptcpSubflowNegotiator, MptcpSubflowState

    negotiator = MptcpSubflowNegotiator()
    conn_id = "conn-8684"
    stats = negotiator.register_connection(
        conn_id=conn_id,
        local_addr=("192.168.1.10", 54321),
        remote_addr=("93.184.216.34", 443),
    )
    assert stats.conn_id == conn_id
    assert stats.subflow_count == 1
    assert stats.subflows[0].subflow_id == 1
    assert stats.subflows[0].state == MptcpSubflowState.ESTABLISHED
    assert stats.subflows[0].is_backup is False

    # Negotiate secondary subflow across secondary interface
    subflow2 = negotiator.add_subflow(
        conn_id=conn_id,
        local_addr=("10.0.0.15", 54322),
        remote_addr=("93.184.216.34", 443),
        is_backup=True,
    )
    assert subflow2 is not None
    assert subflow2.subflow_id == 2
    assert subflow2.is_backup is True

    updated_stats = negotiator.get_connection_stats(conn_id)
    assert updated_stats is not None
    assert updated_stats.subflow_count == 2


def test_subflow_backup_priority_toggle():
    """Verify RFC 8684 MP_PRIO backup path signaling and toggling."""
    from bulk_downloader.mptcp_subflow import MptcpSubflowNegotiator

    negotiator = MptcpSubflowNegotiator()
    conn_id = "conn-prio"
    negotiator.register_connection(
        conn_id=conn_id,
        local_addr=("192.168.1.10", 40001),
        remote_addr=("1.1.1.1", 443),
    )
    sub2 = negotiator.add_subflow(
        conn_id=conn_id,
        local_addr=("10.0.0.2", 40002),
        remote_addr=("1.1.1.1", 443),
        is_backup=False,
    )
    assert sub2.is_backup is False

    # Toggle to backup subflow
    ok = negotiator.set_subflow_backup(conn_id=conn_id, subflow_id=sub2.subflow_id, is_backup=True)
    assert ok is True

    stats = negotiator.get_connection_stats(conn_id)
    assert stats is not None
    sub2_stat = next(s for s in stats.subflows if s.subflow_id == sub2.subflow_id)
    assert sub2_stat.is_backup is True


def test_subflow_telemetry_and_metrics():
    """Verify per-subflow byte throughput and RTT accounting."""
    from bulk_downloader.mptcp_subflow import MptcpSubflowNegotiator

    negotiator = MptcpSubflowNegotiator()
    conn_id = "conn-telemetry"
    negotiator.register_connection(
        conn_id=conn_id,
        local_addr=("192.168.1.10", 50001),
        remote_addr=("8.8.8.8", 443),
    )
    negotiator.record_subflow_io(
        conn_id=conn_id,
        subflow_id=1,
        bytes_sent=1024,
        bytes_recv=65536,
        rtt_us=12500.0,
    )

    stats = negotiator.get_connection_stats(conn_id)
    assert stats is not None
    sf = stats.subflows[0]
    assert sf.bytes_sent == 1024
    assert sf.bytes_received == 65536
    assert sf.rtt_us == 12500.0


def test_thread_safety():
    """Verify concurrent subflow registration and telemetry updates."""
    from bulk_downloader.mptcp_subflow import MptcpSubflowNegotiator

    negotiator = MptcpSubflowNegotiator()
    conn_id = "conn-concurrent"
    negotiator.register_connection(
        conn_id=conn_id,
        local_addr=("192.168.1.10", 60000),
        remote_addr=("8.8.8.8", 443),
    )

    def worker(worker_id: int):
        for _ in range(20):
            negotiator.record_subflow_io(
                conn_id=conn_id,
                subflow_id=1,
                bytes_sent=100,
                bytes_recv=500,
                rtt_us=1000.0 + (worker_id * 10),
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = negotiator.get_connection_stats(conn_id)
    assert stats is not None
    sf = stats.subflows[0]
    assert sf.bytes_sent == 8 * 20 * 100
    assert sf.bytes_received == 8 * 20 * 500


def test_multi_conn_download_exercises_mptcp_caller(tmp_path):
    """Production caller test: multi_conn.download() exercises MPTCP negotiation."""
    from bulk_downloader import multi_conn
    from bulk_downloader.mptcp_subflow import (
        MptcpCapabilityResult,
        MptcpCapabilityState,
    )

    # Assert caller integration seam is exposed on multi_conn
    assert hasattr(multi_conn, "get_mptcp_negotiator")
    negotiator = multi_conn.get_mptcp_negotiator()

    mock_cap = MptcpCapabilityResult(
        state=MptcpCapabilityState.SUPPORTED,
        reason="mock test supported",
        kernel_mptcp_enabled=True,
    )

    out_file = str(tmp_path / "test_mptcp.bin")

    with (
        patch.object(negotiator, "check_capability", return_value=mock_cap),
        patch.object(multi_conn, "_allocate_sparse_file", return_value=True),
        patch.object(multi_conn, "_download_chunk", return_value=(True, 1024, "")),
    ):
        res = multi_conn.download(
            url="https://example.com/test.bin",
            output_path=out_file,
            content_length=2048,
            chunk_count=2,
        )
        assert res.ok is True
        assert res.mptcp_capability == "supported"
        assert res.mptcp_conn_id.startswith("mc-")
        # Verify MPTCP connection and subflows were registered through download()
        conn_stats = negotiator.get_connection_stats(res.mptcp_conn_id)
        assert conn_stats is not None
        assert conn_stats.mptcp_enabled is True
        assert conn_stats.subflow_count >= 1


def test_multi_conn_download_unverifiable_fails_closed(tmp_path):
    """Verify multi_conn.download() handles UNVERIFIABLE MPTCP capability safely without failing open."""
    from bulk_downloader import multi_conn
    from bulk_downloader.mptcp_subflow import (
        MptcpCapabilityResult,
        MptcpCapabilityState,
    )

    negotiator = multi_conn.get_mptcp_negotiator()
    mock_unverifiable = MptcpCapabilityResult(
        state=MptcpCapabilityState.UNVERIFIABLE,
        reason="permission denied probing mptcp",
        kernel_mptcp_enabled=None,
    )

    out_file = str(tmp_path / "test_unverifiable.bin")

    with (
        patch.object(negotiator, "check_capability", return_value=mock_unverifiable),
        patch.object(multi_conn, "_allocate_sparse_file", return_value=True),
        patch.object(multi_conn, "_download_chunk", return_value=(True, 1024, "")),
    ):
        res = multi_conn.download(
            url="https://example.com/test.bin",
            output_path=out_file,
            content_length=1024,
            chunk_count=1,
        )
        assert res.ok is True
        assert res.mptcp_capability == "unverifiable"
        # Subflows must NOT be registered under unverifiable state
        assert res.mptcp_conn_id == ""

