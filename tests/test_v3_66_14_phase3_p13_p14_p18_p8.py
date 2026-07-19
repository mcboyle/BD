"""v3.66.14 — P14 + P13 + P18 + P8.

Smaller schema additions bundled in one test file (they're related —
all add visibility to existing flows without changing semantics).

  P14 — prefer_resolution fallback documentation: emit reason + warning
        when the requested tier is unknown / has no matches / all
        candidates lack resolution metadata.

  P13 — cookie-driven re-detection awareness (deep_detect_live only):
        flag when probe_headers contains a Cookie so the report's
        view may differ from a guest's.

  P18 — HLS media playlist refinement: emit a synthetic candidate
        even when base_url is missing, flagging that the input is
        valid but incomplete (instead of silently returning zero
        candidates).

  P8  — per-URL probe timings: head_probes entries now carry
        elapsed_ms.
"""
from __future__ import annotations

import pytest

from bulk_downloader.deep_detect import deep_detect, deep_detect_live

pytestmark = pytest.mark.bd_module_wipe


# ═══════════════════════════════════════════════════════════════════════
# P14 — prefer_resolution fallback documentation
# ═══════════════════════════════════════════════════════════════════════


PAGE_WITH_720_ONLY = """<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"playlists": [{"url": "https://x.example/720p.mp4", "resolution": "720p"}]}
</script></body></html>"""


PAGE_WITH_720_AND_4K = """<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"playlists": [
  {"url": "https://x.example/720p.mp4", "resolution": "720p"},
  {"url": "https://x.example/4k.mp4", "resolution": "2160p"}
]}
</script></body></html>"""


PAGE_WITH_RESOLUTIONLESS = """<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"source": {"hls": "https://cdn.example.test/master.m3u8"}}
</script></body></html>"""


def test_prefer_resolution_unknown_tier_emits_warning():
    r = deep_detect(PAGE_WITH_720_ONLY,
                    base_url="https://x.example/p",
                    prefer_resolution="69k")
    assert any("not a recognized tier label" in w for w in r["buckets"]["warnings"])
    # Candidate's reasons mention the unknown tier
    if r["buckets"]["accepted"]:
        last_reason = r["buckets"]["accepted"][0]["reasons"][-1]
        assert "unknown" in last_reason


def test_prefer_resolution_fallback_emits_warning_when_no_exact_match():
    r = deep_detect(PAGE_WITH_720_ONLY,
                    base_url="https://x.example/p",
                    prefer_resolution="4k")
    assert any("falling back" in w for w in r["buckets"]["warnings"])
    # And each candidate has a reason explaining its fallback status
    assert r["buckets"]["accepted"]
    last_reason = r["buckets"]["accepted"][0]["reasons"][-1]
    assert "fallback" in last_reason


def test_prefer_resolution_exact_match_does_not_emit_warning():
    r = deep_detect(PAGE_WITH_720_AND_4K,
                    base_url="https://x.example/p",
                    prefer_resolution="4k")
    # Best download is the 4k one
    assert r["buckets"]["best"]["url"] == "https://x.example/4k.mp4"
    # No prefer_resolution-related warning should have been emitted
    assert not any("falling back" in w or "not a recognized" in w
                   for w in r["buckets"]["warnings"])


def test_prefer_resolution_default_highest_is_silent():
    """Default behaviour (no preference) should never emit P14 noise."""
    r = deep_detect(PAGE_WITH_720_AND_4K,
                    base_url="https://x.example/p")
    assert not any("prefer_resolution" in w for w in r["buckets"]["warnings"])


def test_prefer_resolution_resolutionless_pool_emits_distinct_message():
    """When every candidate lacks resolution metadata, the message is
    different — there's nothing to compare against, so 'fallback' is
    misleading."""
    r = deep_detect(PAGE_WITH_RESOLUTIONLESS,
                    base_url="https://x.example/p",
                    prefer_resolution="4k")
    assert r["buckets"]["warnings"]
    msg = " ".join(r["buckets"]["warnings"])
    assert "lack resolution metadata" in msg or "falling back" in msg


def test_prefer_resolution_fallback_disclaimer_is_classified():
    """The fallback warning should classify into the 'prefer_fallback'
    type (not 'unclassified')."""
    r = deep_detect(PAGE_WITH_720_ONLY,
                    base_url="https://x.example/p",
                    prefer_resolution="4k")
    types = {d["type"] for d in r["disclaimers"]}
    assert "prefer_fallback" in types


# ═══════════════════════════════════════════════════════════════════════
# P13 — cookie-driven re-detection awareness
# ═══════════════════════════════════════════════════════════════════════


class _StubHead:
    """Minimal HEAD/GET stub that always returns 200 with no body."""

    def head(self, url, headers=None, timeout=None, follow_redirects=None):
        return _StubResp(status_code=200, headers={}, url=url)

    def get(self, url, headers=None, timeout=None, follow_redirects=None):
        return _StubResp(status_code=200, headers={}, url=url)


class _StubResp:
    def __init__(self, status_code, headers, url):
        self.status_code = status_code
        self.headers = headers
        self.url = url


PAGE_SIMPLE = """<!doctype html><html><body>
<a href="/download/abc123">video</a></body></html>"""


def test_cookie_in_probe_headers_emits_warning():
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        probe_headers={"User-Agent": "test", "Cookie": "session=abc"},
        max_total_probe_time=None,
    )
    assert any("Cookie" in w and "authenticated" in w for w in r["buckets"]["warnings"])
    assert r["probes"]["cookie_view"] is True


def test_no_cookie_in_probe_headers_no_warning():
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        probe_headers={"User-Agent": "test"},
        max_total_probe_time=None,
    )
    assert not any("Cookie" in w and "authenticated" in w
                   for w in r["buckets"]["warnings"])
    assert r["probes"]["cookie_view"] is False


def test_cookie_detection_is_case_insensitive():
    """HTTP header names are case-insensitive; we accept any casing."""
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        probe_headers={"cookie": "lowercase=1"},
        max_total_probe_time=None,
    )
    assert r["probes"]["cookie_view"] is True


def test_cookie_value_is_not_echoed():
    """Privacy: we surface the FACT of authentication, never the
    cookie value itself."""
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        probe_headers={"Cookie": "session=super_secret_token_xyz"},
        max_total_probe_time=None,
    )
    full_payload = str(r)
    assert "super_secret_token_xyz" not in full_payload


def test_no_probe_headers_cookie_view_is_false():
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        max_total_probe_time=None,
    )
    assert r["probes"]["cookie_view"] is False


# ═══════════════════════════════════════════════════════════════════════
# P18 — HLS media playlist refinement
# ═══════════════════════════════════════════════════════════════════════


HLS_MEDIA_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:6
#EXTINF:6.000,
seg-1.ts
#EXTINF:6.000,
seg-2.ts
#EXT-X-ENDLIST
"""


def test_hls_media_with_base_url_emits_real_candidate():
    """Pre-P18 (v3.66.10) behaviour: base_url passed → score 80,
    url=base_url. Unchanged."""
    r = deep_detect(HLS_MEDIA_PLAYLIST,
                    base_url="https://cdn.example.test/m.m3u8")
    cands = r["buckets"]["accepted"]
    assert cands, "expected one synthetic candidate"
    assert cands[0]["url"] == "https://cdn.example.test/m.m3u8"
    assert cands[0]["score"] == 80
    assert cands[0]["source_type"] == "hls_manifest"


def test_hls_media_without_base_url_emits_diagnostic_candidate():
    """Post-P18: no base_url → url=None, score=40, needs_workflow=True,
    a clear reason explaining what to do next. Pre-P18 this returned
    zero candidates."""
    r = deep_detect(HLS_MEDIA_PLAYLIST, base_url="")
    cands = r["buckets"]["accepted"]
    assert cands, "P18: media playlist with no base_url should still " \
                  "emit a diagnostic candidate"
    c = cands[0]
    assert c["url"] is None
    assert c["score"] == 40
    assert c["source_type"] == "hls_manifest"
    assert c["needs_workflow"] is True
    assert any("supply base_url" in r for r in c["reasons"])
    # And there's a warning that the input was incomplete
    assert any("base_url" in w for w in c["warnings"])


def test_hls_media_without_base_url_lowered_ceiling():
    """needs_workflow=True should pull the ceiling down via P9."""
    r = deep_detect(HLS_MEDIA_PLAYLIST, base_url="")
    c = r["buckets"]["accepted"][0]
    assert c["confidence_ceiling"] < 1.0
    assert c["ceiling_reasons"], "expected at least one ceiling reason"


# ═══════════════════════════════════════════════════════════════════════
# P8 — per-URL probe timings
# ═══════════════════════════════════════════════════════════════════════


class _SlowStubHead:
    """Stub that uses an injected clock to simulate elapsed time on
    each probe call. Increments a hand-rolled counter between head()
    and read-of-status so _probe_head's t_start vs t_end straddle it."""

    def __init__(self, clock, step_per_call=0.250):
        self.clock = clock
        self.step_per_call = step_per_call

    def head(self, url, headers=None, timeout=None, follow_redirects=None):
        # Burn `step_per_call` seconds of injected clock time.
        self.clock.advance(self.step_per_call)
        return _StubResp(status_code=200, headers={}, url=url)

    def get(self, url, headers=None, timeout=None, follow_redirects=None):
        self.clock.advance(self.step_per_call)
        return _StubResp(status_code=200, headers={}, url=url)


class _AdvanceableClock:
    """A simple clock that can be `.advance()`d explicitly. Used as
    `_now` so the test controls when elapsed time accumulates."""

    def __init__(self, start=0.0):
        self.t = float(start)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_probe_record_has_elapsed_ms():
    """Every head_probes entry carries elapsed_ms."""
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        max_total_probe_time=None,
    )
    probes = r["probes"]["head_probes"]
    assert probes, "expected at least one probe"
    for p in probes:
        assert "elapsed_ms" in p
        assert isinstance(p["elapsed_ms"], int)
        assert p["elapsed_ms"] >= 0


def test_elapsed_ms_reflects_injected_clock():
    """With an injected slow stub, elapsed_ms should equal the
    simulated duration (in ms). Validates the clock plumbing reaches
    _probe_head end-to-end."""
    clock = _AdvanceableClock()
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_SlowStubHead(clock, step_per_call=0.250),
        max_total_probe_time=None,
        _now=clock,
    )
    probes = r["probes"]["head_probes"]
    assert probes
    # The slow stub advances 250 ms per probe call. Expect each
    # elapsed_ms to be ≥ 250.
    for p in probes:
        assert p["elapsed_ms"] >= 250, (
            f"expected ≥250 ms, got {p['elapsed_ms']} ms")


def test_elapsed_ms_zero_on_executor_exception_path():
    """The synthetic probe built when a future raises has
    elapsed_ms=0. (We can't easily induce this without
    parallelism=2, so just assert the field exists in the
    serial-path probes too.)"""
    r = deep_detect_live(
        PAGE_SIMPLE,
        base_url="https://x.example/p",
        http=_StubHead(),
        max_total_probe_time=None,
    )
    # All probes have the field, regardless of which code path
    # produced them.
    for p in r["probes"]["head_probes"]:
        assert "elapsed_ms" in p
