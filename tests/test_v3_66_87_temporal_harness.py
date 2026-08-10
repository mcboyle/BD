"""v3.66.87 — temporal validation harness (same-title drift).

Tests the four-axis drift measurement and the VC-0019 floor test on real captures.
The two boundary behaviors that matter most are asserted directly: signing drift is
UNTESTED (not falsified) when the values were scrubbed at capture time, and the
VC-0019 floor is UNTESTED at N=2 and only confirmed at N>=3 with real same-title
data. Recognition-only — no signing value appears in the report.
"""
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import temporal_harness as th
from capture_test_fixtures import capture_fixture_lane

_FIXTURES = capture_fixture_lane(allow_synthetic=True)

# the real N=3 ultrafilms title1 series and an N=2 pair
ULTRA_SERIES = ["capA.json", "ultrafilms_title1_later.wacz",
                "yultrafilms_title1_later.wacz"]
NUBILE_PAIR = ["nubile_title1_cap1.wacz", "nubile_title1_cap2.wacz"]


def _load(p):
    p = os.fspath(p)
    if p.endswith(".json"):
        return json.load(open(p))
    with zipfile.ZipFile(p) as z:
        n = [x for x in z.namelist() if x.endswith("capture.json")][0]
        return json.loads(z.read(n))


def _series(files):
    if not _FIXTURES.has(*files):
        pytest.skip("captures not present")
    return th.drift_series(
        [_load(_FIXTURES.path(f)) for f in files], labels=files)


class TestUltrafilmsN3:
    def test_identity_invariant_confirmed(self):
        assert _series(ULTRA_SERIES)["axes"]["identity"]["outcome"] == th.CONFIRMED

    def test_rendition_varies_attributed_to_rendition(self):
        # different renditions across captures, identity stable -> confirmed
        assert _series(ULTRA_SERIES)["axes"]["rendition"]["outcome"] == th.CONFIRMED

    def test_structural_stable_confirmed(self):
        assert _series(ULTRA_SERIES)["axes"]["structural"]["outcome"] == th.CONFIRMED

    def test_signing_untested_when_values_redacted(self):
        # expires/token were scrubbed at capture -> drift undeterminable, NOT a
        # false "no drift"
        rep = _series(ULTRA_SERIES)
        assert rep["axes"]["signing"]["outcome"] == th.UNTESTED
        assert all(v == "undeterminable_redacted"
                   for v in rep["signing_drift_by_marker"].values())

    def test_floor_confirmed_with_real_n3_data(self):
        floor = _series(ULTRA_SERIES)["vc_0019_floor"]
        assert floor["outcome"] == th.CONFIRMED
        assert floor["qualifying_data"] is True
        # scope is stability, not specificity — recorded honestly
        assert "specificity" in floor["scope"]


class TestN2FloorUntested:
    def test_floor_untested_at_n2(self):
        floor = _series(NUBILE_PAIR)["vc_0019_floor"]
        assert floor["outcome"] == th.UNTESTED
        assert floor["qualifying_data"] is False

    def test_signing_drift_measurable_when_values_present(self):
        # nubile e/st are present (not redacted) and differ between sessions
        rep = _series(NUBILE_PAIR)
        assert rep["axes"]["signing"]["outcome"] == th.CONFIRMED


class TestPosture:
    def test_no_signing_values_in_report(self):
        rep = _series(ULTRA_SERIES)
        blob = json.dumps(rep)
        # no query string, no raw token/expiry value leaks
        assert "?" not in blob or "token=" not in blob
        assert "expires=" not in blob and "token=" not in blob

    def test_too_few_captures_errors_cleanly(self):
        out = th.drift_series([{"network_log": []}])
        assert "error" in out
