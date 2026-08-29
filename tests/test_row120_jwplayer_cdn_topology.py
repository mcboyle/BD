"""Row 120 (LEDGER ITEM 31 sub-row JW-TMPL) -- the auto-template must recognise
signed JWPlayer served from an akamai/cloudflare/cloudfront-FRONTED host.

WHY THE ROW WAS PARKED, AND WHAT UNPARKED IT. Measured at v3.66.1195 over the
correct denominator -- every capture whose recorded page host is
``ultrafilms.com``, 21 files across 14 sessions -- all 21 were JWPlayer-bearing
with a member-only entitlement call and signed renditions, but 0 of 21 served a
player or media asset from an akamai/cloudflare/cloudfront host: the only CDN
host anywhere was ``cdn.jsdelivr.net``, a JS-library CDN, which the row's ruling
explicitly does not count. The blocker was a capture, not code. Two recorded
captures now supply the missing topology:

  * a JWPlayer 8.30.1 news page whose player script sits on an akamai-fronted
    origin, whose entitlement call is cloudfront-fronted, and whose HLS
    manifest + segments come from an ``.akamaized.net`` media host; and
  * a JWPlayer 8 archive page whose player and progressive mp4 both come from
    the site's own origin tiers -- the CDN variable isolated to zero.

THOSE RECORDED FILES ARE NEVER COMMITTED. ``project-knowledge/
CAPTURE_SHARING_POLICY.md`` is unambiguous: synthetic fixtures remain the only
captures that may be committed or circulated. So this gate runs over two
COMMITTED SYNTHETIC captures that reproduce the measured topology
(``tools/make_synthetic_capture_corpus.py``: ``_jw_akamai`` / ``_jw_plain``),
and additionally replays the RECORDED originals whenever a private capture root
is configured. The synthetic pair is the CI denominator; the recorded pair is
the higher-fidelity confirmation.

WHAT THIS GATE REFUSES TO DO. A zero read out of an unread archive must never
present as "no CDN present" -- that exact false zero has been manufactured
twice by scanning a ``.wacz`` for ``.warc`` members it does not contain. So
every population here asserts its own nonzero denominator FROM THE ARCHIVE,
independently of the recogniser under test, before any verdict is read; an
archive that parses to zero entries fails loudly. And an unmeasurable input
returns ``status == "unknown"`` with ``cdn_fronted is None`` -- never ``False``,
which would read as a measured absence.

A6: no test here makes a live or authenticated request; every byte is read from
a recorded or committed archive on disk.
"""
from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from capture_test_fixtures import capture_fixture_lane
import tools.build_template_from_wacz as b


BD_GATE_SCOPE = "module"

_REPO = Path(__file__).resolve().parents[1]
_SYNTHETIC = _REPO / "tests" / "capture_corpus_synthetic"

#: committed synthetic captures reproducing the measured recorded topology
SYN_CDN = _SYNTHETIC / "jw_akamai_fronted.wacz"
SYN_PLAIN = _SYNTHETIC / "jw_no_cdn_control.wacz"

#: recorded originals -- private capture evidence, replayed only when a root is set
REC_CDN = "nbcnews_video_akamai_jw.wacz"
REC_PLAIN = "archiveorg_item.wacz"

_CDN_MEDIA_SUFFIX = ".akamaized.net"


# ── archive-side measurement, independent of the recogniser ────────────────
def _capture_of(path: Path) -> dict:
    """Read a .wacz's ``archive/capture.json``. A .wacz is NOT a WARC: a scan
    looking for ``.warc`` members finds nothing and returns a misleading zero,
    so the member name is asserted rather than searched loosely."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        assert "archive/capture.json" in names, (
            f"UNKNOWN: {path.name} has no archive/capture.json member; "
            f"members={names}")
        cap = json.loads(z.read("archive/capture.json"))
    assert isinstance(cap, dict), f"UNKNOWN: {path.name} capture.json is not an object"
    return cap


def _entries(path: Path) -> list:
    cap = _capture_of(path)
    log = cap.get("network_log")
    assert isinstance(log, list), (
        f"UNKNOWN: {path.name} network_log is {type(log).__name__}, not a list")
    entries = [e for e in log if isinstance(e, dict)]
    assert entries, (
        f"UNKNOWN: {path.name} parsed to ZERO network entries -- a zero from an "
        f"unread archive must never read as 'no CDN present'")
    return entries


def _hosts(entries) -> list:
    from urllib.parse import urlsplit
    return [urlsplit(str(e.get("url") or "")).netloc for e in entries]


def _assert_cdn_fixture_preconditions(path: Path) -> dict:
    """PRECONDITION, asserted before any verdict is read: the archive really
    carries an akamai-fronted media asset AND a JWPlayer entitlement call."""
    entries = _entries(path)
    hosts = _hosts(entries)
    akamai_media = [
        e for e in entries
        if _hosts([e])[0].lower().endswith(_CDN_MEDIA_SUFFIX)
        and any(x in str(e.get("url") or "").lower()
                for x in (".m3u8", ".ts", ".mp4", "/out/v1/"))
    ]
    entitlement = [h for h in hosts if h.lower().startswith("entitlements.jwplayer")]
    assert len(entries) > 0
    assert len(akamai_media) >= 1, (
        f"UNKNOWN: {path.name} carries no *{_CDN_MEDIA_SUFFIX} media asset; the "
        f"akamai-fronted axis cannot be measured from it")
    assert len(entitlement) >= 1, (
        f"UNKNOWN: {path.name} carries no JWPlayer entitlement call; the "
        f"JWPlayer axis cannot be measured from it")
    return {"entries": len(entries), "akamai_media": len(akamai_media),
            "entitlement": len(entitlement)}


def _assert_control_preconditions(path: Path) -> dict:
    """The control must be a REAL page with real media and a real JWPlayer --
    otherwise 'not CDN-fronted' is vacuously true of an empty parse."""
    entries = _entries(path)
    urls = [str(e.get("url") or "").lower() for e in entries]
    jw = [u for u in urls if "jwplayer" in u or "/jw/" in u]
    media = [u for u in urls if any(x in u for x in (".mp4", ".m3u8", ".ts"))]
    cdn_hosts = [h for h in _hosts(entries)
                 if any(h.lower().endswith(s) for s in
                        (".akamaized.net", ".akamaihd.net", ".cloudfront.net",
                         ".cloudflarestream.com"))]
    assert len(jw) >= 1, f"UNKNOWN: {path.name} carries no JWPlayer asset"
    assert len(media) >= 1, f"UNKNOWN: {path.name} carries no media asset"
    assert cdn_hosts == [], (
        f"{path.name} is not a no-CDN control: {sorted(set(cdn_hosts))}")
    return {"entries": len(entries), "jw": len(jw), "media": len(media)}


def _topology_of(path: Path) -> dict:
    """Read the topology block out of the REAL production draft path."""
    draft = b.build_template(path)
    topo = draft.get("cdn_topology")
    assert isinstance(topo, dict), (
        "build_template() produced no cdn_topology block; the auto-template "
        "cannot recognise CDN-fronted JWPlayer")
    return topo


# ── the committed synthetic pair: the CI denominator ───────────────────────
def test_synthetic_fixtures_are_present():
    for p in (SYN_CDN, SYN_PLAIN):
        assert p.is_file(), (
            f"UNKNOWN: {p} missing -- regenerate with "
            f"tools/make_synthetic_capture_corpus.py")


def test_synthetic_cdn_fixture_carries_the_topology_it_claims():
    counts = _assert_cdn_fixture_preconditions(SYN_CDN)
    assert counts["akamai_media"] >= 2      # manifest + segment
    assert counts["entitlement"] == 1


def test_synthetic_control_carries_jwplayer_and_media_but_no_cdn():
    counts = _assert_control_preconditions(SYN_PLAIN)
    assert counts["jw"] >= 1
    assert counts["media"] >= 1


def test_draft_recognises_cdn_fronted_jwplayer():
    topo = _topology_of(SYN_CDN)
    assert topo["status"] == "measured", topo
    assert topo["entries_total"] >= topo["entries_examined"] > 0
    assert topo["jwplayer_present"] is True
    assert topo["cdn_fronted"] is True
    assert topo["jwplayer_entitlement_calls"] >= 1
    assert topo["cdn_fronted_media_assets"] >= 1
    assert topo["cdn_fronted_player_assets"] >= 1
    assert "akamai" in topo["cdn_vendors"]
    assert "cloudfront" in topo["cdn_vendors"]     # header-only fronting
    assert set(topo["cdn_evidence"]) >= {"host_suffix", "response_header"}


def test_draft_control_is_jwplayer_but_not_cdn_fronted():
    topo = _topology_of(SYN_PLAIN)
    assert topo["status"] == "measured", topo
    assert topo["jwplayer_present"] is True       # a recogniser, not a yes-machine
    assert topo["media_assets"] >= 1              # nonzero denominator
    assert topo["cdn_fronted"] is False
    assert topo["cdn_vendors"] == []
    assert topo["cdn_fronted_media_assets"] == 0
    assert topo["cdn_fronted_player_assets"] == 0


def test_signed_media_on_a_cdn_host_is_reported_as_both():
    topo = _topology_of(SYN_CDN)
    assert topo["signed_media_assets"] >= 1
    assert topo["signed_and_cdn_fronted_media_assets"] >= 1


def test_topology_block_persists_no_signing_value():
    blob = json.dumps(_topology_of(SYN_CDN))
    assert "hmac=" not in blob
    assert "hdnts" not in blob
    assert "exp=1700000000" not in blob


def test_draft_signals_carry_the_cdn_verdict():
    draft = b.build_template(SYN_CDN)
    sigs = draft.get("network_discovery", {}).get("download_signals") or []
    assert "jwplayer" in sigs
    assert "cdn-fronted" in sigs
    assert "cdn:akamai" in sigs
    plain = b.build_template(SYN_PLAIN)
    psigs = plain.get("network_discovery", {}).get("download_signals") or []
    assert "cdn-fronted" not in psigs


# ── the recorded originals: higher-fidelity replay when a root is configured ──
_LANE = capture_fixture_lane()
_HAVE_RECORDED = _LANE.enabled and _LANE.has(REC_CDN, REC_PLAIN)
_recorded = pytest.mark.skipif(
    not _HAVE_RECORDED,
    reason=(f"recorded capture lane disabled; set {_LANE.env_name} to a root "
            f"holding {REC_CDN} and {REC_PLAIN}"))


@_recorded
def test_recorded_akamai_capture_is_recognised():
    path = _LANE.path(REC_CDN)
    counts = _assert_cdn_fixture_preconditions(path)
    assert counts["entries"] >= 100          # a real page, not a stub
    topo = _topology_of(path)
    assert topo["status"] == "measured", topo
    # a real page: the relevant slice is a small fraction of the whole log
    assert topo["entries_total"] == counts["entries"]
    assert topo["entries_total"] > topo["entries_examined"] > 0
    assert topo["jwplayer_present"] is True
    assert topo["cdn_fronted"] is True
    assert "akamai" in topo["cdn_vendors"]
    assert topo["cdn_fronted_media_assets"] >= counts["akamai_media"] - 1
    assert topo["jwplayer_entitlement_calls"] >= 1


@_recorded
def test_recorded_control_capture_is_jwplayer_without_cdn():
    path = _LANE.path(REC_PLAIN)
    _assert_control_preconditions(path)
    topo = _topology_of(path)
    assert topo["status"] == "measured", topo
    assert topo["jwplayer_present"] is True
    assert topo["media_assets"] >= 1
    assert topo["cdn_fronted"] is False
    assert topo["cdn_vendors"] == []


# ── unit-level vendor and refusal coverage ─────────────────────────────────
def _net(url, ct="video/mp4", status=200, headers=None, etype="xhr"):
    hdrs = {"content-type": ct}
    hdrs.update(headers or {})
    return [{"url": url, "response_status": status, "type": etype,
             "response_headers": [{"name": k, "value": v} for k, v in hdrs.items()]}]


def test_cloudflare_is_recognised_by_response_header():
    log = (_net("https://media.example.com/hls/index.m3u8",
                ct="application/x-mpegURL",
                headers={"cf-ray": "0" * 16, "server": "cloudflare"})
           + _net("https://cdn.example.com/jwplayer/jwplayer-8.js",
                  ct="application/javascript", etype="script"))
    topo = b.jwplayer_cdn_topology(log)
    assert topo["status"] == "measured"
    assert topo["jwplayer_present"] is True
    assert topo["cdn_fronted"] is True
    assert topo["cdn_vendors"] == ["cloudflare"]
    assert topo["cdn_evidence"] == ["response_header"]


def test_cloudfront_is_recognised_by_host_suffix():
    log = (_net("https://d111111abcdef8.cloudfront.net/video/movie.mp4")
           + _net("https://cdn.example.com/jw/8/jwplayer.core.controls.html5.js",
                  ct="application/javascript", etype="script"))
    topo = b.jwplayer_cdn_topology(log)
    assert topo["cdn_fronted"] is True
    assert topo["cdn_vendors"] == ["cloudfront"]
    assert topo["cdn_evidence"] == ["host_suffix"]


def test_library_cdn_alone_is_not_cdn_fronting():
    """The row's own ruling: cdn.jsdelivr.net is a JS-library CDN and the 21
    ultrafilms captures were NOT counted as CDN-fronted because of it."""
    log = (_net("https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js",
                ct="application/javascript", etype="script",
                headers={"cf-ray": "0" * 16, "server": "cloudflare"})
           + _net("https://www.site.example/media/movie.mp4")
           + _net("https://entitlements.jwplayer.com/AAAA.json",
                  ct="application/json"))
    topo = b.jwplayer_cdn_topology(log)
    assert topo["jwplayer_present"] is True
    assert topo["media_assets"] >= 1
    assert topo["cdn_fronted"] is False
    assert topo["cdn_vendors"] == []


def test_tracker_host_on_a_cdn_does_not_flip_the_verdict():
    log = (_net("https://securepubads.g.doubleclick.net/ads/preroll.mp4",
                headers={"cf-ray": "0" * 16})
           + _net("https://www.site.example/media/movie.mp4")
           + _net("https://cdn.example.com/jwplayer/jwplayer-8.js",
                  ct="application/javascript", etype="script"))
    topo = b.jwplayer_cdn_topology(log)
    assert topo["cdn_fronted"] is False


def test_caption_alone_is_not_sufficient_for_cdn_fronted():
    log = (_net("https://sub.akamaized.net/captions/en.vtt", ct="text/vtt")
           + _net("https://www.site.example/media/movie.mp4")
           + _net("https://cdn.example.com/jwplayer/jwplayer-8.js",
                  ct="application/javascript", etype="script"))
    topo = b.jwplayer_cdn_topology(log)
    assert topo["cdn_fronted_caption_assets"] >= 1
    assert topo["cdn_fronted_media_assets"] == 0
    assert topo["cdn_fronted_player_assets"] == 0
    assert topo["cdn_fronted"] is False


def test_no_jwplayer_anywhere_reports_absent_not_unknown():
    topo = b.jwplayer_cdn_topology(_net("https://sub.akamaized.net/movie.mp4"))
    assert topo["status"] == "measured"
    assert topo["jwplayer_present"] is False
    assert topo["cdn_fronted"] is True       # topology is measured independently


@pytest.mark.parametrize("bad,why", [
    ([], "zero request entries"),
    (None, "not a list"),
    ("archive/capture.json", "not a list"),
    ([1, "x", None], "zero request entries"),
])
def test_unmeasurable_input_is_unknown_never_a_measured_absence(bad, why):
    topo = b.jwplayer_cdn_topology(bad)
    assert topo["status"] == "unknown", topo
    assert topo["cdn_fronted"] is None
    assert topo["jwplayer_present"] is None
    assert why in (topo["unknown_reason"] or "")
    assert topo["entries_examined"] == 0


def test_whole_log_denominator_is_visible_not_just_the_relevant_slice():
    """`entries_examined` counts the player/media/entitlement assets, which is a
    tiny slice of a real page. Without `entries_total` a reader cannot tell an
    archive that held 397 requests and 13 relevant ones from one that held 13."""
    topo = _topology_of(SYN_CDN)
    assert topo["entries_total"] >= topo["entries_examined"] > 0
    assert topo["entries_total"] == len(_entries(SYN_CDN))
    assert topo["tracker_filter"] == "honeypot_score"


@pytest.mark.parametrize("path,ct,expect", [
    # a definite type is believed, in both directions
    ("/out/v1/aaaa/bbbb/cccc", "video/MP2T", "media"),       # no extension at all
    ("/hls/index.m3u8", "application/x-mpegURL", "media"),
    ("/captions/en.vtt", "text/vtt", "caption"),
    ("/serve/item/movie.mp4", "text/html; charset=UTF-8", ""),   # a redirect page
    ("/bundle.ts", "application/javascript", ""),                # TypeScript, not MPEG-TS
    # octet-stream is ambiguous, so the extension arbitrates -- and only then
    ("/ramen-overrides.js", "application/octet-stream", ""),
    ("/fonts/iconfont.433506180b.woff2", "binary/octet-stream", ""),
    ("/videos/movie.mp4", "application/octet-stream", "media"),
    # no type recorded at all: the extension is all there is
    ("/videos/movie.mp4", "", "media"),
    ("/videos/movie.mp4", None, "media"),
    ("/page/index.html", "", ""),
])
def test_asset_kind_believes_a_named_type_and_falls_back_only_when_ambiguous(
        path, ct, expect):
    assert b._asset_kind(path, ct) == expect


def test_octet_stream_script_and_font_are_not_counted_as_media():
    """Regression: an `octet-stream` clause in the content-type match counted
    `/ramen-overrides.js` and an `iconfont.woff2` as media assets on a recorded
    page, inflating both the media denominator and the CDN-fronted media count."""
    log = (_net("https://assets.akamaized.net/ramen-overrides.js",
                ct="application/octet-stream")
           + _net("https://assets.akamaized.net/fonts/iconfont.woff2",
                  ct="binary/octet-stream")
           + _net("https://cdn.example.com/jwplayer/jwplayer-8.js",
                  ct="application/javascript", etype="script"))
    topo = b.jwplayer_cdn_topology(log)
    assert topo["status"] == "measured"
    assert topo["media_assets"] == 0
    assert topo["cdn_fronted_media_assets"] == 0
    assert topo["jwplayer_present"] is True
