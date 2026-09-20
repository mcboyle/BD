"""bulk_downloader.canvas_capture — Canvas and WebRTC frame capture fallback.

Provides in-browser video recording capabilities targeting HTML5 `<canvas>` and WebRTC `<video>`
elements via `HTMLCanvasElement.captureStream()` and `MediaRecorder`.
Encodes video streams into compliant Matroska (.mkv) or WebM (.webm) containers.

Key capabilities:
1. In-browser recorder injection script for canvas and WebRTC MediaStream capture.
2. Pure Python EBML header parsing and WebM/Matroska container validation.
3. Frame interval measurement and steady framerate stability analysis.
4. Deterministic synthetic WebM container generator for hermetic testing.
5. High-level `CanvasRecorder` controller for browser automation frameworks (Playwright).

Posture:
- Clean modular design with zero external third-party dependencies (standard library only).
- Strictly non-networked container validation and deterministic mathematical analysis.
- Adheres to Fleet Rule 21 (0 site logins, hermetic local fixture testing).
"""

from __future__ import annotations

import base64
import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# =====================================================================
# EBML / WebM / Matroska Constants & Specifications
# =====================================================================

# EBML Element IDs (Variable-length integers)
EBML_ID_HEADER = 0x1A45DFA3
EBML_ID_VERSION = 0x4286
EBML_ID_READ_VERSION = 0x42F7
EBML_ID_MAX_ID_LENGTH = 0x42F2
EBML_ID_MAX_SIZE_LENGTH = 0x42F3
EBML_ID_DOC_TYPE = 0x4282
EBML_ID_DOC_TYPE_VERSION = 0x4287
EBML_ID_DOC_TYPE_READ_VERSION = 0x4285

# Matroska / WebM Segment Elements
EBML_ID_SEGMENT = 0x18538067
EBML_ID_SEEK_HEAD = 0x114D9B74
EBML_ID_INFO = 0x1549A966
EBML_ID_TIMECODE_SCALE = 0x2AD7B1
EBML_ID_DURATION = 0x4489
EBML_ID_TRACKS = 0x1654AE6B
EBML_ID_TRACK_ENTRY = 0xAE
EBML_ID_TRACK_NUMBER = 0xD7
EBML_ID_TRACK_UID = 0x73C5
EBML_ID_TRACK_TYPE = 0x83
EBML_ID_CODEC_ID = 0x86
EBML_ID_VIDEO_SETTINGS = 0xE0
EBML_ID_PIXEL_WIDTH = 0xB0
EBML_ID_PIXEL_HEIGHT = 0xBA
EBML_ID_CLUSTER = 0x1F43B675
EBML_ID_TIMECODE = 0xE7
EBML_ID_SIMPLE_BLOCK = 0xA3


# =====================================================================
# EBML VINT Parser & Serializer (Pure Python)
# =====================================================================

def _read_vint(data: bytes, offset: int) -> Tuple[int, int, int]:
    """Read an EBML variable-size integer (VINT).

    Returns:
        Tuple of (vint_value_without_marker, vint_raw_id_with_marker, octet_length).
    Raises:
        ValueError if the offset is out of bounds or byte sequence is invalid.
    """
    if offset >= len(data):
        raise ValueError(f"Offset {offset} out of bounds (length {len(data)})")

    first_byte = data[offset]
    if first_byte == 0:
        raise ValueError("Invalid EBML VINT: leading octet cannot be zero")

    # Determine length by counting leading zero bits
    length = 1
    mask = 0x80
    while (first_byte & mask) == 0:
        length += 1
        mask >>= 1
        if length > 8:
            raise ValueError("EBML VINT length exceeds 8 octets")

    if offset + length > len(data):
        raise ValueError(f"Truncated EBML VINT: needed {length} octets at offset {offset}")

    raw_bytes = data[offset:offset + length]
    raw_id = int.from_bytes(raw_bytes, byteorder="big")

    # Strip the leading 1-bit for data values
    value_mask = (1 << (7 * length)) - 1
    value = raw_id & value_mask

    return value, raw_id, length


def _encode_vint(value: int, length: Optional[int] = None) -> bytes:
    """Encode an integer as an EBML VINT with marker bit."""
    if length is None:
        if value < 0x7F:
            length = 1
        elif value < 0x3FFF:
            length = 2
        elif value < 0x1FFFFF:
            length = 3
        elif value < 0x0FFFFFFF:
            length = 4
        else:
            length = 8

    marker = 1 << (7 * length)
    full_val = value | marker
    return full_val.to_bytes(length, byteorder="big")


def _encode_element(element_id: int, payload: bytes) -> bytes:
    """Encode an EBML element: ID + Size VINT + Payload."""
    # Convert element ID to bytes
    id_len = (element_id.bit_length() + 7) // 8
    id_bytes = element_id.to_bytes(id_len, byteorder="big")
    size_bytes = _encode_vint(len(payload))
    return id_bytes + size_bytes + payload


# =====================================================================
# Container Information & Validation
# =====================================================================

@dataclass
class ContainerInfo:
    """Detailed structural information about a media container."""
    is_valid: bool
    container_format: str  # "webm", "matroska", or "unknown"
    doc_type: str = ""
    ebml_version: int = 0
    doc_type_version: int = 0
    has_segment: bool = False
    data_size: int = 0
    error: str = ""


def parse_ebml_header(data: bytes) -> ContainerInfo:
    """Parse EBML container header and extract format details.

    Args:
        data: Binary container payload.
    Returns:
        ContainerInfo containing parsed attributes and validation status.
    """
    if not data:
        return ContainerInfo(is_valid=False, container_format="unknown", error="Container data is empty")

    if len(data) < 4:
        return ContainerInfo(is_valid=False, container_format="unknown", error="Data too short for EBML magic")

    offset = 0
    try:
        val, raw_id, id_len = _read_vint(data, offset)
        if raw_id != EBML_ID_HEADER:
            return ContainerInfo(
                is_valid=False,
                container_format="unknown",
                error=f"Not valid EBML: unexpected root element ID 0x{raw_id:X} (expected 0x{EBML_ID_HEADER:X})",
            )
        offset += id_len

        header_size, _, size_len = _read_vint(data, offset)
        offset += size_len

        header_end = offset + header_size
        if header_end > len(data):
            return ContainerInfo(
                is_valid=False,
                container_format="unknown",
                error="Truncated EBML header: payload truncated before header end",
            )

        ebml_version = 1
        doc_type = ""
        doc_type_version = 1

        # Parse sub-elements inside EBML Header
        while offset < header_end:
            sub_val, sub_id, sub_id_len = _read_vint(data, offset)
            offset += sub_id_len
            sub_size, _, sub_size_len = _read_vint(data, offset)
            offset += sub_size_len

            if offset + sub_size > header_end:
                return ContainerInfo(is_valid=False, container_format="unknown", error="Truncated sub-element in header")

            sub_data = data[offset:offset + sub_size]
            offset += sub_size

            if sub_id == EBML_ID_VERSION:
                ebml_version = int.from_bytes(sub_data, "big")
            elif sub_id == EBML_ID_DOC_TYPE:
                doc_type = sub_data.decode("ascii", errors="replace").strip("\x00").lower()
            elif sub_id == EBML_ID_DOC_TYPE_VERSION:
                doc_type_version = int.from_bytes(sub_data, "big")

        # After EBML Header, look for Segment element
        has_segment = False
        if offset < len(data) and data[offset:offset + 4] == b"\x18\x53\x80\x67":
            has_segment = True

        if doc_type not in ("webm", "matroska"):
            return ContainerInfo(
                is_valid=False,
                container_format="unknown",
                doc_type=doc_type,
                ebml_version=ebml_version,
                error=f"Unsupported DocType: '{doc_type}' (expected 'webm' or 'matroska')",
            )

        return ContainerInfo(
            is_valid=True,
            container_format=doc_type,
            doc_type=doc_type,
            ebml_version=ebml_version,
            doc_type_version=doc_type_version,
            has_segment=has_segment,
            data_size=len(data),
        )

    except ValueError as e:
        return ContainerInfo(is_valid=False, container_format="unknown", error=f"Not valid EBML: parse error {e}")
    except Exception as e:
        return ContainerInfo(is_valid=False, container_format="unknown", error=f"Unexpected error: {e}")


def validate_container(data: bytes) -> ContainerInfo:
    """Validate that data forms a valid WebM or Matroska container."""
    return parse_ebml_header(data)


# =====================================================================
# Synthetic WebM Container Generator
# =====================================================================

def create_synthetic_webm_container(
    frame_count: int = 10,
    fps: float = 30.0,
    width: int = 320,
    height: int = 240,
    doc_type: str = "webm",
) -> bytes:
    """Generate a structurally valid, minimal EBML WebM or Matroska container.

    Used for deterministic offline verification of parser and validation gates.
    """
    # 1. EBML Header Elements
    header_elements = [
        _encode_element(EBML_ID_VERSION, (1).to_bytes(1, "big")),
        _encode_element(EBML_ID_READ_VERSION, (1).to_bytes(1, "big")),
        _encode_element(EBML_ID_MAX_ID_LENGTH, (4).to_bytes(1, "big")),
        _encode_element(EBML_ID_MAX_SIZE_LENGTH, (8).to_bytes(1, "big")),
        _encode_element(EBML_ID_DOC_TYPE, doc_type.encode("ascii") + b"\x00"),
        _encode_element(EBML_ID_DOC_TYPE_VERSION, (4).to_bytes(1, "big")),
        _encode_element(EBML_ID_DOC_TYPE_READ_VERSION, (2).to_bytes(1, "big")),
    ]
    ebml_header = _encode_element(EBML_ID_HEADER, b"".join(header_elements))

    # 2. Segment Information
    timecode_scale = 1_000_000  # 1ms in nanoseconds
    duration_ms = float(frame_count * (1000.0 / fps))
    info_elements = [
        _encode_element(EBML_ID_TIMECODE_SCALE, timecode_scale.to_bytes(4, "big")),
        _encode_element(EBML_ID_DURATION, struct.pack(">f", duration_ms)),
    ]
    info_elem = _encode_element(EBML_ID_INFO, b"".join(info_elements))

    # 3. Track Configuration (Video)
    video_settings = [
        _encode_element(EBML_ID_PIXEL_WIDTH, width.to_bytes(2, "big")),
        _encode_element(EBML_ID_PIXEL_HEIGHT, height.to_bytes(2, "big")),
    ]
    track_entry = [
        _encode_element(EBML_ID_TRACK_NUMBER, (1).to_bytes(1, "big")),
        _encode_element(EBML_ID_TRACK_UID, (1).to_bytes(1, "big")),
        _encode_element(EBML_ID_TRACK_TYPE, (1).to_bytes(1, "big")),  # 1 = Video
        _encode_element(EBML_ID_CODEC_ID, b"V_VP8\x00"),
        _encode_element(EBML_ID_VIDEO_SETTINGS, b"".join(video_settings)),
    ]
    tracks_elem = _encode_element(EBML_ID_TRACKS, _encode_element(EBML_ID_TRACK_ENTRY, b"".join(track_entry)))

    # 4. Cluster & SimpleBlocks
    frame_interval_ms = 1000.0 / fps
    cluster_elements = [
        _encode_element(EBML_ID_TIMECODE, (0).to_bytes(2, "big")),
    ]
    # Minimal synthetic VP8 keyframe dummy payload
    dummy_vp8_frame = b"\x10\x00\x00\x9d\x01\x2a" + struct.pack("<HH", width, height) + (b"\x00" * 32)
    for i in range(frame_count):
        tc_offset = int(i * frame_interval_ms)
        # SimpleBlock: Track Number (1) + Timecode (int16) + Flags (0x80 = keyframe) + Data
        block_header = _encode_vint(1) + struct.pack(">h", tc_offset) + b"\x80"
        cluster_elements.append(_encode_element(EBML_ID_SIMPLE_BLOCK, block_header + dummy_vp8_frame))

    cluster_elem = _encode_element(EBML_ID_CLUSTER, b"".join(cluster_elements))

    segment_payload = info_elem + tracks_elem + cluster_elem
    segment = _encode_element(EBML_ID_SEGMENT, segment_payload)

    return ebml_header + segment


# =====================================================================
# Framerate Stability Analysis
# =====================================================================

@dataclass
class FramerateAnalysis:
    """Measurement of stream temporal pacing and jitter stability."""
    is_steady: bool
    effective_fps: float
    frame_count: int
    jitter_ms: float
    mean_interval_ms: float
    dropped_frames: int
    notes: str = ""


def analyze_framerate_stability(
    timestamps_ms: List[float],
    expected_fps: float = 30.0,
    max_jitter_ms: float = 15.0,
) -> FramerateAnalysis:
    """Analyze frame timestamps to evaluate pacing and detect frame drops.

    Args:
        timestamps_ms: Monotonically increasing sequence of millisecond timestamps.
        expected_fps: Target framerate to measure against.
        max_jitter_ms: Maximum tolerable standard deviation of frame intervals.
    """
    if len(timestamps_ms) < 2:
        return FramerateAnalysis(
            is_steady=False,
            effective_fps=0.0,
            frame_count=len(timestamps_ms),
            jitter_ms=0.0,
            mean_interval_ms=0.0,
            dropped_frames=0,
            notes="Insufficient timestamps for framerate analysis (< 2 frames)",
        )

    nominal_interval = 1000.0 / expected_fps
    intervals = [
        timestamps_ms[i + 1] - timestamps_ms[i]
        for i in range(len(timestamps_ms) - 1)
    ]

    mean_interval = sum(intervals) / len(intervals)
    variance = sum((x - mean_interval) ** 2 for x in intervals) / len(intervals)
    jitter = math.sqrt(variance)

    total_duration_s = (timestamps_ms[-1] - timestamps_ms[0]) / 1000.0
    effective_fps = (len(timestamps_ms) - 1) / total_duration_s if (math.isfinite(total_duration_s) and total_duration_s > 0) else 0.0

    # Count intervals that missed by more than 1.8x the nominal frame budget
    dropped_frames = sum(1 for iv in intervals if iv > 1.8 * nominal_interval)

    # Steady criteria: jitter within bounds and effective fps within 25% of expected
    fps_delta_ratio = abs(effective_fps - expected_fps) / expected_fps if expected_fps > 0 else 1.0
    is_steady = (jitter <= max_jitter_ms) and (fps_delta_ratio <= 0.25) and (dropped_frames == 0)

    notes = "Pacing steady" if is_steady else f"Pacing unsteady (jitter={jitter:.2f}ms, drops={dropped_frames})"

    return FramerateAnalysis(
        is_steady=is_steady,
        effective_fps=effective_fps,
        frame_count=len(timestamps_ms),
        jitter_ms=jitter,
        mean_interval_ms=mean_interval,
        dropped_frames=dropped_frames,
        notes=notes,
    )


# =====================================================================
# In-Browser Recording Script Generator
# =====================================================================

def build_in_browser_recorder_script(
    canvas_selector: str = "canvas",
    fps: int = 30,
    mime_type: str = "video/webm",
    timeslice_ms: int = 100,
) -> str:
    """Synthesize client-side JavaScript recorder for canvas or WebRTC elements."""
    return f"""(function() {{
  if (window.__bd_canvas_recorder) {{
    return;
  }}

  var state = {{
    isRecording: false,
    recorder: null,
    stream: null,
    chunks: [],
    frameTimestamps: [],
    startTime: 0,
    endTime: 0,
    selector: {json.dumps(canvas_selector)},
    fps: {fps},
    mimeType: {json.dumps(mime_type)},
    timesliceMs: {timeslice_ms},
    animCallbackId: null
  }};

  function selectTargetStream(selector, fps) {{
    var elem = document.querySelector(selector);
    if (!elem) {{
      throw new Error("Target element not found: " + selector);
    }}

    // 1. HTMLCanvasElement capture
    if (elem instanceof HTMLCanvasElement || elem.tagName === 'CANVAS') {{
      if (typeof elem.captureStream === 'function') {{
        return elem.captureStream(fps);
      }}
      throw new Error("HTMLCanvasElement.captureStream is not supported in this browser context");
    }}

    // 2. HTMLVideoElement WebRTC stream fallback
    if (elem instanceof HTMLVideoElement || elem.tagName === 'VIDEO') {{
      if (elem.srcObject && typeof elem.srcObject.getVideoTracks === 'function') {{
        return elem.srcObject;
      }}
      if (typeof elem.captureStream === 'function') {{
        return elem.captureStream(fps);
      }}
      throw new Error("HTMLVideoElement has no active MediaStream (srcObject or captureStream)");
    }}

    throw new Error("Element " + selector + " is neither a canvas nor video element");
  }}

  function resolveMimeType(preferred) {{
    var candidates = [
      preferred,
      'video/webm;codecs=vp9',
      'video/webm;codecs=vp8',
      'video/webm',
      'video/x-matroska;codecs=avc1',
      'video/mp4'
    ];
    for (var i = 0; i < candidates.length; i++) {{
      if (candidates[i] && MediaRecorder.isTypeSupported(candidates[i])) {{
        return candidates[i];
      }}
    }}
    return '';
  }}

  window.__bd_canvas_recorder = {{
    start: function(customSelector) {{
      if (state.isRecording) {{
        return;
      }}
      var targetSel = customSelector || state.selector;
      state.stream = selectTargetStream(targetSel, state.fps);
      state.chunks = [];
      state.frameTimestamps = [];
      state.startTime = performance.now();

      var chosenMime = resolveMimeType(state.mimeType);
      var options = chosenMime ? {{ mimeType: chosenMime }} : {{}};
      state.recorder = new MediaRecorder(state.stream, options);

      state.recorder.ondataavailable = function(evt) {{
        if (evt.data && evt.data.size > 0) {{
          state.chunks.push(evt.data);
        }}
      }};

      // Timestamp frame pacing
      function recordTick() {{
        if (!state.isRecording) return;
        state.frameTimestamps.push(performance.now() - state.startTime);
        state.animCallbackId = requestAnimationFrame(recordTick);
      }}

      state.isRecording = true;
      state.recorder.start(state.timesliceMs);
      state.animCallbackId = requestAnimationFrame(recordTick);
    }},

    stop: function() {{
      return new Promise(function(resolve, reject) {{
        if (!state.isRecording || !state.recorder) {{
          reject(new Error("Recording is not active"));
          return;
        }}

        state.isRecording = false;
        if (state.animCallbackId) {{
          cancelAnimationFrame(state.animCallbackId);
        }}
        state.endTime = performance.now();

        state.recorder.onstop = function() {{
          var blob = new Blob(state.chunks, {{ type: state.recorder.mimeType || state.mimeType }});
          var reader = new FileReader();
          reader.onloadend = function() {{
            var base64Data = reader.result.split(',')[1] || '';
            resolve({{
              dataBase64: base64Data,
              mimeType: blob.type,
              durationMs: state.endTime - state.startTime,
              frameCount: state.frameTimestamps.length,
              frameTimestampsMs: state.frameTimestamps,
              byteLength: blob.size
            }});
          }};
          reader.onerror = reject;
          reader.readAsDataURL(blob);
        }};

        state.recorder.stop();
      }});
    }},

    isRecording: function() {{
      return state.isRecording;
    }}
  }};
}})();"""


# =====================================================================
# High-Level CanvasRecorder & Result Wrappers
# =====================================================================

@dataclass
class CanvasRecordingResult:
    """Result of an in-browser canvas or WebRTC recording session."""
    data: bytes
    mime_type: str
    duration_ms: float
    frame_count: int
    fps: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    container_info: ContainerInfo = field(init=False)

    def __post_init__(self):
        self.container_info = validate_container(self.data)

    @property
    def is_valid(self) -> bool:
        return self.container_info.is_valid

    @property
    def container_format(self) -> str:
        return self.container_info.container_format

    @property
    def validation_error(self) -> str:
        return self.container_info.error

    def save(self, destination: Union[str, Path]) -> Path:
        """Persist recorded binary container to disk."""
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.data)
        return target


class CanvasRecorder:
    """Controller for injecting and driving in-browser canvas and WebRTC recording."""

    def __init__(
        self,
        fps: int = 30,
        mime_type: str = "video/webm",
        timeslice_ms: int = 100,
    ):
        self.fps = fps
        self.mime_type = mime_type
        self.timeslice_ms = timeslice_ms

    def get_injection_script(self, canvas_selector: str = "canvas") -> str:
        """Return self-contained client JavaScript recorder."""
        return build_in_browser_recorder_script(
            canvas_selector=canvas_selector,
            fps=self.fps,
            mime_type=self.mime_type,
            timeslice_ms=self.timeslice_ms,
        )

    def start(self, page: Any, selector: str = "canvas") -> None:
        """Inject recorder and begin recording selected element."""
        script = self.get_injection_script(canvas_selector=selector)
        page.evaluate(script)
        page.evaluate(f"window.__bd_canvas_recorder.start({json.dumps(selector)})")

    def is_recording(self, page: Any) -> bool:
        """Check if recording is currently in progress."""
        try:
            return bool(page.evaluate("window.__bd_canvas_recorder && window.__bd_canvas_recorder.isRecording()"))
        except Exception:
            return False

    def stop(self, page: Any) -> CanvasRecordingResult:
        """Stop recording and retrieve encoded video binary data and metadata."""
        raw_result = page.evaluate("window.__bd_canvas_recorder.stop()")
        raw_b64 = raw_result.get("dataBase64", "")
        binary_data = base64.b64decode(raw_b64) if raw_b64 else b""

        raw_dur = raw_result.get("durationMs", 0.0)
        duration_ms = float(raw_dur) if raw_dur is not None else 0.0
        frame_count = int(raw_result.get("frameCount", 0))
        if not math.isfinite(duration_ms) or duration_ms <= 0.0:
            duration_ms = 0.0
            effective_fps = float(self.fps)
        else:
            effective_fps = frame_count / (duration_ms / 1000.0)

        # metadata carries the in-page camelCase record AND the snake_case
        # keys the Python side reads (frame_timestamps_ms feeds
        # analyze_framerate_stability); an absent list is [] not a silent miss
        raw_timestamps = raw_result.get("frameTimestampsMs") or []
        metadata = dict(raw_result)
        metadata["frame_timestamps_ms"] = [float(t) for t in raw_timestamps if t is not None]
        metadata["frame_count"] = frame_count
        metadata["duration_ms"] = duration_ms
        metadata["byte_length"] = int(raw_result.get("byteLength", len(binary_data)) or 0)
        return CanvasRecordingResult(
            data=binary_data,
            mime_type=raw_result.get("mimeType", self.mime_type),
            duration_ms=duration_ms,
            frame_count=frame_count,
            fps=effective_fps,
            metadata=metadata,
        )
