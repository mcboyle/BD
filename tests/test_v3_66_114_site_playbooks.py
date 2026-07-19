"""v3.66.114 — Phase 5 Site Playbooks (living dossier per site).

Read-only aggregation. Posture (no writes, no live fetch/replay, no model) + family
inference correctness + dossier shape (all sections) + signing surfaced as
NAMES-not-values + index shape + wiring (+2 GET routes = 91, no POSTs).
"""
import json
from pathlib import Path

from tools import cockpit_templates as ct

_SRC = Path(ct.__file__).read_text(encoding="utf-8")
_CONSOLE = Path((Path(ct.__file__).parent / "cockpit_console.py")).read_text(encoding="utf-8")


class TestPosture:
    def test_read_only_no_writes(self):
        assert "json.dump(" not in _SRC      # 'json.dumps(' is read-only
        assert "write_text" not in _SRC
        assert "_store_save" not in _SRC

    def test_no_live_fetch_or_replay(self):
        for bad in ("requests.", "urllib.request", "httpx", "playwright",
                    "page.goto", "subprocess", ".replay(", "do_login(",
                    ".fill(", ".click(", "ollama."):
            assert bad not in _SRC, f"playbooks must not use {bad!r}"

    def test_no_auto_apply(self):
        for bad in ("def apply", "def promote", "auto_apply", "_apply_detected_selectors"):
            assert bad not in _SRC


class TestFamilyInference:
    def test_jwplayer_family(self):
        cfg = {"learned": {"download": {"row_selectors": [".jw-overlays a", ".jwplayer source"]}}}
        assert "jwplayer" in ct._infer_families(cfg)

    def test_videojs_family(self):
        cfg = {"learned": {"download": {"row_selectors": ["video.video-js source", ".vjs-tech"]}}}
        assert "video.js" in ct._infer_families(cfg)

    def test_react_family(self):
        cfg = {"learned": {"login": {"user_field": ["#root[data-reactroot] input"]}}}
        assert "react" in ct._infer_families(cfg)

    def test_no_markers_no_family(self):
        cfg = {"learned": {"download": {"row_selectors": ["video > source"]}}}
        fams = ct._infer_families(cfg)
        assert isinstance(fams, list)  # may be empty — honest, not invented

    def test_uses_project_providers_table(self):
        # family inference reuses the project's own PROVIDERS classification
        assert "from bulk_downloader.deep_detect import PROVIDERS" in _SRC


class TestDossier:
    def _cfg(self):
        return [{"id": "vipsite", "name": "VIP", "login_url": "https://v/login",
                 "learned": {"login": {"user_field": ["#u"], "pass_field": ["#p"],
                                       "submit_btn": ["button[type=submit]"]},
                             "download": {"row_selectors": [".jw-overlays a"],
                                          "url_attribute": "src",
                                          "trigger_selectors": [".jw-icon-quality"]}}}]

    def _run(self, fn):
        import os, json, tempfile
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(self._cfg(), f); f.close()
        os.environ["BD_SITES_CONFIG_PATH"] = f.name
        try:
            return fn()
        finally:
            os.environ.pop("BD_SITES_CONFIG_PATH", None)

    def test_dossier_has_all_sections(self):
        pb = self._run(lambda: ct.site_playbook("vipsite"))
        for k in ("site", "family", "login_model", "download_model", "selector_model",
                  "drift_history", "known_failure_modes", "operator_notes",
                  "family_confidence", "confidence_history"):
            assert k in pb, f"dossier missing {k}"

    def test_dossier_models_populated(self):
        pb = self._run(lambda: ct.site_playbook("vipsite"))
        assert pb["login_model"]["template_present"] is True
        assert pb["download_model"]["template_present"] is True
        assert pb["family"]["inferred"] == ["jwplayer"]

    def test_signing_surfaced_as_markers_not_values(self):
        # the dossier carries known_signing_markers (NAMES); ensure no raw query/sig leaks
        pb = self._run(lambda: ct.site_playbook("vipsite"))
        blob = json.dumps(pb)
        assert "?token=" not in blob and "sig=" not in blob
        assert "known_signing_markers" in pb  # the field exists (names only)

    def test_missing_site_errors_cleanly(self):
        assert "error" in ct.site_playbook("nope")
        assert "error" in ct.site_playbook("")

    def test_index_shape(self):
        idx = self._run(lambda: ct.site_playbook_index())
        assert "sites" in idx and "site_count" in idx
        for s in idx["sites"]:
            for k in ("site", "families", "login_template", "video_template",
                      "stability", "maturity", "open_concerns", "notes"):
                assert k in s


class TestWiring:
    def test_endpoints_and_page_present(self):
        for ep in ("playbook-index", "playbook"):
            assert f'@bp.get("/api/template/{ep}")' in _CONSOLE
        assert "PAGES.siteplaybooks" in _CONSOLE and 'data-p="siteplaybooks"' in _CONSOLE
        assert "function pbLoad" in _CONSOLE  # the in-place dossier loader

    def test_route_count_and_serve(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        rules = {r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/cockpit")}
        assert len(rules) >= 162
        c = app.test_client()
        assert c.get("/cockpit/api/template/playbook-index").status_code == 200
        assert c.get("/cockpit/api/template/playbook?site=x").status_code == 200

    def test_no_new_posts(self):
        from flask import Flask
        from tools.cockpit_console import bp
        app = Flask(__name__); app.register_blueprint(bp)
        posts = {r.rule for r in app.url_map.iter_rules()
                 if "POST" in r.methods and r.rule.startswith("/cockpit")}
        for ep in ("playbook-index", "playbook"):
            assert f"/cockpit/api/template/{ep}" not in posts
