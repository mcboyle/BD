"""v3.66.77 — bros sharded-path over-split correction (2nd loop, larger error).

Guards the sharding collapse: a run of >=3 contiguous short opaque identity
segments (CDN path-sharding of one logical id) collapses to a single
sharded-identity slot, and the boundaries hold — single ids are untouched and a
short run (<3) stays split. Recognition-only. Also pins the corpus entry that
records this correction and resolves the VC-0012 debt.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb
from bulk_downloader import validation_corpus as vc
from capture_test_fixtures import capture_fixture_lane


_FIXTURES = capture_fixture_lane(allow_synthetic=True)


def _entry(seq, url):
    return {"seq": seq, "url": url, "method": "GET", "request_headers": {},
            "response_headers": {}, "response_status": 200,
            "response_body": None, "type": "xhr"}


def _skeleton(path):
    def cap(tok):
        return {"host": "x.test", "network_log": [
            _entry(1, "https://x.test/p"),
            _entry(9, f"https://cdn.x.test/{path}?sig={tok}")]}
    return wb.build_workbench(synthesize(cap("A"), cap("B"))).to_dict()["skeleton"]


def _identity_slots(sk):
    return [s for s in sk["skeleton_slots"] if s["role"] == "identity"]


class TestShardingCollapse:
    def test_run_collapses_to_one_slot(self):
        sk = _skeleton("hls/3ca/96d/1e8/74c/40a/02f/8de/video/1080p.mp4")
        ids = _identity_slots(sk)
        assert len(ids) == 1
        assert ids[0].get("sharded") is True
        assert ids[0]["sharded_segment_count"] == 7
        assert "{content_id}" in sk["path_template"]
        assert "{content_id2}" not in sk["path_template"]

    def test_collapsed_sample_preserves_run(self):
        sk = _skeleton("a/3ca/96d/1e8/74c/x_720p.mp4")
        ids = _identity_slots(sk)[0]
        assert ids_sample(ids) == "3ca/96d/1e8/74c"

    def test_generalizes_to_different_scheme(self):
        # different values/length than the bros 3-char scheme
        sk = _skeleton("v/4f/2c/9a/1d/7e/clip_1080p.mp4")
        ids = _identity_slots(sk)
        assert len(ids) == 1 and ids[0].get("sharded")

    def test_single_long_id_not_collapsed(self):
        sk = _skeleton("a/9c8e2f1b7d4a6c3e/720p.mp4")
        ids = _identity_slots(sk)
        assert len(ids) == 1 and not ids[0].get("sharded")

    def test_short_run_below_floor_not_collapsed(self):
        # only two short segments — below the >=3 sharding floor
        sk = _skeleton("a/12/34/x_1080p.mp4")
        assert not any(s.get("sharded") for s in sk["skeleton_slots"])

    def test_regex_matches_full_run(self):
        import re
        sk = _skeleton("hls/3ca/96d/1e8/74c/40a/v_720p.mp4")
        ids = _identity_slots(sk)[0]
        assert re.fullmatch(ids["regex"], "3ca/96d/1e8/74c/40a")


def ids_sample(slot):
    return slot["sample"]


class TestRealBros:
    def _bros(self):
        import json
        import zipfile

        def load(p):
            with zipfile.ZipFile(p) as z:
                n = [x for x in z.namelist() if x.endswith("capture.json")][0]
                return json.loads(z.read(n))
        a = "bros_title1_1.wacz"
        b = "bros_title1_cap2.wacz"
        if not _FIXTURES.has(a, b):
            pytest.skip("bros captures not present in this environment")
        return wb.build_workbench(synthesize(
            load(_FIXTURES.path(a)), load(_FIXTURES.path(b)))).to_dict()

    def test_bros_collapses_to_one_identity(self):
        d = self._bros()
        ids = [s for s in d["skeleton"]["skeleton_slots"]
               if s["role"] == "identity"]
        assert len(ids) == 1 and ids[0]["sharded_segment_count"] == 11


class TestCorpusEntry:
    def test_bros_correction_resolves_oversplit_debt(self):
        s = vc.summarize(vc.load_corpus())
        assert any("VC-0012" in r["resolves"] for r in s["resolved_corrections"])
        assert not any(p["subject"] == "segment_role_oversplits_sharded_paths"
                       for p in s["pending_corrections"])

    def test_bros_correction_records_generalization(self):
        corr = vc.query(vc.load_corpus(),
                        subject="bros_sharded_path_oversplit_collapse")
        assert corr and corr[0].get("correction")
        c = corr[0]["correction"]
        assert c.get("generalized") and c.get("prediction_matched")
        assert c.get("root_cause") and c.get("expected_effect")
