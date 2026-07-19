"""v3.66.82 — confidence-weighted candidate admission (first framework-level
candidate-generation change).

Encodes the corpus acceptance criteria as regression tests: the nubile loss is
retired (slug admitted via filename echo), the t over-generation is retired
(path signing/routing demoted below the floor), bros still collapses, the opaque-id
sites are unchanged, and the audited scaffolding stays literal. Also unit-tests the
scoring function directly. Recognition-only.
"""
import json
import os
import re
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader.capture_synth import synthesize
from bulk_downloader import capture_workbench as wb

U = "/mnt/user-data/uploads/"
MEDIA = re.compile(r"\.(mp4|m3u8|ts|webm|mpd|m4s)(\b|$)", re.I)


def _load(p):
    if p.endswith(".json"):
        return json.load(open(p))
    with zipfile.ZipFile(p) as z:
        n = [x for x in z.namelist() if x.endswith("capture.json")][0]
        return json.loads(z.read(n))


def _pair(a, b):
    if not (os.path.exists(U + a) and os.path.exists(U + b)):
        pytest.skip("captures not present")
    return wb.build_workbench(synthesize(_load(U + a), _load(U + b))).to_dict()["skeleton"]


def _single(f):
    if not os.path.exists(U + f):
        pytest.skip("capture not present")
    cap = _load(U + f)
    from urllib.parse import urlsplit
    media = [(e.get("seq", 0), e.get("url") or "") for e in (cap.get("network_log") or [])
             if MEDIA.search(urlsplit(e.get("url") or "").path)]
    g = max(media, key=lambda t: t[0])[1]
    return wb.goal_skeleton({"requests": [{"goal": True, "url_template": g}]})


def _ids(sk):
    return [s["sample"] for s in sk["skeleton_slots"] if s["role"] == "identity"]


# ── the scoring function, unit-level ──────────────────────────────────────────
class TestScoring:
    def test_id_shape_positive(self):
        score, pos, neg = wb._candidate_signals("b57891f2-5f04-44c6-ad7c-4513602cb3c9", "")
        assert "id_shape" in pos and score >= wb._CANDIDATE_FLOOR

    def test_filename_echo_recovers_readable_slug(self):
        slug = "stepsis_gives_me_the_best_birthday_ever"
        fname = "lilsis_stepsis_gives_me_the_best_birthday_ever_3840.mp4"
        score, pos, neg = wb._candidate_signals(slug, fname)
        assert "filename_echo" in pos and score >= wb._CANDIDATE_FLOOR

    def test_key_value_form_demoted(self):
        score, pos, neg = wb._candidate_signals("reftag=05412169", "")
        assert "signing_or_kv" in neg and score < wb._CANDIDATE_FLOOR

    def test_tiny_numeric_demoted(self):
        # a 1-2 digit routing index nets below the floor despite id-shape
        score, pos, neg = wb._candidate_signals("12", "")
        assert "tiny_numeric" in neg and score < wb._CANDIDATE_FLOOR

    def test_storage_hint_demoted(self):
        score, pos, neg = wb._candidate_signals("ssd1", "")
        assert "storage_hint" in neg and score < wb._CANDIDATE_FLOOR

    def test_scaffolding_word_below_floor(self):
        for w in ("fame", "hls", "videos", "exclusive", "video"):
            score, pos, neg = wb._candidate_signals(w, "preview.mp4")
            assert score < wb._CANDIDATE_FLOOR, w


# ── corpus acceptance, end to end ─────────────────────────────────────────────
class TestNubileLossRetired:
    def test_slug_now_promoted(self):
        sk = _pair("nubile_title1_cap1.wacz", "nubile_title1_cap2.wacz")
        assert "stepsis_gives_me_the_best_birthday_ever" in _ids(sk)

    def test_slug_carries_filename_echo_signal(self):
        sk = _pair("nubile_title1_cap1.wacz", "nubile_title1_cap2.wacz")
        slug = next(s for s in sk["skeleton_slots"]
                    if s["sample"] == "stepsis_gives_me_the_best_birthday_ever")
        assert "filename_echo" in slot_positives(slug)


def slot_positives(s):
    return s.get("positive_signals", [])


class TestTOverGenerationRetired:
    def test_only_one_real_identity(self):
        sk = _single("t_title1_cap2.wacz")
        assert _ids(sk) == ["493498581"]

    def test_signing_and_routing_demoted_to_literal(self):
        sk = _single("t_title1_cap2.wacz")
        lits = sk.get("literal_segments", [])
        # the key=value signing/routing segments are no longer identities
        assert any(l.startswith("reftag=") for l in lits)
        assert any(l.startswith("state=") for l in lits)
        assert "ssd1" in lits and "12" in lits


class TestBrosCollapseRetained:
    def test_one_sharded_identity(self):
        sk = _pair("bros_title1_1.wacz", "bros_title1_cap2.wacz")
        ids = [s for s in sk["skeleton_slots"] if s["role"] == "identity"]
        assert len(ids) == 1 and ids[0].get("sharded")


class TestOpaqueIdNoRegression:
    def test_ultrafilms_identity_unchanged(self):
        sk = _pair("capA.json", "yultrafilms_title1_later.wacz")
        assert "53eb2252" in _ids(sk)

    def test_filthy_uuid_identity_unchanged(self):
        sk = _pair("filthy_title1_cap1.wacz", "filthy_title1_cap2.wacz")
        assert any("-" in i and len(i) == 36 for i in _ids(sk))


class TestVisibility:
    def test_promoted_slots_carry_score_and_signals(self):
        sk = _pair("filthy_title1_cap1.wacz", "filthy_title1_cap2.wacz")
        for s in sk["skeleton_slots"]:
            assert "score" in s
            assert "positive_signals" in s and "negative_signals" in s
