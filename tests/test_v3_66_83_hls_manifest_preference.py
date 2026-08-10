"""v3.66.83 — HLS manifest-preference (VC-0014) + goal-selection explainability.

Goal-selection prefers an HLS/DASH manifest over its segments so the existing
classify_url hls_manifest recognition fires (bros). When no manifest was captured
it falls back to highest-seq media, transparently, via the goal_selection record.
Recognition-only — the signed manifest is surfaced, never reassembled.
"""
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize, _media_kind
from bulk_downloader import capture_workbench as wb
from capture_test_fixtures import capture_fixture_lane

_FIXTURES = capture_fixture_lane(allow_synthetic=True)


def _load(p):
    p = os.fspath(p)
    if p.endswith(".json"):
        return json.load(open(p))
    with zipfile.ZipFile(p) as z:
        n = [x for x in z.namelist() if x.endswith("capture.json")][0]
        return json.loads(z.read(n))


def _synth(a, b):
    if not _FIXTURES.has(a, b):
        pytest.skip("captures not present")
    return synthesize(_load(_FIXTURES.path(a)), _load(_FIXTURES.path(b)))


class TestMediaKind:
    def test_manifest_extensions(self):
        assert _media_kind("https://x/y/index.m3u8?sig=1") == "manifest"
        assert _media_kind("https://x/y/stream.mpd") == "manifest"

    def test_segment_extensions(self):
        assert _media_kind("https://x/y/seg_00001.ts") == "segment"
        assert _media_kind("https://x/y/chunk.m4s") == "segment"

    def test_progressive(self):
        assert _media_kind("https://x/y/movie_1080p.mp4") == "progressive"


class TestManifestPreferred:
    def test_bros_prefers_manifest(self):
        syn = _synth("bros_title1_1.wacz", "bros_title1_cap2.wacz")
        gs = syn["goal_selection"]
        assert gs["reason"] == "hls_manifest_preferred"
        assert gs["selected"]["kind"] == "manifest"
        assert len(gs["manifest_candidates"]) >= 1
        # the goal request classifies as an HLS manifest (existing classify_url)
        d = wb.build_workbench(syn).to_dict()
        assert d["impact"]["goal_classification"]["type"] == "hls_manifest"

    def test_bros_records_segments_considered(self):
        gs = _synth("bros_title1_1.wacz", "bros_title1_cap2.wacz")["goal_selection"]
        assert gs["n_segment_candidates"] >= 1  # segments were considered and lost


class TestTransparentFallback:
    def test_filthy_falls_back_no_manifest_captured(self):
        # filthy's CloudFront-signed manifest was not in the capture; the record
        # makes the fallback explicit rather than silent
        gs = _synth("filthy_title1_cap1.wacz", "filthy_title1_cap2.wacz")["goal_selection"]
        assert gs["reason"] == "highest_seq_media"
        assert gs["manifest_candidates"] == []


class TestProgressiveUnchanged:
    def test_ultrafilms_no_manifest(self):
        gs = _synth("capA.json", "yultrafilms_title1_later.wacz")["goal_selection"]
        assert gs["reason"] == "highest_seq_media"
        assert gs["selected"]["kind"] == "progressive"


class TestExplainabilityAndPosture:
    def test_goal_selection_record_shape(self):
        gs = _synth("bros_title1_1.wacz", "bros_title1_cap2.wacz")["goal_selection"]
        for k in ("reason", "selected", "manifest_candidates",
                  "segment_candidates", "n_segment_candidates"):
            assert k in gs

    def test_candidate_urls_are_query_stripped(self):
        # posture: no signing values in the surfaced candidate URLs
        gs = _synth("bros_title1_1.wacz", "bros_title1_cap2.wacz")["goal_selection"]
        for c in gs["manifest_candidates"] + gs["segment_candidates"] + [gs["selected"]]:
            assert "?" not in c["url"]
