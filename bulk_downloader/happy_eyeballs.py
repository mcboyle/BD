"""Row 867: RFC 8305-style synchronous TCP race for the pinned transport's
httpcore backend.

``race_connect`` takes literals that the transport has ALREADY vetted
(``PinnedTransport.pin`` validates every DNS answer once, row 703) -- it never
resolves a name itself, so the socket layer only ever sees vetted literals.
IPv6 starts first; IPv4 starts after ``HEAD_START_SECONDS`` or as soon as the
IPv6 attempt fails, whichever is sooner.  The first stream to connect wins; a
late winner is closed, and a loser that is still hanging on a black-holed
route is left to its own timeout on a daemon thread -- the caller is never
joined to it (the whole point: a black-holed v6 route must not stall v4).
"""
from __future__ import annotations

import queue
import threading
from typing import Callable, Optional, Sequence

HEAD_START_SECONDS = 0.250

# (ok, payload): payload is the stream on success, the OSError on failure
_Outcome = tuple[bool, object]


def race_connect(candidates: Sequence[str], port: int, *,
                 connect: Callable[[str, int, Optional[float]], object],
                 timeout: Optional[float] = None,
                 head_start: float = HEAD_START_SECONDS):
    """Return the first stream ``connect(ip, port, timeout)`` yields for one
    of ``candidates`` (vetted literals, IPv6 first).  Raises the last OSError
    when every attempt fails; a non-OSError from ``connect`` is its bug and
    propagates as-is (never treated as a lost race)."""
    if not candidates:
        raise OSError("no vetted address to connect to")
    if len(candidates) == 1:
        return connect(candidates[0], port, timeout)

    results: "queue.Queue[tuple[str, _Outcome]]" = queue.Queue()
    won = threading.Event()

    def attempt(ip: str) -> None:
        try:
            stream = connect(ip, port, timeout)
        except OSError as exc:
            results.put((ip, (False, exc)))
            return
        except BaseException as exc:  # a transport bug: surface it, do not swallow
            results.put((ip, (False, exc)))
            return
        if won.is_set():
            # a sibling already won while we were connecting: do not leak the socket
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except OSError:
                    pass
            return
        results.put((ip, (True, stream)))

    def start(ip: str) -> None:
        threading.Thread(target=attempt, args=(ip,), name=f"happy-eyeballs-{ip}",
                         daemon=True).start()

    pending = list(candidates)
    start(pending.pop(0))
    in_flight = 1
    last_error: Optional[BaseException] = None
    while in_flight:
        try:
            # the next candidate starts after the head start unless the
            # running one reports first
            ip, (ok, payload) = results.get(timeout=head_start if pending else None)
        except queue.Empty:
            start(pending.pop(0))
            in_flight += 1
            continue
        in_flight -= 1
        if ok:
            won.set()
            return payload
        last_error = payload  # type: ignore[assignment]
        if not isinstance(payload, OSError):
            raise payload  # type: ignore[misc]
        if pending:
            start(pending.pop(0))
            in_flight += 1
    raise last_error if last_error is not None else OSError("all raced connections failed")
