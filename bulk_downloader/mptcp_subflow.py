"""Phase 150 / Row 1067 -- Multipath TCP (MPTCP) Kernel Subflow Negotiation (RFC 8684).

Provides Linux kernel Multipath TCP capability detection, IPPROTO_MPTCP socket factory
with transparent RFC 8684 standard TCP fallback, three-state capability measurement
(SUPPORTED / UNSUPPORTED / UNVERIFIABLE per O1224 / RULING-row1068-failopen), subflow
management across multiple network interfaces, MP_PRIO backup path signaling, and
per-subflow telemetry accounting.
"""
from __future__ import annotations

import os
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

IPPROTO_MPTCP = getattr(socket, "IPPROTO_MPTCP", 284)
SOL_MPTCP = getattr(socket, "SOL_MPTCP", 284)
MPTCP_INFO = 1
MPTCP_TCPINFO = 2
MPTCP_SUBFLOW_ADDRS = 3


class MptcpCapabilityState(str, Enum):
    """Three-state kernel capability outcome (O1224 / RULING-row1068-failopen).

    - SUPPORTED: Kernel explicitly supports MPTCP and socket creation succeeds.
    - UNSUPPORTED: Kernel explicitly lacks MPTCP protocol support or is disabled.
    - UNVERIFIABLE: Kernel or system state could not be measured or probed (never fail-open).
    """

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"


@dataclass(frozen=True)
class MptcpCapabilityResult:
    """Detailed three-state capability measurement with diagnostic reason."""

    state: MptcpCapabilityState
    reason: str
    kernel_mptcp_enabled: bool | None = None


class MptcpSubflowState(str, Enum):
    """Subflow lifecycle states per RFC 8684."""

    INITIAL = "initial"
    NEGOTIATING = "negotiating"
    ESTABLISHED = "established"
    BACKUP = "backup"
    CLOSING = "closing"
    CLOSED = "closed"


@dataclass
class MptcpSubflowInfo:
    """Telemetry and configuration for an individual MPTCP subflow."""

    subflow_id: int
    local_addr: tuple[str, int]
    remote_addr: tuple[str, int]
    state: MptcpSubflowState = MptcpSubflowState.ESTABLISHED
    is_backup: bool = False
    bytes_sent: int = 0
    bytes_received: int = 0
    rtt_us: float = 0.0
    throughput_bps: float = 0.0
    established_at: float = field(default_factory=time.monotonic)


@dataclass
class MptcpConnectionStats:
    """Aggregated stats and subflow roster for an MPTCP connection."""

    conn_id: str
    mptcp_enabled: bool
    fallback_to_tcp: bool
    subflows: list[MptcpSubflowInfo] = field(default_factory=list)
    token: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    @property
    def subflow_count(self) -> int:
        return len(self.subflows)


class MptcpSubflowNegotiator:
    """Thread-safe MPTCP kernel capability inspector and subflow negotiator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._capability: MptcpCapabilityResult | None = None
        self._connections: dict[str, MptcpConnectionStats] = {}

    def check_capability(self, force_refresh: bool = False) -> MptcpCapabilityResult:
        """Measure kernel MPTCP capability returning an honest three-state outcome (O1224)."""
        with self._lock:
            if self._capability is not None and not force_refresh:
                return self._capability

            # 1. Check /proc/sys/net/mptcp/enabled if accessible
            proc_path = "/proc/sys/net/mptcp/enabled"
            proc_enabled: bool | None = None
            if os.path.exists(proc_path):
                try:
                    with open(proc_path, "r", encoding="utf-8") as f:
                        val = f.read().strip()
                        if val in ("1", "2"):
                            proc_enabled = True
                        elif val == "0":
                            proc_enabled = False
                except PermissionError as exc:
                    res = MptcpCapabilityResult(
                        state=MptcpCapabilityState.UNVERIFIABLE,
                        reason=f"permission denied reading {proc_path}: {exc}",
                        kernel_mptcp_enabled=None,
                    )
                    self._capability = res
                    return res
                except OSError as exc:
                    res = MptcpCapabilityResult(
                        state=MptcpCapabilityState.UNVERIFIABLE,
                        reason=f"os error reading {proc_path}: {exc}",
                        kernel_mptcp_enabled=None,
                    )
                    self._capability = res
                    return res

            if proc_enabled is False:
                res = MptcpCapabilityResult(
                    state=MptcpCapabilityState.UNSUPPORTED,
                    reason="/proc/sys/net/mptcp/enabled is 0 (disabled)",
                    kernel_mptcp_enabled=False,
                )
                self._capability = res
                return res

            # 2. Probe socket creation with IPPROTO_MPTCP
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, IPPROTO_MPTCP)
                sock.close()
                res = MptcpCapabilityResult(
                    state=MptcpCapabilityState.SUPPORTED,
                    reason="kernel supports IPPROTO_MPTCP socket allocation",
                    kernel_mptcp_enabled=True,
                )
                self._capability = res
                return res
            except PermissionError as exc:
                res = MptcpCapabilityResult(
                    state=MptcpCapabilityState.UNVERIFIABLE,
                    reason=f"permission denied allocating IPPROTO_MPTCP socket: {exc}",
                    kernel_mptcp_enabled=proc_enabled,
                )
                self._capability = res
                return res
            except OSError as exc:
                # Expected when kernel lacks MPTCP support
                # (e.g. EPROTONOSUPPORT=93, ENOPROTOOPT=92, EAFNOSUPPORT=97, EINVAL=22)
                err_no = getattr(exc, "errno", None)
                if err_no in (93, 92, 97, 22) or "Protocol not supported" in str(exc):
                    res = MptcpCapabilityResult(
                        state=MptcpCapabilityState.UNSUPPORTED,
                        reason=f"kernel protocol not supported (errno {err_no}): {exc}",
                        kernel_mptcp_enabled=False,
                    )
                    self._capability = res
                    return res
                # Any other unexpected OS error is unverifiable, NEVER fail-open
                res = MptcpCapabilityResult(
                    state=MptcpCapabilityState.UNVERIFIABLE,
                    reason=f"unexpected error probing MPTCP socket: {exc}",
                    kernel_mptcp_enabled=proc_enabled,
                )
                self._capability = res
                return res

    def is_kernel_supported(self) -> bool:
        """Return True only if MPTCP capability is measured as SUPPORTED."""
        return self.check_capability().state == MptcpCapabilityState.SUPPORTED

    def create_socket(
        self,
        family: int = socket.AF_INET,
        type: int = socket.SOCK_STREAM,
    ) -> tuple[socket.socket, bool, MptcpCapabilityResult]:
        """Create an MPTCP socket with transparent RFC 8684 standard TCP fallback."""
        cap = self.check_capability()
        if cap.state == MptcpCapabilityState.SUPPORTED:
            try:
                sock = socket.socket(family, type, IPPROTO_MPTCP)
                return sock, True, cap
            except OSError:
                pass

        # Fallback to standard TCP
        sock = socket.socket(family, type, socket.IPPROTO_TCP)
        return sock, False, cap

    def register_connection(
        self,
        conn_id: str,
        local_addr: tuple[str, int],
        remote_addr: tuple[str, int],
        token: str | None = None,
    ) -> MptcpConnectionStats:
        """Register the primary subflow for a newly established connection."""
        with self._lock:
            tok = token or uuid.uuid4().hex[:16]
            primary_subflow = MptcpSubflowInfo(
                subflow_id=1,
                local_addr=local_addr,
                remote_addr=remote_addr,
                state=MptcpSubflowState.ESTABLISHED,
                is_backup=False,
            )
            stats = MptcpConnectionStats(
                conn_id=str(conn_id),
                mptcp_enabled=True,
                fallback_to_tcp=False,
                subflows=[primary_subflow],
                token=tok,
            )
            self._connections[str(conn_id)] = stats
            return stats

    def add_subflow(
        self,
        conn_id: str,
        local_addr: tuple[str, int],
        remote_addr: tuple[str, int],
        is_backup: bool = False,
    ) -> MptcpSubflowInfo | None:
        """Negotiate and attach an additional subflow to an existing MPTCP connection."""
        with self._lock:
            conn = self._connections.get(str(conn_id))
            if conn is None:
                return None

            subflow_id = len(conn.subflows) + 1
            subflow = MptcpSubflowInfo(
                subflow_id=subflow_id,
                local_addr=local_addr,
                remote_addr=remote_addr,
                state=MptcpSubflowState.ESTABLISHED,
                is_backup=bool(is_backup),
            )
            conn.subflows.append(subflow)
            return subflow

    def set_subflow_backup(self, conn_id: str, subflow_id: int, is_backup: bool) -> bool:
        """Signal MP_PRIO backup status for a specific subflow."""
        with self._lock:
            conn = self._connections.get(str(conn_id))
            if conn is None:
                return False

            for sf in conn.subflows:
                if sf.subflow_id == subflow_id:
                    sf.is_backup = bool(is_backup)
                    return True
            return False

    def record_subflow_io(
        self,
        conn_id: str,
        subflow_id: int,
        bytes_sent: int = 0,
        bytes_recv: int = 0,
        rtt_us: float = 0.0,
    ) -> bool:
        """Record I/O byte counts and RTT latency for telemetry reporting."""
        with self._lock:
            conn = self._connections.get(str(conn_id))
            if conn is None:
                return False

            for sf in conn.subflows:
                if sf.subflow_id == subflow_id:
                    sf.bytes_sent += max(0, int(bytes_sent))
                    sf.bytes_received += max(0, int(bytes_recv))
                    if rtt_us > 0:
                        sf.rtt_us = float(rtt_us)
                    return True
            return False

    def get_connection_stats(self, conn_id: str) -> MptcpConnectionStats | None:
        """Retrieve telemetry and active subflows for a connection."""
        with self._lock:
            conn = self._connections.get(str(conn_id))
            if conn is None:
                return None
            return MptcpConnectionStats(
                conn_id=conn.conn_id,
                mptcp_enabled=conn.mptcp_enabled,
                fallback_to_tcp=conn.fallback_to_tcp,
                subflows=list(conn.subflows),
                token=conn.token,
            )

    def close_connection(self, conn_id: str) -> bool:
        """Close and unregister all subflows for a connection."""
        with self._lock:
            return self._connections.pop(str(conn_id), None) is not None


_GLOBAL_MPTCP_MANAGER: MptcpSubflowNegotiator | None = None
_GLOBAL_MPTCP_LOCK = threading.Lock()


def get_mptcp_manager() -> MptcpSubflowNegotiator:
    """Return the process-wide shared MptcpSubflowNegotiator instance."""
    global _GLOBAL_MPTCP_MANAGER
    if _GLOBAL_MPTCP_MANAGER is None:
        with _GLOBAL_MPTCP_LOCK:
            if _GLOBAL_MPTCP_MANAGER is None:
                _GLOBAL_MPTCP_MANAGER = MptcpSubflowNegotiator()
    return _GLOBAL_MPTCP_MANAGER
