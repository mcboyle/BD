"""v3.66.110 — Template Intelligence (priority 1: video template health +
download decision explainer).

The posture boundary tests are the load-bearing ones: no credential values, no
signing values, query-stripped URLs, pure scorer (no live fetch/model/replay), no
config writes, no auto-application. Plus correctness of the scoring narration.
"""
import json
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")


class TestPostureBoundaries:
    def test_module_writes_nothing(self):
        # template intelligence is strictly read-only — no writes anywhere
        for bad in (".write(", "write_text", "json.dump(", ".replace(", "open("):
            # 'open(' is allowed only as read; assert no write-mode opens
            pass
        assert "write_text" not in _SRC
        assert "json.dump(" not in _SRC
        assert '"w"' not in _SRC and "'w'" not in _SRC  # no write-mode file opens

    def test_no_live_fetch_or_model_or_replay(self):
        # check for actual fetch/replay/model CONSTRUCTS, not the boundary words
        # (the module's docstring legitimately says "no request replay")
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", "ollama.", ".replay("):
            assert bad not in _SRC, f"template intel must not use {bad!r}"

    def test_urls_are_query_stripped(self):
        # signing lives in the query string — the explainer must strip it
        ex = ct.download_decision_explainer()
        for c in ex["candidates"]:
            assert "?" not in c["url"], f"unstripped url leaked: {c['url']}"
        if ex["chosen"]:
            assert "?" not in ex["chosen"]["url"]

    def test_no_signing_or_credential_values_in_output(self):
        # feed a candidate whose href carries a signing query + a token-ish field;
        # neither the value nor the query may appear in the output
        cands = [{"text": "Download 1080p",
                  "href": "https://cdn.example.com/v/abc/1080p.mp4?token=SECRET123&sig=abcdef",
                  "tag": "a", "data_download": "1"}]
        ex = ct.download_decision_explainer(candidates=cands)
        blob = json.dumps(ex)
        assert "SECRET123" not in blob and "sig=abcdef" not in blob and "token=" not in blob

    def test_health_does_not_echo_query_strings(self):
        vh = ct.video_template_health()
        blob = json.dumps(vh)
        # any last_url surfaced must be query-stripped
        for s in vh["sites"]:
            assert "?" not in (s["drift"]["last_url"] or "")

    def test_suggested_updates_not_auto_applied(self):
        # there is no apply/promote path in this module — it returns data only
        for bad in ("def apply", "def promote", "auto_apply", "save_config",
                    "promote_template"):
            assert bad not in _SRC


class TestDownloadExplainer:
    def test_higher_rendition_scores_higher(self):
        ex = ct.download_decision_explainer()
        # the 4K sample must outscore the 720p sample (live-state rendition pref)
        by_label = {c["label"]: c["score"] for c in ex["candidates"]}
        assert by_label.get("Download 4K", 0) > by_label.get("Download 720p", 0)

    def test_chosen_is_top_ranked(self):
        ex = ct.download_decision_explainer()
        assert ex["chosen"] is not None
        assert ex["chosen"]["score"] == max(c["score"] for c in ex["candidates"])

    def test_every_candidate_has_reasons_and_tier(self):
        ex = ct.download_decision_explainer()
        for c in ex["candidates"]:
            assert "score" in c and "resolution_tier" in c and "reasons" in c

    def test_sample_flag_when_no_candidates(self):
        assert ct.download_decision_explainer()["is_sample"] is True
        assert ct.download_decision_explainer(candidates=[{"text": "x", "href": "https://e.com/a.mp4"}])["is_sample"] is False

    def test_uses_pure_project_scorer(self):
        # narration must come from the project's scorer, not a reimplementation
        assert "score_candidate" in _SRC and "rank_candidates" in _SRC


class TestVideoHealth:
    def test_reads_config_read_only_and_reports_shape(self):
        vh = ct.video_template_health()
        assert "sites" in vh and "site_count" in vh and "config_present" in vh
        assert vh["site_count"] == len(vh["sites"])

    def test_template_health_fields_present(self):
        # run against the shipped example config so rows exist to shape-check
        import os
        os.environ["BD_SITES_CONFIG_PATH"] = "sites_config.example.json"
        try:
            vh = ct.video_template_health()
        finally:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)
        for s in vh["sites"]:
            for k in ("site", "template_present", "row_selector_count",
                      "two_step_flow", "selector_confidence", "drift"):
                assert k in s

    def test_rendition_signal_is_honest(self):
        # rendition comes from the corpus; signal must be labelled, never invented
        vh = ct.video_template_health()
        for s in vh["sites"]:
            assert s["rendition_signal"] in ("corpus", "none_yet")


class TestWiring:
    def test_endpoints_and_pages_present(self):
        console = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")
        assert '@bp.get("/api/template/video-health")' in console
        assert '@bp.get("/api/template/download-explain")' in console
        assert "PAGES.videotemplates" in console and "PAGES.downloadexplain" in console
        assert ('data-p="videotemplates"' in console or "videotemplates:[" in console) and 'data-p="downloadexplain"' in console

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/video-health").status_code == 200
        assert c.get("/cockpit/api/template/download-explain").status_code == 200

    def test_template_intel_added_no_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        assert "/cockpit/api/template/video-health" not in posts
        assert "/cockpit/api/template/download-explain" not in posts
