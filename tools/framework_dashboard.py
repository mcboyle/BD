#!/usr/bin/env python3
"""framework_dashboard.py — read-only in-GUI view of the framework reports.

A Flask blueprint that renders the recognition-only framework's generated reports
(operator cockpit, executive summary, health, risk, audit, calibration, etc.) in a
sleek single-page dashboard. It is READ-ONLY: it reads the JSON/Markdown artifacts the
analysis tools produce and renders them. It issues no commands, drives no browser,
triggers no capture, runs no analysis, and writes nothing. No control surface.

Integration (into the existing Flask app):
    from tools.framework_dashboard import bp as framework_bp
    app.register_blueprint(framework_bp)
    # set BD_FRAMEWORK_REPORTS to the dir holding the generated reports (default below).
    # then browse to /framework
After registering, regenerate ENDPOINT_CATALOG.md (it adds GET routes under /framework).

Standalone (local viewing without the main app):
    BD_FRAMEWORK_REPORTS=./reports python3 tools/framework_dashboard.py  # serves :8770

Scope note: this dashboard intentionally has NO capture-trigger, NO remote-browser
control, and NO command dispatch. Those are a separate, explicitly-scoped concern for
the operator's own authorized sessions; they are not part of this read-only view.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, render_template_string, abort, jsonify

try:
    import markdown as _md
except Exception:  # pragma: no cover
    _md = None

bp = Blueprint("framework", __name__, url_prefix="/framework")


def _reports_root() -> Path:
    return Path(os.environ.get("BD_FRAMEWORK_REPORTS", "./framework_reports")).resolve()


def _safe_under_root(name: str) -> Optional[Path]:
    """Resolve a report name strictly under the reports root (no path traversal)."""
    root = _reports_root()
    p = (root / name).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        return None
    return p if p.is_file() else None


def _load_cockpit() -> Optional[Dict[str, Any]]:
    root = _reports_root()
    for cand in ("operator_cockpit.json", "cockpit/operator_cockpit.json"):
        p = root / cand
        if p.is_file():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                return None
    return None


def _list_reports() -> List[Dict[str, str]]:
    root = _reports_root()
    out = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix in (".md", ".json"):
                out.append({"name": str(p.relative_to(root)), "kind": p.suffix[1:]})
    return out


# ── sleek dark UI (self-contained; no external assets) ──────────────
_PAGE = """
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Framework — Operator Cockpit</title>
<style>
  :root{
    --bg:#0b0e14; --panel:#11161f; --panel2:#161d29; --line:#222c3a;
    --ink:#e6edf3; --dim:#8b9bb0; --accent:#4ea1ff; --good:#3fb950;
    --warn:#d29922; --bad:#f85149; --chip:#1d2733;
  }
  *{box-sizing:border-box} html,body{margin:0;height:100%}
  body{background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Inter,sans-serif}
  a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
  .wrap{display:grid;grid-template-columns:280px 1fr;height:100vh}
  .side{background:var(--panel);border-right:1px solid var(--line);padding:18px 14px;overflow:auto}
  .brand{font-weight:700;letter-spacing:.3px;font-size:15px;margin:2px 6px 14px}
  .brand small{display:block;color:var(--dim);font-weight:500;font-size:11px;margin-top:2px}
  .navlbl{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.6px;margin:16px 6px 6px}
  .navitem{display:block;padding:7px 10px;border-radius:8px;color:var(--ink);font-size:13px;
           white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .navitem:hover{background:var(--panel2);text-decoration:none}
  .navitem .tag{float:right;color:var(--dim);font-size:10px;border:1px solid var(--line);
                border-radius:4px;padding:0 5px;margin-left:6px}
  .main{padding:26px 30px;overflow:auto}
  h1{font-size:20px;margin:0 0 4px} .sub{color:var(--dim);margin:0 0 22px;font-size:13px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px;margin-bottom:24px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
  .card .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
  .card .v{font-size:24px;font-weight:700;margin-top:6px}
  .card .v.good{color:var(--good)} .card .v.warn{color:var(--warn)} .card .v.bad{color:var(--bad)}
  .card .note{color:var(--dim);font-size:12px;margin-top:6px}
  .section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 20px;margin-bottom:18px}
  .section h2{font-size:14px;margin:0 0 12px;color:var(--ink)}
  .chips .chip{display:inline-block;background:var(--chip);border:1px solid var(--line);
               border-radius:999px;padding:4px 11px;margin:0 6px 6px 0;font-size:12px}
  .chip.bad{border-color:#5a2a2a;color:#ffb4ad} .chip.warn{border-color:#5a4a1f;color:#f0d28a}
  .risk{display:flex;gap:10px;align-items:flex-start;padding:8px 0;border-bottom:1px solid var(--line)}
  .risk:last-child{border-bottom:none}
  .sev{font-size:10px;font-weight:700;border-radius:5px;padding:2px 7px;white-space:nowrap}
  .sev.Critical{background:#4a1414;color:#ffb4ad} .sev.High{background:#4a2a14;color:#f0c08a}
  .sev.Medium{background:#3a3414;color:#e8da8a} .sev.Low{background:#1d2733;color:#9fb2c8}
  .empty{color:var(--dim);font-style:italic}
  .report{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:8px 26px 26px}
  .report .md{font-size:14px} .report .md h1{font-size:20px;margin-top:18px}
  .report .md h2{font-size:16px;border-bottom:1px solid var(--line);padding-bottom:5px;margin-top:22px}
  .report .md code{background:#0d1117;border:1px solid var(--line);border-radius:4px;padding:1px 5px;font-size:12px}
  .report .md pre{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:14px;overflow:auto}
  .report .md ul{padding-left:20px} .report .md li{margin:3px 0}
  .pillbar{display:flex;gap:8px;margin-bottom:16px;flex-wrap:wrap}
  .pill{background:var(--chip);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:12px;color:var(--dim)}
  .pill b{color:var(--ink)}
  .ro{position:fixed;right:16px;bottom:14px;background:var(--panel2);border:1px solid var(--line);
      border-radius:8px;padding:6px 12px;color:var(--dim);font-size:11px}
</style></head><body>
<div class="wrap">
  <aside class="side">
    <div class="brand">Framework Cockpit<small>recognition-only · read-only</small></div>
    <a class="navitem" href="{{ url_for('framework.index') }}">▸ Cockpit overview</a>
    <div class="navlbl">Reports</div>
    {% for r in reports %}
      <a class="navitem" href="{{ url_for('framework.report', name=r.name) }}">
        {{ r.name }}<span class="tag">{{ r.kind }}</span></a>
    {% else %}
      <div class="navitem empty">no reports found</div>
    {% endfor %}
  </aside>
  <main class="main">{{ body|safe }}</main>
</div>
<div class="ro">read-only · no control surface</div>
</body></html>
"""

_OVERVIEW = """
<h1>Operator cockpit — what matters right now</h1>
<p class="sub">Reports root: <code>{{ root }}</code></p>
{% if not cockpit %}
  <div class="section"><span class="empty">No operator_cockpit.json found under the reports
  root. Generate it with <code>tools/operator_layer.py cockpit ...</code> and set
  <code>BD_FRAMEWORK_REPORTS</code> to its output directory.</span></div>
{% else %}
  <div class="grid">
    <div class="card"><div class="k">Framework maturity</div>
      <div class="v {{ mat_cls }}">{{ cockpit.framework_maturity or '—' }}</div>
      <div class="note">overall {{ cockpit.framework_overall_health if cockpit.framework_overall_health is not none else '—' }}</div></div>
    <div class="card"><div class="k">Debt (corr/cap/val)</div>
      <div class="v {{ 'warn' if (cockpit.debt_status and cockpit.debt_status.validation) else 'good' }}">
        {{ cockpit.debt_status.correction if cockpit.debt_status else '?' }}/{{ cockpit.debt_status.capability if cockpit.debt_status else '?' }}/{{ cockpit.debt_status.validation if cockpit.debt_status else '?' }}</div>
      <div class="note">{{ (cockpit.capture_priorities.validation_debt_items if cockpit.capture_priorities else []) | join(', ') }}</div></div>
    <div class="card"><div class="k">Review workload</div>
      <div class="v">{{ cockpit.review_workload.review if cockpit.review_workload else 0 }}</div>
      <div class="note">+{{ cockpit.review_workload.approvals if cockpit.review_workload else 0 }} approvals · {{ cockpit.capture_priorities.requested if cockpit.capture_priorities else 0 }} captures requested</div></div>
    <div class="card"><div class="k">Unsupported conclusions</div>
      <div class="v {{ 'bad' if cockpit.audit_unsupported else 'good' }}">{{ cockpit.audit_unsupported or 0 }}</div>
      <div class="note">from audit readiness</div></div>
  </div>

  <div class="section"><h2>Active high / critical risks</h2>
    {% if cockpit.active_high_risks %}
      {% for r in cockpit.active_high_risks %}<div class="risk"><span class="sev High">RISK</span><div>{{ r }}</div></div>{% endfor %}
    {% else %}<span class="empty">none</span>{% endif %}
  </div>

  <div class="section"><h2>Fragile / at-risk sites</h2>
    <div class="chips">
      {% for s in cockpit.fragile_sites %}<span class="chip bad">{{ s }}</span>{% else %}<span class="empty">none</span>{% endfor %}
    </div></div>

  <div class="section"><h2>Stale evidence</h2>
    <div class="chips">
      {% for s in (cockpit.evidence_freshness.stale_sites if cockpit.evidence_freshness else []) %}<span class="chip warn">{{ s }}</span>{% else %}<span class="empty">all current</span>{% endfor %}
    </div></div>
{% endif %}
"""

_REPORT = """
<div class="pillbar">
  <span class="pill">report · <b>{{ name }}</b></span>
  <span class="pill">{{ kind }}</span>
  <a class="pill" href="{{ url_for('framework.index') }}">← back to cockpit</a>
</div>
<div class="report"><div class="md">{{ rendered|safe }}</div></div>
"""


def _maturity_class(m: Optional[str]) -> str:
    return {"Highly Mature": "good", "Mature": "good", "Operational": "warn",
            "Emerging": "warn", "Experimental": "bad"}.get(m or "", "")


@bp.route("/")
def index():
    cockpit = _load_cockpit()
    body = render_template_string(
        _OVERVIEW, cockpit=cockpit, root=str(_reports_root()),
        mat_cls=_maturity_class(cockpit.get("framework_maturity") if cockpit else None))
    return render_template_string(_PAGE, body=body, reports=_list_reports())


@bp.route("/report/<path:name>")
def report(name):
    p = _safe_under_root(name)
    if not p:
        abort(404)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".json":
        try:
            pretty = json.dumps(json.loads(text), indent=2, sort_keys=True)
        except ValueError:
            pretty = text
        rendered = "<pre>" + (pretty.replace("&", "&amp;").replace("<", "&lt;")) + "</pre>"
    else:
        if _md:
            rendered = _md.markdown(text, extensions=["fenced_code", "tables"])
        else:
            rendered = "<pre>" + text.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
    body = render_template_string(_REPORT, name=name, kind=p.suffix[1:], rendered=rendered)
    return render_template_string(_PAGE, body=body, reports=_list_reports())


@bp.route("/api/cockpit.json")
def api_cockpit():
    """Read-only JSON passthrough of the cockpit (for any custom frontend)."""
    c = _load_cockpit()
    return (jsonify(c), 200) if c else (jsonify({"error": "no cockpit report"}), 404)


def register_routes(app):
    """Register this read-only report dashboard blueprint on `app`. Idempotent;
    returns the number of /framework routes. Matches the app_*_api convention."""
    import sys
    try:
        app.register_blueprint(bp)
    except (ValueError, AssertionError) as e:  # already registered (hot-reload)
        sys.stderr.write(f"[framework-dashboard] blueprint already registered: {e}\n")
        return 0
    n = sum(1 for r in app.url_map.iter_rules() if r.rule.startswith("/framework"))
    sys.stderr.write(f"[framework-dashboard] registered {n} report-dashboard routes\n")
    return n


def _standalone():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(bp)
    port = int(os.environ.get("BD_FRAMEWORK_PORT", "8770"))
    print(f"Framework dashboard (read-only) on http://127.0.0.1:{port}/framework")
    print(f"Reports root: {_reports_root()}  (set BD_FRAMEWORK_REPORTS to change)")
    app.run(host="127.0.0.1", port=port, debug=False)


if __name__ == "__main__":
    _standalone()
