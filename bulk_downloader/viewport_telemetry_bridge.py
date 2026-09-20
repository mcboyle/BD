"""viewport_telemetry_bridge -- remote viewport streaming and diagnostic console.

Row 955 (v3.66.1585): stream browser framebuffer updates via a telemetry
channel to a local management console, providing administrative inspection
and operator-signaled session pipeline resumption.
"""
from __future__ import annotations

import base64
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


class FrameEncoder:
    def __init__(self) -> None:
        self._seq = 0

    def encode(
        self, pixels: bytes, *, width: int, height: int, fmt: str = "rgb24",
    ) -> EncodedFrame:
        if width <= 0 or height <= 0:
            raise ValueError(f"Invalid dimensions: {width}x{height}")
        compressed = zlib.compress(pixels, level=1)
        frame = EncodedFrame(
            data=compressed,
            width=width,
            height=height,
            fmt=fmt,
            sequence=self._seq,
        )
        self._seq += 1
        return frame


class TelemetryChannel:
    def __init__(self) -> None:
        self._inbox: deque[dict] = deque()

    def send(self, message: dict) -> None:
        self._inbox.append(message)

    def receive_all(self) -> List[dict]:
        msgs = list(self._inbox)
        self._inbox.clear()
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
