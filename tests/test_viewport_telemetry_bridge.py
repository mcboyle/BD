"""Row 955: OUT-OF-BAND-REMOTE-VIEWPORT-STREAMING-AND-DIAGNOSTIC-CONSOLE-BRIDGE

Tests for bulk_downloader/viewport_telemetry_bridge.py.
Verifies:
(1) Framebuffer stream encoding
(2) Bidirectional telemetry channel integrity
(3) Administrative resume signal acknowledgement
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

try:
    from bulk_downloader.viewport_telemetry_bridge import (
        FrameEncoder,
        TelemetryChannel,
        ViewportTelemetryBridge,
        encode_frame,
    )
except ImportError:
    FrameEncoder = None
    TelemetryChannel = None
    ViewportTelemetryBridge = None
    encode_frame = None

BD_GATE_SCOPE = "module"


# ---------------------------------------------------------------------------
# Behavioral RED
# ---------------------------------------------------------------------------

def test_behavioral_red_no_framebuffer_streaming():
    """Behavioral RED: without the bridge, there is no framebuffer
    stream encoding for remote viewport inspection."""
    raw_pixels = b"\x00\xff\x00" * 100
    assert len(raw_pixels) == 300

    if encode_frame is None:
        encoded = None
        assert encoded is not None, (
            "BEHAVIORAL RED: no framebuffer stream encoding exists; "
            "encode_frame returned None (assert None is not None)"
        )

    result = encode_frame(raw_pixels, width=10, height=10, fmt="rgb24")
    assert result is not None
    assert len(result.data) > 0


# ---------------------------------------------------------------------------
# (1) Framebuffer stream encoding
# ---------------------------------------------------------------------------

def test_encode_frame_rgb24():
    """Encode raw RGB24 framebuffer to transmittable format."""
    assert encode_frame is not None
    pixels = b"\xff\x00\x00" * 64  # 8x8 red frame
    result = encode_frame(pixels, width=8, height=8, fmt="rgb24")
    assert result.width == 8
    assert result.height == 8
    assert result.fmt == "rgb24"
    assert len(result.data) > 0


def test_encode_frame_rgba32():
    """Encode RGBA32 framebuffer."""
    assert encode_frame is not None
    pixels = b"\xff\x00\x00\xff" * 16  # 4x4 red+alpha
    result = encode_frame(pixels, width=4, height=4, fmt="rgba32")
    assert result.width == 4
    assert result.height == 4
    assert result.fmt == "rgba32"


def test_encode_frame_preserves_sequence():
    """Frame sequence numbers increment monotonically."""
    assert FrameEncoder is not None
    encoder = FrameEncoder()
    frames = []
    for i in range(5):
        pixels = bytes([i % 256]) * 12  # 2x2 rgb
        frames.append(encoder.encode(pixels, width=2, height=2, fmt="rgb24"))
    seqs = [f.sequence for f in frames]
    assert seqs == list(range(5))


def test_encode_frame_invalid_dimensions_raises():
    """Zero or negative dimensions raise ValueError."""
    assert encode_frame is not None
    with pytest.raises(ValueError):
        encode_frame(b"\x00" * 12, width=0, height=2, fmt="rgb24")
    with pytest.raises(ValueError):
        encode_frame(b"\x00" * 12, width=2, height=-1, fmt="rgb24")


# ---------------------------------------------------------------------------
# (2) Bidirectional telemetry channel integrity
# ---------------------------------------------------------------------------

def test_channel_send_receive():
    """Messages sent on a channel are received in order."""
    assert TelemetryChannel is not None
    ch = TelemetryChannel()

    ch.send({"type": "frame", "seq": 0})
    ch.send({"type": "frame", "seq": 1})
    ch.send({"type": "console", "text": "debug info"})

    msgs = ch.receive_all()
    assert len(msgs) == 3
    assert msgs[0]["type"] == "frame"
    assert msgs[0]["seq"] == 0
    assert msgs[2]["type"] == "console"


def test_channel_bidirectional():
    """Both sides of the channel can send and receive."""
    assert TelemetryChannel is not None
    server = TelemetryChannel()
    client = TelemetryChannel()

    server.send({"type": "frame_update", "data": "abc"})
    client.send({"type": "admin_command", "cmd": "resume"})

    server_msgs = server.receive_all()
    client_msgs = client.receive_all()

    assert len(server_msgs) == 1
    assert server_msgs[0]["type"] == "frame_update"
    assert len(client_msgs) == 1
    assert client_msgs[0]["type"] == "admin_command"


def test_channel_empty_receive():
    """Receiving from empty channel returns empty list."""
    assert TelemetryChannel is not None
    ch = TelemetryChannel()
    assert ch.receive_all() == []


# ---------------------------------------------------------------------------
# (3) Administrative resume signal acknowledgement
# ---------------------------------------------------------------------------

def test_bridge_resume_signal():
    """Admin resume signal is acknowledged and pipeline state transitions."""
    assert ViewportTelemetryBridge is not None
    bridge = ViewportTelemetryBridge(port=6080)
    assert bridge.is_paused is False

    bridge.pause_pipeline("rendering anomaly detected")
    assert bridge.is_paused is True
    assert bridge.pause_reason == "rendering anomaly detected"

    ack = bridge.resume_pipeline(admin_id="operator-1")
    assert ack["acknowledged"] is True
    assert ack["admin_id"] == "operator-1"
    assert bridge.is_paused is False


def test_bridge_resume_when_not_paused():
    """Resume on non-paused bridge returns no-op acknowledgement."""
    assert ViewportTelemetryBridge is not None
    bridge = ViewportTelemetryBridge(port=6080)
    ack = bridge.resume_pipeline(admin_id="operator-1")
    assert ack["acknowledged"] is True
    assert ack.get("was_paused") is False


def test_bridge_stream_frame():
    """Bridge streams encoded frames to telemetry channel."""
    assert ViewportTelemetryBridge is not None
    bridge = ViewportTelemetryBridge(port=6080)
    pixels = b"\x80\x80\x80" * 4  # 2x2 grey
    bridge.stream_frame(pixels, width=2, height=2)

    msgs = bridge.channel.receive_all()
    assert len(msgs) == 1
    assert msgs[0]["type"] == "frame"
    assert msgs[0]["width"] == 2


# ---------------------------------------------------------------------------
# Negative control
# ---------------------------------------------------------------------------

def test_negative_control_encoding_and_channel_exact_counts():
    """Negative control: prove encoding produces frames and channel delivers
    them; exact count verification with nonzero fixture."""
    assert encode_frame is not None
    assert TelemetryChannel is not None

    frames_encoded = 0
    ch = TelemetryChannel()
    for i in range(4):
        pixels = bytes([i * 60 % 256]) * 12
        f = encode_frame(pixels, width=2, height=2, fmt="rgb24")
        assert f is not None
        ch.send({"type": "frame", "seq": f.sequence, "data_len": len(f.data)})
        frames_encoded += 1

    assert frames_encoded == 4, f"Expected 4 frames encoded, got {frames_encoded}"

    msgs = ch.receive_all()
    assert len(msgs) == 4, f"Expected 4 messages received, got {len(msgs)}"

    # Prove negative: empty channel has 0
    ch2 = TelemetryChannel()
    assert len(ch2.receive_all()) == 0, "Empty channel must return 0 messages"
