"""Integration and unit tests for Row 908 (detect.py duration and size qualification)."""
from __future__ import annotations

import importlib

detect = importlib.import_module("bulk_downloader.detect")

BD_GATE_SCOPE = "module"


def _qualify(*args, **kwargs):
    fn = getattr(detect, "qualifies_as_full_length", None)
    if fn is not None:
        return fn(*args, **kwargs)
    return {"ok": True, "duration_s": 0, "size_bytes": 0, "reason": None}


def _inspect_stream(*args, **kwargs):
    fn = getattr(detect, "inspect_container_stream", None)
    if fn is not None:
        return fn(*args, **kwargs)
    return {"duration_s": 0, "size_bytes": 0}


def test_inspect_container_stream_reads_metadata_dict():
    meta = {"duration": 240, "size": 60000000}
    info = _inspect_stream(None, stream_meta=meta)
    assert info["duration_s"] == 240
    assert info["size_bytes"] == 60000000


def test_inspect_container_stream_reads_format_subdict():
    meta = {"format": {"duration": "300.5", "size": "80000000"}}
    info = _inspect_stream(None, stream_meta=meta)
    assert info["duration_s"] == 300
    assert info["size_bytes"] == 80000000


def test_find_best_download_skips_short_preview_when_full_length_enabled():
    """find_best_download should filter out short preview clips and select complete payload."""
    class FakeEl:
        def __init__(self, text, attrs=None):
            self._text = text
            self._attrs = attrs or {}

        def inner_text(self):
            return self._text

        def get_attribute(self, name):
            return self._attrs.get(name)

        def is_visible(self):
            return True

        def evaluate(self, script, *args):
            return True

    class FakeLocator:
        def __init__(self, els):
            self._els = els

        def count(self):
            return len(self._els)

        def all(self):
            return self._els

        def nth(self, i):
            return self._els[i]

        @property
        def first(self):
            return self._els[0] if self._els else None

    class FakePage:
        def __init__(self, els):
            self.url = "https://example.com/watch/video1"
            self._loc = FakeLocator(els)

        def locator(self, sel):
            if sel in ("a", "a[download]", "a[href*='.mp4']"):
                return self._loc
            return FakeLocator([])

    el_preview = FakeEl(
        "Bonus Scene 1:30 1080p 20MB",
        {"href": "https://example.com/watch/video1_bonus.mp4"}
    )
    el_full = FakeEl(
        "Full movie 1:45:00 720p 1.5GB",
        {"href": "https://example.com/watch/video1_full.mp4"}
    )

    class FakeRunner:
        def __init__(self):
            self.config = {"require_full_length": True}
            self.events = []

        def log_event(self, kind, message, extra=None):
            self.events.append((kind, message, extra))

    page = FakePage([el_preview, el_full])
    runner = FakeRunner()

    best = detect.find_best_download(page, runner=runner)
    assert best is not None, "must find candidate"
    assert "Full movie" in best["text"], f"Expected full movie, but got: {best['text']}"
