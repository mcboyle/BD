"""viewport_telemetry_bridge -- remote viewport streaming and diagnostic console.

Row 955 (v3.66.1585): stream browser framebuffer updates via a telemetry
channel to a local management console, providing administrative inspection
and operator-signaled session pipeline resumption.
"""
from __future__ import annotations

import base64
import itertools
import zlib
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EncodedFrame:
    data: bytes
    width: int
    height: int
    fmt: str
    sequence: int


# Bytes per pixel of each frame format the console can decode.
_BYTES_PER_PIXEL = {"rgb24": 3, "rgba32": 4}
# Undrained telemetry is bounded: past this many messages the oldest are dropped.
_INBOX_MAXLEN = 4096


class FrameEncoder:
    def __init__(self) -> None:
        # next() on a count is atomic, so concurrent encode() calls never share a sequence.
        self._seq = itertools.count()

    def encode(
        self, pixels: bytes, *, width: int, height: int, fmt: str = "rgb24",
    ) -> EncodedFrame:
        if (isinstance(width, bool) or isinstance(height, bool)
                or not isinstance(width, int) or not isinstance(height, int)
                or width <= 0 or height <= 0):
            raise ValueError(f"Invalid dimensions: {width!r}x{height!r}")
        bpp = _BYTES_PER_PIXEL.get(fmt)
        if bpp is None:
            raise ValueError(f"Unknown frame format: {fmt!r}")
        if len(pixels) != width * height * bpp:
            raise ValueError(
                f"Pixel buffer is {len(pixels)} B; {width}x{height} {fmt} needs "
                f"{width * height * bpp} B")
        compressed = zlib.compress(pixels, level=1)
        return EncodedFrame(
            data=compressed,
            width=width,
            height=height,
            fmt=fmt,
            sequence=next(self._seq),
        )


class TelemetryChannel:
    def __init__(self) -> None:
        self._inbox: deque[dict] = deque(maxlen=_INBOX_MAXLEN)

    def send(self, message: dict) -> None:
        self._inbox.append(message)

    def receive_all(self) -> List[dict]:
        # popleft() is atomic: a message sent while draining is either taken
        # now or left for the next call, never cleared unseen.
        msgs: List[dict] = []
        for _ in range(len(self._inbox)):
            try:
                msgs.append(self._inbox.popleft())
            except IndexError:
                break
        return msgs


class ViewportTelemetryBridge:
    def __init__(self, *, port: int = 6080) -> None:
        self.port = port
        self.channel = TelemetryChannel()
        self.is_paused = False
        self.pause_reason: str = ""
        self._encoder = FrameEncoder()

    def stream_frame(
        self, pixels: bytes, *, width: int, height: int, fmt: str = "rgb24",
    ) -> EncodedFrame:
        frame = self._encoder.encode(pixels, width=width, height=height, fmt=fmt)
        self.channel.send({
            "type": "frame",
            "seq": frame.sequence,
            "width": frame.width,
            "height": frame.height,
            "fmt": frame.fmt,
            "data_len": len(frame.data),
        })
        return frame

    def pause_pipeline(self, reason: str) -> None:
        self.is_paused = True
        self.pause_reason = reason

    def resume_pipeline(self, *, admin_id: str) -> dict:
        if not isinstance(admin_id, str) or not admin_id.strip():
            # An unattributed resume is refused and the pipeline stays as it was.
            return {
                "acknowledged": False,
                "admin_id": admin_id,
                "was_paused": self.is_paused,
                "error": "admin_id required",
            }
        was_paused = self.is_paused
        self.is_paused = False
        self.pause_reason = ""
        return {
            "acknowledged": True,
            "admin_id": admin_id,
            "was_paused": was_paused,
        }


def encode_frame(
    pixels: bytes, *, width: int, height: int, fmt: str = "rgb24",
) -> EncodedFrame:
    return FrameEncoder().encode(pixels, width=width, height=height, fmt=fmt)
