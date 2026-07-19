"""Framework GUI tests (v3.66.92) — read-only dashboard + fleet blueprints.

Verifies the in-GUI report viewer and the multi-server fleet view register, serve,
render, and stay read-only/safe (path-traversal guarded; no command surface). Builds
its own fixtures so it has no dependency on any out-of-tree artifacts.
"""
import json
import os
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)


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


def test_dashboard_serves_cockpit_and_reports():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "operator_cockpit.json"), "w", encoding="utf-8") as f:
        json.dump(_minimal_cockpit(), f)
    with open(os.path.join(d, "site_health_report.md"), "w", encoding="utf-8") as f:
        f.write("# Health\n\n- maturity: Mature\n")
    os.environ["BD_FRAMEWORK_REPORTS"] = d

    import importlib
    import tools.framework_dashboard as dash
    importlib.reload(dash)  # re-read env-derived root
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(dash.bp)
    c = app.test_client()

    r = c.get("/framework/")
    assert r.status_code == 200
    assert b"Framework maturity" in r.data and b"Mature" in r.data
    assert b"sites_trending_broken" in r.data
    assert b"fragilesite" in r.data

    r2 = c.get("/framework/report/site_health_report.md")
    assert r2.status_code == 200
    assert b"<h1" in r2.data or b"<h2" in r2.data  # markdown rendered

    r3 = c.get("/framework/api/cockpit.json")
    assert r3.status_code == 200 and r3.is_json


def test_dashboard_blocks_path_traversal():
    d = tempfile.mkdtemp()
    os.environ["BD_FRAMEWORK_REPORTS"] = d
    import importlib
    import tools.framework_dashboard as dash
    importlib.reload(dash)
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
