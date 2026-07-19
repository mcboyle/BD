"""v3.66.117 — Phase 8 Capture Intelligence (read-only, posture-safe).

The posture tests are load-bearing: metadata only — no signing VALUES, no raw URLs,
no capture reassembly, uses the posture-safe descriptor lens. Plus pure scoring
correctness (coverage/quality/completeness/missing-evidence) and wiring.
"""
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


class TestPostureSafe:
    def test_no_reassembly_or_signing_value_constructs(self):
        # check for actual reassembly/replay/signing CONSTRUCTS, not the posture
        # words the module's docstring legitimately uses ("never reassembles…")
        for bad in ("bd_dev_inspect", "stitch_segments(", "_reassemble(",
                    ".replay(", "requests.", "page.goto", "web_fetch",
                    "decrypt(", "compute_signature("):
            assert bad not in _SRC, f"capture intel must not use {bad!r}"

    def test_uses_posture_safe_descriptor_lens(self):
        # signing/renditions come from the posture-safe _descriptors_of (names only)
        assert "_descriptors_of" in _SRC

    def test_signing_is_names_only_in_score(self):
        meta = {"loaded": True, "has_network": True, "network_events": 10,
                "media_events": 2, "has_dom": True, "has_cookies": True,
                "renditions": ["1920x1080.mp4"], "signing_markers": ["token", "expires"]}
        sc = ct._score_capture(meta)
        # only marker NAMES are present; never a value
        assert sc["signing_markers"] == ["token", "expires"]
        import json
        blob = json.dumps(sc)
        assert "token=" not in blob and "sig=" not in blob

    def test_read_only_no_writes(self):
        assert "json.dump(" not in _SRC      # 'json.dumps(' is read-only
        assert "write_text" not in _SRC and "_store_save" not in _SRC


class TestScoring:
    def test_rich_capture_scores_high(self):
        meta = {"loaded": True, "has_network": True, "network_events": 40,
                "media_events": 5, "has_dom": True, "has_cookies": True,
                "renditions": ["1920x1080.mp4", "1280x720.mp4"], "signing_markers": []}
        sc = ct._score_capture(meta)
        assert sc["quality"] == 100 and sc["band"] == "rich"
        assert sc["completeness"] == 1.0
        assert sc["missing_evidence"] is None

    def test_thin_capture_flags_missing(self):
        meta = {"loaded": True, "has_network": True, "network_events": 3,
                "media_events": 0, "has_dom": False, "has_cookies": False,
                "renditions": [], "signing_markers": []}
        sc = ct._score_capture(meta)
        assert sc["band"] == "thin"
        assert "no DOM/HTML snapshot" in sc["missing_evidence"]
        assert "no rendition descriptors" in sc["missing_evidence"]

    def test_unreadable_capture_handled(self):
        sc = ct._score_capture({"loaded": False})
        assert sc["band"] == "unreadable" and sc["_unreadable"] is True

    def test_coverage_dimensions_present(self):
        meta = {"loaded": True, "has_network": True, "network_events": 10,
                "media_events": 1, "has_dom": False, "has_cookies": True,
                "renditions": [], "signing_markers": []}
        cov = ct._score_capture(meta)["coverage"]
        for k in ("dom", "network", "template", "drift"):
            assert k in cov and 0.0 <= cov[k] <= 1.0

    def test_media_count_helper(self):
        nl = [{"url": "https://cdn/x/1080p.mp4?token=Z"}, {"url": "https://cdn/a.js"},
              {"url": "https://cdn/y/seg.ts"}]
        assert ct._capture_media_count(nl) == 2  # .mp4 + .ts (query ignored)


class TestIntelligence:
    def test_shape_and_honest_when_empty(self):
        ci = ct.capture_intelligence()
        assert "captures" in ci and "capture_count" in ci and "readable_count" in ci
        assert "average_quality" in ci and "captures_root_present" in ci
        # in an env with no captures, it's honestly empty (not fabricated)
        assert ci["capture_count"] == len(ci["captures"])


class TestWiring:
    def test_endpoint_and_page_present(self):
        assert '@bp.get("/api/template/capture-intel")' in _CONSOLE
        assert "PAGES.captureintel" in _CONSOLE and ('data-p="captureintel"' in _CONSOLE or "captureintel:[" in _CONSOLE)

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/capture-intel").status_code == 200

    def test_no_new_post(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/template/capture-intel" not in posts
