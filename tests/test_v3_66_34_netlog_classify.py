"""Network-log classifier tests — v3.66.34.

The load-bearing invariant: a signed (access-controlled, short-lived)
media URL is NEVER returned as a download candidate. Signed streams are
reported with their signing status and expiry so the operator knows
what a capture contains; only genuinely unsigned direct media passes
through as a candidate. These tests pin that line so it can't regress.
"""
import glob
import json
import os

import pytest

from bulk_downloader.netlog_classify import (
    classify_network_log, MediaItem, CaptureReport,
    KIND_HLS_MANIFEST, KIND_DASH_MANIFEST, KIND_HLS_SEGMENT, KIND_DIRECT,
)


_CORPUS = os.path.join(os.path.dirname(__file__),
                       "fixtures", "recon_corpus")


# ── synthetic log fixtures ──────────────────────────────────────────

def _entry(url, ct, status=200):
    return {"url": url, "response_headers": {"content-type": ct},
            "response_status": status}


class TestKindClassification:
    def test_hls_manifest(self):
        rep = classify_network_log(
            [_entry("https://v/x.m3u8", "application/vnd.apple.mpegurl")])
        assert rep.items[0].kind == KIND_HLS_MANIFEST

    def test_dash_manifest(self):
        rep = classify_network_log(
            [_entry("https://v/x.mpd", "application/dash+xml")])
        assert rep.items[0].kind == KIND_DASH_MANIFEST

    def test_segment(self):
        rep = classify_network_log(
            [_entry("https://v/seg/4316.ts", "video/mp2t")])
        assert rep.items[0].kind == KIND_HLS_SEGMENT

    def test_direct_media(self):
        rep = classify_network_log(
            [_entry("https://v/clip.mp4", "video/mp4")])
        assert rep.items[0].kind == KIND_DIRECT

    def test_non_media_ignored(self):
        rep = classify_network_log([
            _entry("https://api/x/index?limit=48", "application/json"),
            _entry("https://ga/collect", "text/plain"),
        ])
        assert rep.items == []


class TestSigningDetection:
    def test_key_marker_is_signed(self):
        rep = classify_network_log(
            [_entry("https://v/key=abc,end=1778807746/720p/1.ts",
                    "video/mp2t")])
        assert rep.items[0].signed is True

    def test_expiry_extracted(self):
        rep = classify_network_log(
            [_entry("https://v/key=abc,end=1778807746,limit=3/1.ts",
                    "video/mp2t")])
        assert rep.items[0].expiry_epoch == 1778807746

    def test_scrub_placeholder_counts_as_signed(self):
        # A scrubbed token means a token WAS there → still signed.
        rep = classify_network_log(
            [_entry("https://v/key=<scrubbed>/720p/1.ts", "video/mp2t")])
        assert rep.items[0].signed is True

    def test_plain_url_is_unsigned(self):
        rep = classify_network_log(
            [_entry("https://cdn/trailers/tr_1_sm.mp4", "video/mp4")])
        assert rep.items[0].signed is False

    def test_ms_expiry_normalized_to_seconds(self):
        rep = classify_network_log(
            [_entry("https://v/seg.ts?end=1778807746000", "video/mp2t")])
        assert rep.items[0].expiry_epoch == 1778807746

    def test_aws_sigv4_full_is_signed(self):
        # AWS SigV4 query params are hyphenated (X-Amz-Signature=, …). The
        # general marker branch anchors on a boundary char + '=', which the
        # hyphenated form defeats; the dedicated x-amz-[a-z-]+= branch (added
        # v3.66.55) catches it. Regression guard for that fix — without it
        # SigV4-signed media reads as a pass-through candidate (posture bug).
        rep = classify_network_log([_entry(
            "https://b.s3.amazonaws.com/v.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Date=20200101T000000Z&X-Amz-Expires=3600"
            "&X-Amz-Signature=abc", "video/mp4")])
        assert rep.items[0].signed is True

    def test_aws_sigv4_signature_param_alone_is_signed(self):
        rep = classify_network_log([_entry(
            "https://b.s3.amazonaws.com/v.mp4?X-Amz-Signature=deadbeef"
            "&X-Amz-Credential=AKIA", "video/mp4")])
        assert rep.items[0].signed is True

    def test_hyphenated_non_aws_param_not_falsely_signed(self):
        # The SigV4 branch must NOT loosen detection for unrelated hyphenated
        # params — a plain media URL with e.g. a cache-buster stays unsigned.
        rep = classify_network_log(
            [_entry("https://cdn/trailers/tr.mp4?cache-bust=5", "video/mp4")])
        assert rep.items[0].signed is False


class TestDRM:
    def test_drm_marker_flagged(self):
        rep = classify_network_log(
            [_entry("https://drm/license/widevine?x.mpd",
                    "application/dash+xml")])
        assert rep.items[0].drm is True

    def test_drm_never_a_candidate(self):
        rep = classify_network_log(
            [_entry("https://drm/license/widevine/clip.mp4", "video/mp4")])
        assert rep.candidates() == []


# ── THE invariant: signed media never becomes a candidate ───────────

class TestCandidateInvariant:
    def test_signed_segment_not_a_candidate(self):
        rep = classify_network_log(
            [_entry("https://v/key=abc,end=1778807746/720p/1.ts",
                    "video/mp2t")])
        assert rep.candidates() == []

    def test_signed_manifest_not_a_candidate(self):
        rep = classify_network_log(
            [_entry("https://v/key=abc,end=1778807746/media=hls/x.m3u8",
                    "application/vnd.apple.mpegurl")])
        assert rep.candidates() == []

    def test_unsigned_segment_not_a_candidate(self):
        # Even unsigned, a lone segment isn't a usable standalone
        # download and reassembling the set is the declined path.
        rep = classify_network_log(
            [_entry("https://v/plain/1.ts", "video/mp2t")])
        assert rep.candidates() == []

    def test_only_unsigned_direct_media_passes(self):
        rep = classify_network_log([
            _entry("https://cdn/trailers/tr_1_sm.mp4", "video/mp4"),
            _entry("https://v/key=abc,end=1778807746/720p/1.ts", "video/mp2t"),
            _entry("https://v/stream.m3u8", "application/vnd.apple.mpegurl"),
        ])
        assert rep.candidates() == ["https://cdn/trailers/tr_1_sm.mp4"]

    def test_mixed_log_invariant(self):
        rep = classify_network_log([
            _entry("https://cdn/open.mp4", "video/mp4"),
            _entry("https://v/key=k,end=1778807746/1.ts", "video/mp2t"),
            _entry("https://v/key=k,end=1778807746/2.ts", "video/mp2t"),
            _entry("https://drm/widevine/x.mpd", "application/dash+xml"),
        ])
        signed_or_drm = {i.url for i in rep.items if i.signed or i.drm}
        for c in rep.candidates():
            assert c not in signed_or_drm


# ── corpus characterization ─────────────────────────────────────────

class TestCorpus:
    def _files(self):
        return sorted(glob.glob(os.path.join(_CORPUS, "*.json")))

    def test_corpus_present(self):
        assert self._files()

    def test_no_signed_url_ever_in_candidates_across_corpus(self):
        for f in self._files():
            d = json.load(open(f, encoding="utf-8"))
            rep = classify_network_log(d)
            signed = {i.url for i in rep.items if i.signed or i.drm}
            for c in rep.candidates():
                assert c not in signed, (
                    f"signed URL leaked to candidates in {os.path.basename(f)}")

    def test_corpus_finds_signed_streams(self):
        # The corpus is dominated by signed streams — the classifier
        # should be reporting them (just not as candidates).
        total_signed = 0
        for f in self._files():
            d = json.load(open(f, encoding="utf-8"))
            rep = classify_network_log(d)
            total_signed += len(rep.signed_items)
        assert total_signed > 0, "expected signed media in the corpus"

    def test_report_as_dict_serializable(self):
        d = json.load(open(self._files()[0], encoding="utf-8"))
        rep = classify_network_log(d)
        out = rep.as_dict()
        json.dumps(out)  # must be JSON-serializable
        assert "summary" in out and "candidates" in out
