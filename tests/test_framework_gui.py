"""Framework GUI tests (v3.66.92) — read-only dashboard + fleet blueprints.

Verifies the in-GUI report viewer and the multi-server fleet view register, serve,
render, and stay read-only/safe (path-traversal guarded; no command surface). Builds
its own fixtures so it has no dependency on any out-of-tree artifacts.
"""
import json
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

BD_GATE_SCOPE = "repo-wide"


def _set_reports_root(monkeypatch, path):
    monkeypatch.setenv("BD_FRAMEWORK_REPORTS", str(path))


def _minimal_cockpit():
    return {
        "framework_maturity": "Mature",
        "framework_overall_health": 0.78,
        "debt_status": {"correction": 0, "capability": 0, "validation": 2},
        "review_workload": {"review": 5, "approvals": 1},
        "capture_priorities": {"requested": 2, "validation_debt_items": ["VC-0017", "VC-0018"]},
        "active_high_risks": ["sites_trending_broken"],
        "fragile_sites": ["fragilesite"],
        "evidence_freshness": {"stale_sites": ["fragilesite"], "n_stale": 1},
        "audit_unsupported": 1,
    }


def _dashboard_client(tmp_path, monkeypatch):
    with open(tmp_path / "operator_cockpit.json", "w", encoding="utf-8") as f:
        json.dump(_minimal_cockpit(), f)
    report_text = "# Health\n\n- maturity: Mature\n\n<unsafe&tag>\n"
    with open(tmp_path / "site_health_report.md", "w", encoding="utf-8") as f:
        f.write(report_text)
    _set_reports_root(monkeypatch, tmp_path)

    import tools.framework_dashboard as dash
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(dash.bp)
    return app.test_client(), dash, report_text


def _force_markdown(monkeypatch, dash, renderer):
    monkeypatch.setattr(dash, "_md", renderer)


def test_dashboard_serves_cockpit_and_markdown_rendered_report(tmp_path, monkeypatch):
    c, dash, report_text = _dashboard_client(tmp_path, monkeypatch)

    r = c.get("/framework/")
    assert r.status_code == 200
    assert b"Framework maturity" in r.data and b"Mature" in r.data
    assert b"sites_trending_broken" in r.data
    assert b"fragilesite" in r.data

    class _HermeticMarkdown:
        def __init__(self):
            self.calls = []

        def markdown(self, text, extensions):
            self.calls.append((text, extensions))
            return "<h1>Hermetic rendered heading</h1>"

    renderer = _HermeticMarkdown()
    _force_markdown(monkeypatch, dash, renderer)
    rendered = c.get("/framework/report/site_health_report.md")
    assert rendered.status_code == 200
    assert b"<h1>Hermetic rendered heading</h1>" in rendered.data
    assert b"<pre># Health" not in rendered.data
    assert renderer.calls == [(report_text, ["fenced_code", "tables"])]

    r3 = c.get("/framework/api/cockpit.json")
    assert r3.status_code == 200 and r3.is_json


def test_dashboard_serves_pre_fallback_without_markdown(tmp_path, monkeypatch):
    c, dash, _report_text = _dashboard_client(tmp_path, monkeypatch)
    _force_markdown(monkeypatch, dash, None)

    fallback = c.get("/framework/report/site_health_report.md")

    assert fallback.status_code == 200
    assert b"<pre># Health" in fallback.data
    assert b"&lt;unsafe&amp;tag>" in fallback.data
    assert b"<unsafe&tag>" not in fallback.data
    assert b"Hermetic rendered heading" not in fallback.data


def test_dashboard_optional_renderer_seams_restore_state_in_either_order(
        tmp_path, monkeypatch):
    import tools.framework_dashboard as dash
    original_md = dash._md
    original_env_present = "BD_FRAMEWORK_REPORTS" in os.environ
    original_env = os.environ.get("BD_FRAMEWORK_REPORTS")

    def assert_restored():
        assert dash._md is original_md
        assert ("BD_FRAMEWORK_REPORTS" in os.environ) is original_env_present
        assert os.environ.get("BD_FRAMEWORK_REPORTS") == original_env

    class Renderer:
        def markdown(self, _text, extensions):
            assert extensions == ["fenced_code", "tables"]
            return "<h1>ORDER-INDEPENDENT</h1>"

    def rendered(path):
        path.mkdir()
        with monkeypatch.context() as scoped:
            client, current_dash, _text = _dashboard_client(path, scoped)
            _force_markdown(scoped, current_dash, Renderer())
            response = client.get("/framework/report/site_health_report.md")
            assert response.status_code == 200
            assert b"<h1>ORDER-INDEPENDENT</h1>" in response.data
        assert_restored()

    def fallback(path):
        path.mkdir()
        with monkeypatch.context() as scoped:
            client, current_dash, _text = _dashboard_client(path, scoped)
            _force_markdown(scoped, current_dash, None)
            response = client.get("/framework/report/site_health_report.md")
            assert response.status_code == 200
            assert b"<pre># Health" in response.data
        assert_restored()

    fallback(tmp_path / "fallback-one")
    rendered(tmp_path / "rendered-one")
    rendered(tmp_path / "rendered-two")
    fallback(tmp_path / "fallback-two")


def test_dashboard_blocks_path_traversal(tmp_path, monkeypatch):
    _set_reports_root(monkeypatch, tmp_path)
    import tools.framework_dashboard as dash
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(dash.bp)
    c = app.test_client()
    r = c.get("/framework/report/..%2f..%2f..%2fetc%2fpasswd")
    assert r.status_code == 404


def test_fleet_aggregates_and_handles_offline_nodes():
    import importlib
    import tools.framework_fleet as fleet
    importlib.reload(fleet)

    ex = _minimal_cockpit()
    ex2 = dict(ex)
    ex2["framework_maturity"] = "Fragile"
    ex2["debt_status"] = {"correction": 0, "capability": 0, "validation": 1}
    ex2["fragile_sites"] = ["siteX"]
    nodes = [{"name": "bd-a", "url": "http://a"}, {"name": "bd-b", "url": "http://b"},
             {"name": "bd-c", "url": "http://c"}]

    def fake(n):
        if n["name"] == "bd-a":
            return {"name": "bd-a", "url": "http://a", "ok": True, "cockpit": ex}
        if n["name"] == "bd-b":
            return {"name": "bd-b", "url": "http://b", "ok": True, "cockpit": ex2}
        return {"name": "bd-c", "url": "http://c", "ok": False, "error": "refused"}

    fleet._FETCHER = fake
    fleet._nodes = lambda: nodes
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(fleet.bp)
    c = app.test_client()

    r = c.get("/fleet/")
    assert r.status_code == 200
    assert b"2/3 nodes reachable" in r.data
    assert b"refused" in r.data            # offline node surfaced, not crashing

    s = c.get("/fleet/api/summary.json").get_json()
    assert s["nodes_reachable"] == 2 and s["nodes_offline"] == 1
    assert s["worst_maturity"] == "Fragile"        # worst, not best
    assert s["total_validation_debt"] == 3         # 2 + 1


def test_fleet_has_no_command_surface():
    # the fleet blueprint must expose only read-only GET routes (no POST/control).
    import importlib
    import tools.framework_fleet as fleet
    importlib.reload(fleet)
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(fleet.bp)
    methods = set()
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("fleet."):
            methods |= (rule.methods or set())
    # only GET (plus HEAD/OPTIONS that Flask adds automatically); never POST/PUT/DELETE.
    assert "POST" not in methods and "PUT" not in methods and "DELETE" not in methods
