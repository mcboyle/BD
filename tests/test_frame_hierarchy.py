"""tests/test_frame_hierarchy.py -- Row 931 (T1) Acceptance & Regression Suite.

Acceptance:
  (1) Automated multi-level frame inspection (Target.setAutoAttach + Page.getFrameTree)
  (2) Discovery of encapsulated media elements in nested documents
  (3) Stream parameter extraction (kind, host, query, resolution)
  (4) Active CDP pipeline integration (capture_via_cdp + cloak.discover_frames)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import pytest

from bulk_downloader.session_capture import SessionCapture, capture_via_cdp
import bulk_downloader
from bulk_downloader import cloak

try:
    from bulk_downloader import frame_hierarchy
except ImportError:
    frame_hierarchy = None

BD_GATE_SCOPE = "repo-wide"


class FakeCDPClient:
    """Mock CDP client implementing send, on, emit for frame hierarchy testing."""

    def __init__(self, frame_tree: Optional[Dict[str, Any]] = None):
        self.handlers: Dict[str, List[Any]] = {}
        self.sent: List[str] = []
        self.sent_payloads: List[tuple[str, Any]] = []
        self._frame_tree = frame_tree or {
            "frameTree": {
                "frame": {"id": "top-1", "url": "https://example.com/main", "name": "top"},
                "childFrames": [],
            }
        }

    def send(self, method: str, params: Any = None) -> Any:
        self.sent.append(method)
        self.sent_payloads.append((method, params))
        if method == "Page.getFrameTree":
            return self._frame_tree
        if method == "Target.setAutoAttach":
            return {"success": True}
        return None

    def on(self, event: str, handler: Any) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, params: Any) -> None:
        for h in self.handlers.get(event, []):
            h(params)


class FakePlaywrightFrame:
    """Mock Playwright Frame for evaluate() probe testing."""

    def __init__(self, url: str, probe_result: Optional[Dict[str, Any]] = None):
        self.url = url
        self._probe_result = probe_result or {"media": [], "controls": []}

    def evaluate(self, script: str) -> Any:
        return self._probe_result


class FakePlaywrightPage:
    """Mock Playwright Page with CDP session and frames."""

    def __init__(self, client: FakeCDPClient, frames: Optional[List[FakePlaywrightFrame]] = None, url: str = "https://example.com/main"):
        self.url = url
        self.client = client
        self.frames = frames or []
        self.context = type("Ctx", (), {"new_cdp_session": staticmethod(lambda page: client)})()


@pytest.fixture
def nested_tree():
    return {
        "frameTree": {
            "frame": {"id": "root-1", "url": "https://example.com/main", "name": "main"},
            "childFrames": [
                {
                    "frame": {"id": "frame-sub-1", "url": "https://player.cdn.com/embed?v=123", "name": "player_frame"},
                    "childFrames": [
                        {
                            "frame": {"id": "frame-sub-2", "url": "https://player.cdn.com/inner", "name": "inner_frame"},
                            "childFrames": [],
                        }
                    ],
                }
            ],
        }
    }


def test_cdp_auto_attach_and_nested_frame_discovery_during_capture(nested_tree):
    """Verifies that capture_via_cdp enables Target.setAutoAttach and discovers nested frames."""
    client = FakeCDPClient(nested_tree)
    frame_probe = {
        "media": [
            {
                "tag": "video",
                "src": "",
                "current_src": "https://video.edge.net/stream/master.m3u8?token=xyz",
                "poster": "https://video.edge.net/poster.jpg",
                "sources": [],
                "data": {"data-player": "v1"},
            }
        ],
        "controls": [
            {"tag": "button", "label": "Play", "class": "vjs-play-control"},
            {"tag": "button", "label": "Mute", "class": "vjs-mute-control"},
        ],
    }
    pw_frames = [
        FakePlaywrightFrame("https://example.com/main", {"media": [], "controls": []}),
        FakePlaywrightFrame("https://player.cdn.com/embed?v=123", frame_probe),
        FakePlaywrightFrame("https://player.cdn.com/inner", {"media": [], "controls": []}),
    ]
    page = FakePlaywrightPage(client, pw_frames)
    cap = SessionCapture(url="https://example.com/main")

    capture_via_cdp(page, cap)

    # 1. Target.setAutoAttach must be sent to CDP
    assert "Target.setAutoAttach" in client.sent, f"Target.setAutoAttach not sent: {client.sent}"

    # 2. Frame hierarchy must be attached to capture
    assert getattr(cap, "frame_hierarchy", None) is not None, "capture.frame_hierarchy is None"
    hier = cap.frame_hierarchy
    assert hier["n_frames"] == 3
    assert hier["max_depth"] == 2
    assert hier["n_media"] == 1

    # 3. Discovered media stream in nested frame
    subframe = hier["frames"][1]
    assert subframe["url"] == "https://player.cdn.com/embed?v=123"
    assert len(subframe["media"]) == 1
    media = subframe["media"][0]
    assert media["tag"] == "video"
    assert len(media["streams"]) == 1
    stream = media["streams"][0]
    assert stream["kind"] == "hls"
    assert stream["host"] == "video.edge.net"
    assert stream["query"]["token"] == "xyz"

    # 4. Controls discovered
    assert len(subframe["controls"]) == 2
    assert any("Play" in c["label"] for c in subframe["controls"])


def test_multi_level_frame_inspection_depth(nested_tree):
    """Direct verification of multi-level frame walking with depth annotations."""
    assert frame_hierarchy is not None, "bulk_downloader.frame_hierarchy must be implemented"
    rows = frame_hierarchy.walk_frame_tree(nested_tree)
    assert len(rows) == 3
    assert rows[0]["depth"] == 0
    assert rows[0]["frame_id"] == "root-1"
    assert rows[1]["depth"] == 1
    assert rows[1]["parent_id"] == "root-1"
    assert rows[2]["depth"] == 2
    assert rows[2]["parent_id"] == "frame-sub-1"


def test_oopif_target_attachment_merging():
    """Verifies that out-of-process iframes reported by Target.attachedToTarget are merged."""
    assert frame_hierarchy is not None, "bulk_downloader.frame_hierarchy must be implemented"
    tree = {
        "frame": {"id": "root-1", "url": "https://example.com", "name": "top"},
        "childFrames": [],
    }
    frames = frame_hierarchy.walk_frame_tree(tree)
    assert len(frames) == 1

    # Attached target for out-of-process iframe not present in initial tree
    attached = [
        {
            "targetInfo": {
                "targetId": "oopif-99",
                "type": "iframe",
                "url": "https://cross-origin.com/player",
                "openerId": "root-1",
            }
        }
    ]
    merged = frame_hierarchy.merge_attached_targets(frames, attached)
    assert len(merged) == 2
    assert merged[1]["frame_id"] == "oopif-99"
    assert merged[1]["out_of_process"] is True
    assert merged[1]["depth"] == 1
    assert merged[1]["parent_id"] == "root-1"


def test_stream_parameter_extraction_formats():
    """Verifies stream parameter extraction across HLS, DASH, progressive MP4, and resolution hint."""
    assert frame_hierarchy is not None, "bulk_downloader.frame_hierarchy must be implemented"

    hls = frame_hierarchy.extract_stream_parameters("https://stream.cdn.net/hls/1080p/index.m3u8?sig=abc&exp=123")
    assert hls["kind"] == "hls"
    assert hls["host"] == "stream.cdn.net"
    assert hls["resolution"] == 1080
    assert hls["query"] == {"sig": "abc", "exp": "123"}

    dash = frame_hierarchy.extract_stream_parameters("https://dash.cdn.net/manifest.mpd")
    assert dash["kind"] == "dash"
    assert dash["host"] == "dash.cdn.net"

    mp4 = frame_hierarchy.extract_stream_parameters("https://files.video.org/media/720p.mp4")
    assert mp4["kind"] == "progressive"
    assert mp4["resolution"] == 720

    blob = frame_hierarchy.extract_stream_parameters("blob:https://example.com/uuid-1234")
    assert blob["kind"] == "blob"

    empty = frame_hierarchy.extract_stream_parameters("")
    assert empty["kind"] == "none"


def test_cloak_discover_frames_wiring(nested_tree):
    """Verifies bulk_downloader.cloak exposes discover_frames and discovers hierarchy."""
    assert hasattr(cloak, "discover_frames"), "bulk_downloader.cloak.discover_frames must exist"
    client = FakeCDPClient(nested_tree)
    page = FakePlaywrightPage(client, [FakePlaywrightFrame("https://example.com/main")])
    result = cloak.discover_frames(page)
    assert isinstance(result, dict)
    assert result["n_frames"] == 3


def test_to_capture_dict_serialization():
    """Verifies SessionCapture.to_capture_dict serializes frame_hierarchy when present."""
    cap = SessionCapture(url="https://example.com")
    cap.frame_hierarchy = {
        "frames": [{"frame_id": "1", "url": "https://example.com", "depth": 0, "media": []}],
        "n_frames": 1,
        "max_depth": 0,
        "n_media": 0,
        "n_oopif": 0,
    }
    d = cap.to_capture_dict()
    assert "frame_hierarchy" in d
    assert d["frame_hierarchy"]["n_frames"] == 1


def test_negative_control_detached_frame_error_handling():
    """Negative control: a frame whose evaluation raises does not crash inspection."""
    assert frame_hierarchy is not None, "bulk_downloader.frame_hierarchy must be implemented"
    client = FakeCDPClient()
    def _exploding_eval(frame):
        raise RuntimeError("Frame was detached during navigation")

    result = frame_hierarchy.inspect_frame_hierarchy(client, _exploding_eval)
    assert result["n_frames"] == 1
    frame_res = result["frames"][0]
    assert frame_res["probe_error"] is not None
    assert "RuntimeError" in frame_res["probe_error"]
    assert result["n_media"] == 0


def test_negative_control_frame_without_media():
    """Negative control: frames with zero media elements return exact count zero."""
    assert frame_hierarchy is not None, "bulk_downloader.frame_hierarchy must be implemented"
    client = FakeCDPClient()
    def _empty_eval(frame):
        return {"media": [], "controls": []}

    result = frame_hierarchy.inspect_frame_hierarchy(client, _empty_eval)
    assert result["n_media"] == 0
    assert result["frames"][0]["media"] == []
