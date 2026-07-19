"""v3.66.74 — multi-site generalization benchmark.

Proves the comparison on SYNTHETIC sites with different schemes: a reusable
class shared by two sites is confirmed; a class on one site is site-local; a
robust conclusion present on all sites is framework-level; a goal classification
that varies is a held site-specific label; an inflated identity-slot count is
flagged as an over-split anomaly. Recognition-only — signing compared by name.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bulk_downloader import multi_site_benchmark as ms


def _profile(**over):
    base = dict(goal_host="h", goal_classification="direct_file",
                new_provider_required=False, signing_params=["token"],
                identity_slot_count=1, rendition_slot_count=1,
                reusable_classes=["telemetry", "signing"],
                framework_level_count=3, site_specific_count=10,
                sensitivity_robust=["assume:goal_selection"])
    base.update(over)
    return base


class TestCompare:
    def test_reusable_reproduced_vs_site_local(self):
        profiles = {
            "a": _profile(reusable_classes=["telemetry", "signing"]),
            "b": _profile(reusable_classes=["telemetry", "local_only"]),
        }
        c = ms.compare_sites(profiles)
        assert "telemetry" in c["reusable_classes_reproduced"]  # on both
        assert "signing" in c["reusable_classes_site_local"]    # only a
        assert "local_only" in c["reusable_classes_site_local"]  # only b

    def test_framework_level_robust_across_all(self):
        profiles = {"a": _profile(), "b": _profile(), "c": _profile()}
        c = ms.compare_sites(profiles)
        assert c["sensitivity_robust_across_all"] == ["assume:goal_selection"]

    def test_robust_not_across_if_one_site_differs(self):
        profiles = {"a": _profile(),
                    "b": _profile(sensitivity_robust=["assume:other"])}
        c = ms.compare_sites(profiles)
        assert c["sensitivity_robust_across_all"] == []

    def test_goal_classification_varies_is_confirmed_site_specific(self):
        profiles = {"a": _profile(goal_classification="direct_file"),
                    "b": _profile(goal_classification="unknown")}
        c = ms.compare_sites(profiles)
        assert c["goal_classification_varies"] is True
        assert "goal_classification" in c["verdict"]["confirmed_site_specific"]

    def test_over_split_flagged(self):
        profiles = {"clean": _profile(identity_slot_count=1),
                    "sharded": _profile(identity_slot_count=11)}
        c = ms.compare_sites(profiles)
        assert "sharded" in c["over_split_flag"]
        assert c["over_split_flag"]["sharded"] == 11
        assert any("sharded" in a for a in c["verdict"]["anomalies"])
        assert "clean" not in c["over_split_flag"]

    def test_segment_role_reproduced_all_sites(self):
        profiles = {"a": _profile(identity_slot_count=1),
                    "b": _profile(identity_slot_count=2)}
        assert ms.compare_sites(profiles)["segment_role_reproduced_all_sites"]

    def test_distinct_signing_schemes_counted(self):
        profiles = {
            "uf": _profile(signing_params=["expires", "token"]),
            "fk": _profile(signing_params=["Key-Pair-Id", "Policy", "Signature"]),
            "br": _profile(signing_params=["hash"]),
        }
        assert ms.compare_sites(profiles)["distinct_signing_schemes"] == 3


class TestProfile:
    def test_profile_extracts_roles_and_signing(self):
        wb = {
            "skeleton": {"host": "cdn.test", "skeleton_slots": [
                {"name": "content_id", "role": "identity"},
                {"name": "rendition", "role": "rendition"}],
                "signing_params": [{"param": "token"}, {"param": "expires"}]},
            "impact": {"goal_classification": {"type": "direct_file"},
                       "new_provider_required": False},
            "generalization": {"reusable_classes": [{"class": "telemetry"}],
                               "framework_level": [1, 2], "site_specific": [1]},
            "sensitivity": {"robust_conclusions": ["assume:goal_selection"]},
        }
        p = ms.site_profile(wb)
        assert p["identity_slot_count"] == 1 and p["rendition_slot_count"] == 1
        assert p["signing_params"] == ["token", "expires"]
        assert p["goal_classification"] == "direct_file"
        assert p["sensitivity_robust"] == ["assume:goal_selection"]


class TestPosture:
    def test_comparison_carries_only_signing_names(self):
        # signing is compared by NAME; values must never enter the comparison
        profiles = {"a": _profile(signing_params=["Signature", "Policy"])}
        c = ms.compare_sites(profiles)
        blob = str(c)
        assert "Signature" in blob  # the NAME is fine
        # a value-looking blob would be a leak; profiles never carry values
        assert "=" not in str(c["signing_schemes_by_site"])
