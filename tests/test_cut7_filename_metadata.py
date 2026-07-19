"""Cut 7.5a: filename -> metadata at scale.

Broadens the resolution-only `normalize_resolution` pattern to full
title / season / episode / codec / resolution extraction, cached by
filename hash. Advisory only: a deterministic regex validates the
common case; the LLM is a best-effort fallback for the weird tail.
Same return shape from every path so callers don't branch on `via`.

Boundary (mirrors the rest of Cut 7): the helper is side-effect-free,
fails open (never raises), and never persists/mutates anything.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


# ── a fake GenerationResult-shaped object for the injected LLM path ────
class _FakeResult:
    def __init__(self, ok=True, text="", error="", error_kind="",
                 provider="fake", latency_ms=1):
        self.ok = ok
        self.text = text
        self.error = error
        self.error_kind = error_kind
        self.provider = provider
        self.latency_ms = latency_ms


_EXPECTED_KEYS = {
    "ok", "title", "season", "episode", "codec",
    "resolution", "label", "width", "height", "confidence", "via",
}


def test_helper_exists_and_shape():
    """normalize_filename returns the canonical key set."""
    from bulk_downloader.aiassist import normalize_filename
    out = normalize_filename("The.Show.S01E02.1080p.x265-GRP.mkv")
    assert isinstance(out, dict)
    assert _EXPECTED_KEYS.issubset(set(out.keys()))


def test_regex_fast_path_full():
    """Common pattern resolves wholly via regex: no model needed."""
    from bulk_downloader.aiassist import normalize_filename

    def _boom(*a, **k):  # must NOT be called on the fast path
        raise AssertionError("regex fast path must not hit the model")

    out = normalize_filename("The.Show.S01E02.1080p.x265-GRP.mkv", _call=_boom)
    assert out["ok"] is True
    assert out["via"] == "regex"
    assert out["season"] == 1
    assert out["episode"] == 2
    assert out["codec"] == "h265"
    assert out["resolution"] == "1080p"
    assert out["label"] == "1080"
    # title is best-effort but should recover the human-readable stem
    assert "show" in (out["title"] or "").lower()
    assert out["confidence"] >= 90


def test_codec_aliases_normalize():
    """x264/h.264/AVC all normalize to h264; AV1 stays av1."""
    from bulk_downloader.aiassist import normalize_filename
    assert normalize_filename("clip.720p.h264.mp4")["codec"] == "h264"
    assert normalize_filename("clip.720p.x264.mp4")["codec"] == "h264"
    assert normalize_filename("clip.720p.AVC.mp4")["codec"] == "h264"
    assert normalize_filename("clip.2160p.AV1.mkv")["codec"] == "av1"


def test_episode_alt_form():
    """1x02 form parses the same as S01E02."""
    from bulk_downloader.aiassist import normalize_filename
    out = normalize_filename("Some Title 1x02 720p.mkv")
    assert out["season"] == 1
    assert out["episode"] == 2
    assert out["via"] == "regex"


def test_empty_filename():
    """Empty input -> ok, all-null, via=empty (never raises)."""
    from bulk_downloader.aiassist import normalize_filename
    out = normalize_filename("")
    assert out["ok"] is True
    assert out["via"] == "empty"
    assert out["title"] is None
    assert out["resolution"] is None
    assert out["codec"] is None
    assert out["season"] is None and out["episode"] is None
    assert out["width"] == 0 and out["height"] == 0
    assert out["confidence"] == 0


def test_no_signal_ai_disabled_fails_open():
    """No regex signal + AI disabled -> ok, null fields, via=no-match."""
    from bulk_downloader import aiassist
    saved = aiassist._config["enabled"]
    try:
        aiassist._config["enabled"] = False
        out = aiassist.normalize_filename("randomblob")
        assert out["ok"] is True
        assert out["via"] == "no-match"
        assert out["resolution"] is None
        assert out["codec"] is None
    finally:
        aiassist._config["enabled"] = saved


def test_llm_fallback_parses_and_clamps():
    """No regex signal + AI enabled -> injected model, JSON parsed,
    confidence clamped 0..100, via=ai."""
    from bulk_downloader import aiassist
    saved = aiassist._config["enabled"]
    try:
        aiassist._config["enabled"] = True
        payload = ('{"title":"Mystery","season":3,"episode":7,'
                   '"codec":"h265","resolution":"2160p","label":"4K",'
                   '"width":3840,"height":2160,"confidence":250}')

        def _fake(*a, **k):
            return _FakeResult(ok=True, text=payload)

        out = aiassist.normalize_filename("totally-opaque-name", _call=_fake)
        assert out["ok"] is True
        assert out["via"] == "ai"
        assert out["title"] == "Mystery"
        assert out["season"] == 3 and out["episode"] == 7
        assert out["resolution"] == "2160p"
        assert out["confidence"] == 100  # clamped down from 250
    finally:
        aiassist._config["enabled"] = saved


def test_llm_unparseable_fails_open():
    """AI returns prose with no JSON -> ok:false, via=ai-fail, no raise."""
    from bulk_downloader import aiassist
    saved = aiassist._config["enabled"]
    try:
        aiassist._config["enabled"] = True

        def _fake(*a, **k):
            return _FakeResult(ok=True, text="sorry, I cannot tell")

        out = aiassist.normalize_filename("totally-opaque-name2", _call=_fake)
        assert out["ok"] is False
        assert out["via"] == "ai-fail"
    finally:
        aiassist._config["enabled"] = saved


def test_llm_error_fails_open():
    """Model error result -> ok:false, via=ai-fail (never raises)."""
    from bulk_downloader import aiassist
    saved = aiassist._config["enabled"]
    try:
        aiassist._config["enabled"] = True

        def _fake(*a, **k):
            return _FakeResult(ok=False, error="conn refused",
                               error_kind="unreachable")

        out = aiassist.normalize_filename("opaque3", _call=_fake)
        assert out["ok"] is False
        assert out["via"] == "ai-fail"
    finally:
        aiassist._config["enabled"] = saved


def test_cache_hits_skip_second_model_call():
    """Same opaque filename twice -> model invoked at most once
    (result cached by filename hash)."""
    from bulk_downloader import aiassist
    saved = aiassist._config["enabled"]
    try:
        aiassist._config["enabled"] = True
        calls = {"n": 0}
        payload = '{"title":"Cached","resolution":"1080p","label":"1080","confidence":80}'

        def _fake(*a, **k):
            calls["n"] += 1
            return _FakeResult(ok=True, text=payload)

        name = "cache-me-unique-xyz"
        a = aiassist.normalize_filename(name, _call=_fake)
        b = aiassist.normalize_filename(name, _call=_fake)
        assert a["title"] == "Cached" and b["title"] == "Cached"
        assert calls["n"] == 1  # second call served from cache
    finally:
        aiassist._config["enabled"] = saved


def test_resolution_helper_unchanged_byte_stable():
    """normalize_resolution must remain present + behaviorally intact
    (7.5a adds a sibling, never edits the resolution helper)."""
    from bulk_downloader.aiassist import normalize_resolution
    out = normalize_resolution("movie.1080p.mkv")
    assert out["resolution"] == "1080p"
    assert out["via"] == "regex"
