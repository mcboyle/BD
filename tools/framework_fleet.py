#!/usr/bin/env python3
"""framework_fleet.py — read-only fleet view across multiple BD servers (your own).

Aggregates the read-only cockpit status from several BulkDownloader nodes on your own
network into one overview. It MONITORS: it issues HTTP GETs to each node's read-only
`/framework/api/cockpit.json` and rolls the results up. It does NOT command nodes, does
NOT dispatch tasks, does NOT control anything — it reads status from machines you own.

This is fleet *monitoring*, not command-and-control. The distinction is deliberate:
reading aggregate status from your own servers is ordinary multi-host admin; issuing
commands to control hosts is not something this builds.

Node list comes from `BD_FLEET_NODES` — a path to a JSON file:
    [
      {"name": "bd-stash", "url": "http://stash.lan:5555", "token": "optional-bearer"},
      {"name": "bd-02",    "url": "http://10.0.0.12:5555"}
    ]
(Each node should keep its /framework routes behind the app's existing auth; the token,
if given, is sent as `Authorization: Bearer <token>`.)

Integrate:
    from tools.framework_fleet import bp as fleet_bp
    app.register_blueprint(fleet_bp)            # adds read-only GET routes under /fleet
Standalone:
    BD_FLEET_NODES=./nodes.json python3 tools/framework_fleet.py   # serves 127.0.0.1:8771
"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Callable

from flask import Blueprint, render_template_string, jsonify

bp = Blueprint("fleet", __name__, url_prefix="/fleet")

_MATURITY_RANK = {"Broken": 0, "Experimental": 1, "Emerging": 2, "Fragile": 2,
                  "Operational": 3, "Watch": 3, "Stable": 4, "Mature": 5,
                  "Highly Mature": 6}


def _nodes() -> List[Dict[str, Any]]:
    path = None
    try:
        from bulk_downloader import global_config as _gc
        _s = _gc.get("fleet_nodes", None)
        if _s not in (None, ""):
            path = str(_s)
    except Exception:
        pass
    if path is None:
        path = os.environ.get("BD_FLEET_NODES")
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def _http_fetch_node(node: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
    """GET {node.url}/framework/api/cockpit.json (read-only). Never raises."""
    name = node.get("name") or node.get("url", "?")
    base = str(node.get("url", "")).rstrip("/")
    url = f"{base}/framework/api/cockpit.json"
    req = urllib.request.Request(url, method="GET")
    if node.get("token"):
        req.add_header("Authorization", f"Bearer {node['token']}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec - operator's own LAN
            body = r.read().decode("utf-8")
        cockpit = json.loads(body)
        return {"name": name, "url": base, "ok": True, "cockpit": cockpit}
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as e:
        return {"name": name, "url": base, "ok": False, "error": str(e)[:120]}


# injectable so the aggregation/render can be tested offline
_FETCHER: Callable[[Dict[str, Any]], Dict[str, Any]] = _http_fetch_node


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    reachable = [r for r in results if r.get("ok")]
    worst_rank = None
    worst_maturity = None
    total_val_debt = 0
    total_review = 0
    fragile: List[Dict[str, str]] = []
    risks: List[Dict[str, str]] = []
    for r in reachable:
        c = r.get("cockpit") or {}
        m = c.get("framework_maturity")
        if m is not None:
            rank = _MATURITY_RANK.get(m, 99)
            if worst_rank is None or rank < worst_rank:
                worst_rank, worst_maturity = rank, m
        debt = c.get("debt_status") or {}
        total_val_debt += debt.get("validation", 0) or 0
        rw = c.get("review_workload") or {}
        total_review += (rw.get("review", 0) or 0) + (rw.get("approvals", 0) or 0)
        for s in (c.get("fragile_sites") or []):
            fragile.append({"node": r["name"], "site": s})
        for risk in (c.get("active_high_risks") or []):
            risks.append({"node": r["name"], "risk": risk})
    return {
        "nodes_total": len(results),
        "nodes_reachable": len(reachable),
        "nodes_offline": len(results) - len(reachable),
        "worst_maturity": worst_maturity,
        "total_validation_debt": total_val_debt,
        "total_review_workload": total_review,
        "fragile_sites": fragile,
        "active_high_risks": risks,
    }


_PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BD Fleet — overview</title>
<style>
  :root{--bg:#0b0e14;--panel:#11161f;--panel2:#161d29;--line:#222c3a;--ink:#e6edf3;
        --dim:#8b9bb0;--accent:#4ea1ff;--good:#3fb950;--warn:#d29922;--bad:#f85149;--chip:#1d2733}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);
    font:14px/1.5 -apple-system,Segoe UI,Roboto,Inter,sans-serif}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  .top{padding:22px 30px;border-bottom:1px solid var(--line);display:flex;align-items:baseline;gap:14px}
  .top h1{font-size:19px;margin:0} .top .sub{color:var(--dim);font-size:13px}
  .main{padding:24px 30px}
  .rollup{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:24px}
  .stat{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .stat .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  .stat .v{font-size:22px;font-weight:700;margin-top:6px}
  .v.good{color:var(--good)} .v.warn{color:var(--warn)} .v.bad{color:var(--bad)}
  h2{font-size:14px;margin:0 0 12px}
  .nodes{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-bottom:26px}
  .node{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
  .node .hd{display:flex;align-items:center;gap:9px;margin-bottom:10px}
  .dot{width:9px;height:9px;border-radius:50%} .dot.on{background:var(--good)} .dot.off{background:var(--bad)}
  .node .nm{font-weight:700} .node .url{color:var(--dim);font-size:11px;margin-left:auto}
  .row{display:flex;justify-content:space-between;padding:3px 0;font-size:13px;border-bottom:1px solid var(--line)}
  .row:last-child{border-bottom:none} .row .lbl{color:var(--dim)}
  .badge{font-size:11px;border-radius:5px;padding:1px 7px}
  .badge.good{background:#16351c;color:#7ee787} .badge.warn{background:#3a3414;color:#e8da8a}
  .badge.bad{background:#4a1414;color:#ffb4ad} .badge.dim{background:var(--chip);color:var(--dim)}
  .offline{color:var(--bad);font-size:12px;font-style:italic}
  .section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 20px;margin-bottom:18px}
  .chips .chip{display:inline-block;background:var(--chip);border:1px solid var(--line);border-radius:999px;
               padding:4px 11px;margin:0 6px 6px 0;font-size:12px}
  .chip.bad{border-color:#5a2a2a;color:#ffb4ad}
  .chip .n{color:var(--dim)}
  .empty{color:var(--dim);font-style:italic}
  .ro{position:fixed;right:16px;bottom:14px;background:var(--panel2);border:1px solid var(--line);
      border-radius:8px;padding:6px 12px;color:var(--dim);font-size:11px}
</style></head><body>
<div class="top"><h1>BulkDownloader Fleet</h1>
  <span class="sub">{{ agg.nodes_reachable }}/{{ agg.nodes_total }} nodes reachable · read-only monitoring</span></div>
<div class="main">
  {% if agg.nodes_total == 0 %}
    <div class="section"><span class="empty">No fleet nodes configured. Point
    <code>BD_FLEET_NODES</code> at a JSON file listing your BD servers
    (name + url [+ token]).</span></div>
  {% else %}
  <div class="rollup">
    <div class="stat"><div class="k">Nodes online</div>
      <div class="v {{ 'good' if agg.nodes_offline==0 else 'warn' }}">{{ agg.nodes_reachable }}/{{ agg.nodes_total }}</div></div>
    <div class="stat"><div class="k">Worst maturity</div>
      <div class="v {{ 'bad' if agg.worst_maturity in ['Broken','Experimental'] else 'warn' if agg.worst_maturity in ['Emerging','Fragile'] else 'good' }}">{{ agg.worst_maturity or '—' }}</div></div>
    <div class="stat"><div class="k">Validation debt (fleet)</div>
      <div class="v {{ 'warn' if agg.total_validation_debt else 'good' }}">{{ agg.total_validation_debt }}</div></div>
    <div class="stat"><div class="k">Review workload (fleet)</div>
      <div class="v">{{ agg.total_review_workload }}</div></div>
    <div class="stat"><div class="k">High/critical risks</div>
      <div class="v {{ 'bad' if agg.active_high_risks else 'good' }}">{{ agg.active_high_risks|length }}</div></div>
  </div>

  <h2>Nodes</h2>
  <div class="nodes">
    {% for n in nodes %}
      <div class="node">
        <div class="hd"><span class="dot {{ 'on' if n.ok else 'off' }}"></span>
          <span class="nm">{{ n.name }}</span><span class="url">{{ n.url }}</span></div>
        {% if n.ok %}
          {% set c = n.cockpit %}
          <div class="row"><span class="lbl">maturity</span>
            <span class="badge {{ 'good' if c.framework_maturity in ['Mature','Highly Mature'] else 'warn' }}">{{ c.framework_maturity or '—' }}</span></div>
          <div class="row"><span class="lbl">debt (c/c/v)</span>
            <span class="badge {{ 'warn' if (c.debt_status and c.debt_status.validation) else 'good' }}">{{ c.debt_status.correction if c.debt_status else '?' }}/{{ c.debt_status.capability if c.debt_status else '?' }}/{{ c.debt_status.validation if c.debt_status else '?' }}</span></div>
          <div class="row"><span class="lbl">fragile sites</span><span>{{ (c.fragile_sites or [])|length }}</span></div>
          <div class="row"><span class="lbl">high risks</span><span>{{ (c.active_high_risks or [])|length }}</span></div>
          <div class="row"><span class="lbl"><a href="{{ n.url }}/framework">open node dashboard ↗</a></span><span></span></div>
        {% else %}
          <div class="offline">offline — {{ n.error }}</div>
        {% endif %}
      </div>
    {% endfor %}
  </div>

  <div class="section"><h2>Fragile sites across the fleet</h2>
    <div class="chips">
      {% for f in agg.fragile_sites %}<span class="chip bad">{{ f.site }} <span class="n">@ {{ f.node }}</span></span>
      {% else %}<span class="empty">none</span>{% endfor %}</div></div>

  <div class="section"><h2>Active high / critical risks across the fleet</h2>
    {% for r in agg.active_high_risks %}<div class="row"><span>{{ r.risk }}</span><span class="lbl">@ {{ r.node }}</span></div>
    {% else %}<span class="empty">none</span>{% endfor %}</div>
  {% endif %}
</div>
<div class="ro">read-only fleet monitoring · no command surface</div>
</body></html>
"""


@bp.route("/")
def index():
    results = [_FETCHER(n) for n in _nodes()]
    agg = _aggregate(results)
    return render_template_string(_PAGE, nodes=results, agg=agg)


@bp.route("/api/summary.json")
def api_summary():
    results = [_FETCHER(n) for n in _nodes()]
    return jsonify(_aggregate(results))


def register_routes(app):
    """Register this read-only fleet view blueprint on `app`. Idempotent;
    returns the number of /fleet routes. Matches the app_*_api convention."""
    import sys
    try:
        app.register_blueprint(bp)
    except (ValueError, AssertionError) as e:  # already registered (hot-reload)
        sys.stderr.write(f"[framework-fleet] blueprint already registered: {e}\n")
        return 0
    n = sum(1 for r in app.url_map.iter_rules() if r.rule.startswith("/fleet"))
    sys.stderr.write(f"[framework-fleet] registered {n} fleet routes\n")
    return n


def _standalone():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    port = int(os.environ.get("BD_FLEET_PORT", "8771"))
    print(f"BD fleet view (read-only) on http://127.0.0.1:{port}/fleet")
    print(f"Nodes from: {os.environ.get('BD_FLEET_NODES', '(BD_FLEET_NODES unset)')}")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    _standalone()
