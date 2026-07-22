"""v3.66.84 — bare resolution-suffix rendition signal (VC-0028).

The rendition signal now matches a bare resolution width/height suffix (nubile:
_3840) from the KNOWN resolution set, so nubile's resolution-suffixed filename is a
RENDITION, not a second identity. The known-set gate means ids, segment indices,
and years do not false-match.
"""
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import capture_workbench as wb
from bulk_downloader.capture_synth import synthesize
from capture_test_fixtures import capture_fixture_lane

_FIXTURES = capture_fixture_lane()


def _load(p):
    p = os.fspath(p)
    if p.endswith(".json"):
        return json.load(open(p))
    with zipfile.ZipFile(p) as z:
        n = [x for x in z.namelist() if x.endswith("capture.json")][0]
        return json.loads(z.read(n))


class TestBareResolutionSuffixMatches:
    def test_nubile_style_width_suffix(self):
        assert wb._RENDITION_SIGNAL.search("lilsis_some_title_3840")
        assert wb._RENDITION_SIGNAL.search("clip_480")
        assert wb._RENDITION_SIGNAL.search("movie_1920.mp4")

    def test_existing_signals_still_match(self):
        assert wb._RENDITION_SIGNAL.search("7680x4320_60FPS")
        assert wb._RENDITION_SIGNAL.search("720p")
        assert wb._RENDITION_SIGNAL.search("1280x720")


class TestNoFalsePositives:
    def test_ids_do_not_match(self):
        for s in ("493498581", "53eb2252",
                  "b57891f2-5f04-44c6-ad7c-4513602cb3c9"):
            assert not wb._RENDITION_SIGNAL.search(s), s

    def test_segment_indices_do_not_match(self):
        for s in ("segment_00249", "part_12345", "file_100", "id_999"):
            assert not wb._RENDITION_SIGNAL.search(s), s

    def test_years_do_not_match(self):
        # a year suffix is not in the resolution set
        assert not wb._RENDITION_SIGNAL.search("clip_2024")


class TestNubileNowCorrect:
    def test_slug_identity_filename_rendition(self):
        a, b = "nubile_title1_cap1.wacz", "nubile_title1_cap2.wacz"
        if not _FIXTURES.has(a, b):
            pytest.skip("captures not present")
        sk = wb.build_workbench(synthesize(
            _load(_FIXTURES.path(a)), _load(_FIXTURES.path(b)))).to_dict()["skeleton"]
        ids = [s["sample"] for s in sk["skeleton_slots"] if s["role"] == "identity"]
        rends = [s["sample"] for s in sk["skeleton_slots"] if s["role"] == "rendition"]
        # the slug is the lone identity; the resolution-suffixed filename is a rendition
        assert ids == ["stepsis_gives_me_the_best_birthday_ever"]
        assert any("_3840" in r for r in rends)
