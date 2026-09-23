"""Row 1066 -- ask the kernel to pace a socket, and report honestly when it will not.

``traffic_shaper.BandwidthShaper`` (Row 892) is a token bucket over the READ loop: it slows how
fast BD consumes a stream, which throttles the peer only indirectly, through the TCP receive
window, and only after the bytes have already crossed the wire. It cannot smooth BD's own egress,
and it cannot burst-protect a link, because by the time userspace declines to read, the packets
have arrived.

Linux can do the other half in the kernel. ``SO_MAX_PACING_RATE`` hands the socket a byte-per-
second ceiling that the fair-queue qdisc enforces at transmit time, spacing packets instead of
letting them burst. It is one ``setsockopt``, and the whole difficulty is that it may quietly not
be available -- the constant is missing from CPython's ``socket`` module, the option is Linux-only,
and a container can refuse it.

That is why every function here returns a REPORT rather than a bool, and why the rate is read back
with ``getsockopt`` instead of assumed. A pacing call that silently failed would leave a caller
believing its egress was shaped: a throttle that does not throttle is worse than no throttle,
because nobody goes looking for it.

NOT IN THIS MODULE: the eBPF EDT (Earliest Departure Time) scheduler half of row 1066's title. It
needs a privileged loader, a compiled BPF object and a kernel this test suite cannot assume, so it
cannot be built or verified here.
"""
from __future__ import annotations

import math
import socket
from typing import Any, Dict, Optional

from .traffic_shaper import BYTES_PER_MBIT

#: Linux ``SO_MAX_PACING_RATE`` from ``include/uapi/asm-generic/socket.h``. CPython's ``socket``
#: module does not export it, so it is named here once rather than inlined at a call site. The
#: value is architecture-independent on every Linux port that defines it.
SO_MAX_PACING_RATE = 47

#: The kernel's own "no ceiling" for the option: the all-ones value a fresh socket holds (~0U).
#: ``setsockopt(level, opt, int)`` passes a C int, so it is written -- and reads back -- as -1.
#: NOT 0: 0 is a ceiling of zero bytes per second. TCP's own pacing happens to skip a zero rate,
#: but 0 is not what a fresh socket holds, and this module does not assume what it does elsewhere.
UNLIMITED = -1

#: The largest rate this module hands the kernel. ``setsockopt(level, opt, int)`` passes a C int,
#: so CPython refuses 2**31 and above with a TypeError before any syscall is made.
LARGEST_RATE = 2**31 - 1


def rate_from_mbps(mbps: float) -> int:
    """Megabits per second -> bytes per second, using the shaper's constant.

    Imported from ``traffic_shaper`` rather than restated: two definitions of a megabit is exactly
    how a rate limit comes to mean two different things in one process. The runner's own cap is a
    different unit, MEGABYTES per second, converted where the runner reads it
    (``runner_transport._hls_download_guarded``, x 1024 x 1024): two units, each named at its
    entry point, never two definitions of one.

    Anything that is not a finite positive rate is 0 ("no rate"): NaN, infinity, and a value so
    large that the product overflows. ``int()`` of an infinite float raises OverflowError, and a
    converter that crashes on "no limit" is worse than one that answers there is none.
    """
    try:
        value = float(mbps)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(value):
        return 0
    rate = value * BYTES_PER_MBIT
    return int(rate) if value > 0 and math.isfinite(rate) else 0


def _report(applied: bool, rate: int, reason: Optional[str] = None,
            verified: Optional[int] = None) -> Dict[str, Any]:
    return {"applied": applied, "rate_bytes_per_s": rate,
            "verified_bytes_per_s": verified, "reason": reason}


def pacing_supported(sock: Any) -> bool:
    """Whether this socket will accept a pacing rate, probed by setting one.

    Probed, never inferred from ``sys.platform``: a Linux kernel with the option compiled out, or
    a sandbox that filters the setsockopt, is exactly the case a platform check gets wrong. The
    probe writes back the value the socket already holds, read first, so it changes nothing.
    Writing UNLIMITED instead would silently lift a ceiling already set on the socket; a socket
    whose rate cannot be read is answered False rather than probed blind.
    """
    try:
        held = sock.getsockopt(socket.SOL_SOCKET, SO_MAX_PACING_RATE)
        sock.setsockopt(socket.SOL_SOCKET, SO_MAX_PACING_RATE, held)
        return True
    except Exception:                      # noqa: BLE001 -- an unsupported option is DATA
        return False


def set_pacing_rate(sock: Any, bytes_per_second: Any) -> Dict[str, Any]:
    """Ask the kernel to pace *sock* at *bytes_per_second*; report what actually happened.

    A rate that is not a positive whole number of bytes is refused BEFORE the syscall, so a typo
    cannot reach the kernel and be interpreted as something else. ``bool`` is rejected with it:
    ``True`` is 1 byte per second, which is a plausible-looking way to stall a transfer. So is a
    rate above LARGEST_RATE: the kernel is never asked for it, and the reason says so rather
    than passing off CPython's TypeError as the kernel's answer.
    """
    if isinstance(bytes_per_second, bool) or not isinstance(bytes_per_second, int):
        return _report(False, 0, f"pacing rate must be a whole number of bytes, got "
                                 f"{type(bytes_per_second).__name__}")
    if bytes_per_second <= 0:
        return _report(False, 0, f"pacing rate must be positive, got {bytes_per_second}; "
                                 f"call clear_pacing() to remove a ceiling")
    if bytes_per_second > LARGEST_RATE:
        return _report(False, bytes_per_second,
                       f"pacing rate is above {LARGEST_RATE} B/s, the most setsockopt carries "
                       f"for SO_MAX_PACING_RATE; the kernel was not asked, the socket keeps "
                       f"its rate")
    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_MAX_PACING_RATE, bytes_per_second)
    except Exception as exc:               # noqa: BLE001 -- see module docstring: never silent
        return _report(False, bytes_per_second, "%s: %s" % (type(exc).__name__, exc))
    return _report(True, bytes_per_second, None, _read_back(sock))


def clear_pacing(sock: Any) -> Dict[str, Any]:
    """Remove any ceiling from *sock*, and report it.

    Writes the kernel's UNLIMITED, so the socket is left holding what a fresh socket holds.
    """
    try:
        sock.setsockopt(socket.SOL_SOCKET, SO_MAX_PACING_RATE, UNLIMITED)
    except Exception as exc:               # noqa: BLE001
        return _report(False, UNLIMITED, "%s: %s" % (type(exc).__name__, exc))
    return _report(True, UNLIMITED, None, _read_back(sock))


def _read_back(sock: Any) -> Optional[int]:
    """The rate the kernel says it is holding, or None when it cannot be read.

    None is a real answer here. The set succeeded; what is unknown is whether the value survived,
    and a caller comparing this to what it asked for must be able to tell "unknown" from "wrong".
    """
    try:
        value = sock.getsockopt(socket.SOL_SOCKET, SO_MAX_PACING_RATE)
    except Exception:                      # noqa: BLE001
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None
