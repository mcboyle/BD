"""v3.66.85 — signing-in-path recognition (VC-0026).

Signing detection previously inspected query parameters only. This release
recognizes signing embedded in a PATH segment (site 't': key=/s=/end=/ip=),
surfaces the markers by name and type, and masks every value. The tests assert
recognition, the explainability record, posture (no value leak in ANY skeleton
field), and that sites without path-signing are unaffected. Recognition-only.
"""
import json
import os
import re
import sys
import zipfile
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import capture_workbench as wb
from bulk_downloader.capture_synth import synthesize
from capture_test_fixtures import capture_fixture_lane

_FIXTURES = capture_fixture_lane()
MEDIA = re.compile(r"\.(mp4|m3u8|ts|webm|mpd|m4s)(\b|$)", re.I)


def _load(p):
    p = os.fspath(p)
    if p.endswith(".json"):
        return json.load(open(p))
    with zipfile.ZipFile(p) as z:
        n = [x for x in z.namelist() if x.endswith("capture.json")][0]
        return json.loads(z.read(n))


def _single(f):
    if not _FIXTURES.has(f):
        pytest.skip("capture not present")
    cap = _load(_FIXTURES.path(f))
    media = [(e.get("seq", 0), e.get("url") or "") for e in (cap.get("network_log") or [])
             if MEDIA.search(urlsplit(e.get("url") or "").path)]
    g = max(media, key=lambda t: t[0])[1]
    return wb.goal_skeleton({"requests": [{"goal": True, "url_template": g}]})


def _pair(a, b):
    if not _FIXTURES.has(a, b):
        pytest.skip("captures not present")
    return wb.build_workbench(synthesize(
        _load(_FIXTURES.path(a)), _load(_FIXTURES.path(b)))).to_dict()["skeleton"]


# ── the masking/recognition helper, unit-level ────────────────────────────────
class TestMaskPathSigning:
    def test_recognizes_signing_segment_and_masks(self):
        masked, rec = wb._mask_path_signing("key=ABC123,s=,end=1780202306,ip=1.2.3.4")
        assert rec is not None
        names = {m["name"]: m["type"] for m in rec["markers"]}
        assert names["key"] == "token"
        assert names["end"] == "expiry"
        assert names["ip"] == "ip-binding"
        # values masked, key names kept
        assert "ABC123" not in masked and "1780202306" not in masked and "1.2.3.4" not in masked
        assert "key=" in masked and "end=" in masked

    def test_plain_literal_unchanged(self):
        masked, rec = wb._mask_path_signing("hls")
        assert masked == "hls" and rec is None

    def test_opaque_kv_masked_but_not_asserted_signing(self):
        # a key=value with no recognized marker is masked (posture) but NOT
        # claimed to be signing (conservative — avoids false signing claims)
        masked, rec = wb._mask_path_signing("reftag=05412169")
        assert "05412169" not in masked
        assert rec is None


# ── end to end on site t ──────────────────────────────────────────────────────
class TestTPathSigning:
    def test_signing_recognized_by_name_and_type(self):
        sk = _single("t_title1_cap2.wacz")
        assert sk["path_signing"], "expected a path_signing record for t"
        markers = {m["name"]: m["type"] for s in sk["path_signing"] for m in s["markers"]}
        assert "key" in markers and "end" in markers and "ip" in markers

    def test_posture_no_raw_values_anywhere_in_skeleton(self):
        sk = _single("t_title1_cap2.wacz")
        blob = json.dumps(sk)
        for raw in ("VaNljMXp6cQWLP20aI7nmQ", "1780202306",
                    "75.165.31.213", "ahuRS6Uf", "05412169"):
            assert raw not in blob, f"signing value leaked: {raw}"

    def test_url_template_is_masked(self):
        sk = _single("t_title1_cap2.wacz")
        assert "<masked>" in sk["url_template"]
        assert "?" not in sk["url_template"]  # query-stripped


# ── no regression on sites without path-signing ───────────────────────────────
class TestNoRegression:
    def test_progressive_and_hls_sites_have_empty_path_signing(self):
        for a, b in [("nubile_title1_cap1.wacz", "nubile_title1_cap2.wacz"),
                     ("filthy_title1_cap1.wacz", "filthy_title1_cap2.wacz"),
                     ("bros_title1_1.wacz", "bros_title1_cap2.wacz")]:
            sk = _pair(a, b)
            assert sk["path_signing"] == []

    def test_clean_literals_unchanged(self):
        sk = _pair("filthy_title1_cap1.wacz", "filthy_title1_cap2.wacz")
        # no '=' in these literals, so masking leaves them exactly as before
        assert "fame" in sk["literal_segments"] and "hls" in sk["literal_segments"]
