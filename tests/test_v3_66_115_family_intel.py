"""v3.66.115 — Phase 6 Family Intelligence (cross-site family analysis).

Read-only. Posture + shared-selector aggregation correctness + cross-pollination
(family-common selectors a member lacks, data-only) + workflow/drift/failure
sharing + wiring (+2 GET routes = 93, no POSTs).
"""
import json, os, tempfile
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


def _with_config(cfg_list, fn):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(cfg_list, f); f.close()
    os.environ["BD_SITES_CONFIG_PATH"] = f.name
    try:
        return fn()
    finally:
        os.environ.pop("BD_SITES_CONFIG_PATH", None)


# a 3-site JWPlayer family: jwA/jwB share two selectors that jwC lacks
_JW_FAMILY = [
    {"id": "jwA", "learned": {"download": {"row_selectors": [".jw-overlays a[href$='.mp4']", ".jwplayer source[label]"],
                                           "url_attribute": "src", "trigger_selectors": [".jw-icon-quality"]}}},
    {"id": "jwB", "learned": {"download": {"row_selectors": [".jw-overlays a[href$='.mp4']", ".jwplayer source[label]"],
                                           "url_attribute": "src", "trigger_selectors": [".jw-icon-quality"]}}},
    {"id": "jwC", "learned": {"download": {"row_selectors": [".jwplayer source[label]"], "url_attribute": "src"}}},
]


class TestPosture:
    def test_read_only_no_writes(self):
        assert "json.dump(" not in _SRC      # 'json.dumps(' is read-only serialization
        assert "write_text" not in _SRC
        assert "_store_save" not in _SRC

    def test_no_live_fetch_or_replay(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    ".fill(", ".click(", "ollama."):
            assert bad not in _SRC, f"family intel must not use {bad!r}"

    def test_cross_pollination_is_data_only(self):
        fd = _with_config(_JW_FAMILY, lambda: ct.family_detail("jwplayer"))
        for c in fd["cross_pollination"]:
            assert c["applies_automatically"] is False
        for bad in ("def apply", "def promote", "auto_apply", "_apply_detected_selectors"):
            assert bad not in _SRC


class TestSharedSelectors:
    def test_shared_requires_two_members(self):
        fi = _with_config(_JW_FAMILY, lambda: ct.family_intelligence())
        jw = next(f for f in fi["families"] if f["family"] == "jwplayer")
        assert jw["member_count"] == 3
        # the selector all 3 share + the two that 2 share = 3 shared download selectors
        assert jw["shared_download_selectors"] == 3

    def test_shared_selector_counts(self):
        fd = _with_config(_JW_FAMILY, lambda: ct.family_detail("jwplayer"))
        by_sel = {s["selector"]: s["used_by"] for s in fd["shared_download_selectors"]}
        assert by_sel[".jwplayer source[label]"] == 3
        assert by_sel[".jw-icon-quality"] == 2

    def test_single_member_family_shares_nothing(self):
        cfg = _JW_FAMILY + [{"id": "vjsX", "learned": {"download": {"row_selectors": ["video.video-js source"]}}}]
        fi = _with_config(cfg, lambda: ct.family_intelligence())
        vjs = next(f for f in fi["families"] if f["family"] == "video.js")
        assert vjs["member_count"] == 1
        assert vjs["shared_download_selectors"] == 0


class TestCrossPollination:
    def test_member_missing_common_selector_is_flagged(self):
        fd = _with_config(_JW_FAMILY, lambda: ct.family_detail("jwplayer"))
        cp = {c["site"]: c for c in fd["cross_pollination"]}
        # jwC lacks the two selectors jwA/jwB share (majority threshold met)
        assert "jwC" in cp
        missing = set(cp["jwC"]["missing_common_download_selectors"])
        assert ".jw-icon-quality" in missing
        assert ".jw-overlays a[href$='.mp4']" in missing

    def test_members_with_all_common_selectors_not_flagged(self):
        fd = _with_config(_JW_FAMILY, lambda: ct.family_detail("jwplayer"))
        flagged = {c["site"] for c in fd["cross_pollination"]}
        # jwA and jwB have every family-common selector
        assert "jwA" not in flagged and "jwB" not in flagged


class TestWorkflowAndShape:
    def test_workflow_two_step_fraction(self):
        fd = _with_config(_JW_FAMILY, lambda: ct.family_detail("jwplayer"))
        # 2 of 3 members have trigger_selectors
        assert fd["workflow"]["two_step_fraction"] == round(2 / 3, 2)
        assert fd["workflow"]["common_url_attribute"] == "src"

    def test_family_intelligence_shape(self):
        fi = _with_config(_JW_FAMILY, lambda: ct.family_intelligence())
        assert "families" in fi and "family_count" in fi
        for f in fi["families"]:
            for k in ("family", "member_count", "members", "workflow",
                      "shared_drift_patterns", "shared_failure_modes"):
                assert k in f

    def test_missing_family_errors(self):
        assert "error" in ct.family_detail("")
        assert "error" in _with_config(_JW_FAMILY, lambda: ct.family_detail("nope"))


class TestWiring:
    def test_endpoints_and_page_present(self):
        for ep in ("family-intel", "family"):
            assert f'@bp.get("/api/template/{ep}")' in _CONSOLE
        assert "PAGES.familyintel" in _CONSOLE and ('data-p="familyintel"' in _CONSOLE or "familyintel:[" in _CONSOLE)
        assert "function famLoad" in _CONSOLE

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/family-intel").status_code == 200
        assert c.get("/cockpit/api/template/family?name=x").status_code == 200

    def test_no_new_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for ep in ("family-intel", "family"):
            assert f"/cockpit/api/template/{ep}" not in posts
