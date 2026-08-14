"""app_template_manager_ui.py — additive, READ-ONLY Template Manager view (#6/P3).

A new Flask blueprint that gives the operator one place to SEE the template tree:
reviewed templates, drafts, and review candidates with host, status, selector
groups, resolutions, network patterns, completeness score, blocked-term warnings,
promotion-readiness, and per-template drift vs the reviewed gold.

It is purely additive and purely observational:
  * It does NOT promote, enable, disable, swap, or modify any template. There are
    no action buttons and no mutating routes here — promotion/disable remain the
    existing operator-gated POST endpoints in app.py, untouched.
  * It composes existing pieces rather than re-implementing them:
      - bulk_downloader.template_manager.list_templates()  (reviewed+drafts, with
        the SAME pattern redaction the existing API uses)
      - tools.template_inventory.scan()                    (all four dirs + scoring,
        blocked terms, promotion-readiness — mirrors the promote gate)
      - tools.template_drift_report.diff_*()               (candidate/draft ⇄ gold)
  * Auth/CSRF piggyback on app.py's global before-request hooks (same as the
    other app_*.py blueprints), so no auth logic lives here.

Wiring (final-integration step, one line — deferred, operator-applied):
    from .app_template_manager_ui import register_routes as _reg_tmui
    _reg_tmui(app)

NEEDS OPERATOR CLICK-THROUGH VALIDATION: endpoints are unit/render tested
(200 + content), but the rendered page has not been exercised in a live browser.
"""
from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

template_manager_ui_bp = Blueprint("template_manager_ui", __name__)

# Repo root = parent of the bulk_downloader package. Used to (a) put `tools` on
# the import path and (b) give template_inventory a deterministic root that does
# not depend on CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _ensure_tools_on_path():
    p = str(_REPO_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)


def _inventory_data():
    """Compose the rich, read-only inventory. Returns the dict the JSON endpoint
    and the page both consume."""
    _ensure_tools_on_path()
    from tools import template_inventory as TI  # type: ignore

    scan = TI.scan(str(_REPO_ROOT))

    # Reuse the existing API's redacted network patterns for reviewed+drafts.
    redacted = {}
    try:
        from . import template_manager as TM
        lst = TM.list_templates()
        for bucket in ("reviewed", "drafts"):
            for e in lst.get(bucket, []) or []:
                if e.get("host"):
                    redacted[(bucket, e["host"])] = e.get("network_patterns")
    except Exception:  # noqa: BLE001 - redaction is a nicety, never fatal
        pass

    out = {"ok": True, "counts": scan["counts"], "sanity": scan["sanity"],
           "dirs": {}}
    for name, items in scan["dirs"].items():
        rows = []
        for a in items:
            host = a["host"]
            rows.append({
                "source": a["source"],
                "host": host,
                "status": a["status"],
                "enabled": a["status"] == "enabled",
                "selector_groups": a["selector_groups"],
                "resolutions": a["resolutions"],
                "resolutions_count": a["resolutions_count"],
                "network_patterns_redacted": redacted.get((name, host)),
                "network_patterns_count": a["network_patterns_count"],
                "completeness_score": a["completeness_score"],
                "blocked_terms": a["blocked_terms"],
                "promotion_ready": a["promotion_ready"],
                "download_trigger": a["download_trigger"],
                "row_selectors_count": a["row_selectors_count"],
                "api_base": a["api_base"],
                "missing": a["missing"],
                # Wave 168 review-only recognition (None on pre-168 drafts).
                "recognition": a.get("recognition"),
                # a drift report is meaningful only for a non-reviewed template
                # that has a reviewed gold for the same host
                "drift_available": name in ("drafts", "review_candidates"),
            })
        out["dirs"][name] = rows
    return out


def _drift_for(file_arg):
    """Run the read-only candidate/draft ⇄ gold drift diff (reuses
    tools.template_drift_report)."""
    _ensure_tools_on_path()
    from tools import template_drift_report as DR  # type: ignore

    # resolve the file inside the template tree (no traversal)
    safe = os.path.basename(file_arg or "")
    if not safe.endswith(".json"):
        return None, "invalid template filename"
    cand_path = None
    for sub in ("review_candidates", "drafts"):
        p = _REPO_ROOT / "templates" / sub / safe
        if p.is_file():
            cand_path = p
            break
    if cand_path is None:
        return None, "candidate/draft not found"
    cand = DR._load(str(cand_path))
    gold_rel = DR._default_gold(cand)  # takes the loaded dict; returns a relative path
    if not gold_rel:
        return None, "candidate has no host; cannot locate gold"
    gold_path = (_REPO_ROOT / gold_rel)
    if not gold_path.is_file():
        return None, "no reviewed gold for this host"
    gold = DR._load(str(gold_path))
    out = []
    DR.diff_selectors(cand, gold, out)
    DR.diff_row_selectors(cand, gold, out)
    DR.diff_resolutions(cand, gold, out)
    DR.diff_api(cand, gold, out)
    DR.diff_network_patterns(cand, gold, out)
    return {"ok": True, "file": safe, "gold": gold_path.name,
            "drift": out, "clean": not out}, None


@template_manager_ui_bp.route("/api/template_manager/inventory", methods=["GET"])
def api_template_manager_inventory():
    """Rich, read-only inventory of all four template dirs."""
    try:
        return jsonify(_inventory_data())
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


@template_manager_ui_bp.route("/api/template_manager/drift", methods=["GET"])
def api_template_manager_drift():
    """Read-only drift of a draft/candidate vs its reviewed gold. ?file=<name>."""
    try:
        res, err = _drift_for(request.args.get("file"))
        if err:
            return jsonify({"ok": False, "error": err}), 400
        return jsonify(res)
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)[:200]}), 500


# ── server-rendered read-only page ─────────────────────────────────

def _badge(text, kind):
    colors = {"ok": "#1b7f3b", "warn": "#9a6b00", "bad": "#9a1b1b",
              "muted": "#444"}
    return (f'<span style="background:{colors.get(kind, "#444")};color:#fff;'
            f'padding:1px 7px;border-radius:9px;font-size:11px;'
            f'white-space:nowrap">{html.escape(text)}</span>')


def _bar(score):
    color = "#1b7f3b" if score >= 85 else ("#9a6b00" if score >= 55 else "#9a1b1b")
    return (f'<div style="background:#222;border-radius:4px;width:120px;'
            f'height:10px;display:inline-block;vertical-align:middle">'
            f'<div style="background:{color};width:{max(0,min(100,score))}%;'
            f'height:10px;border-radius:4px"></div></div> '
            f'<span style="font-size:11px;color:#bbb">{score}/100</span>')


def _hint_labels(hints):
    """Compact label list from hint dicts (label-only; the dicts are already
    F2-clean — no query/token/PII — but we surface just the labels for the row)."""
    out = []
    for h in hints or []:
        if isinstance(h, dict):
            lbl = h.get("hint") or ""
            cat = h.get("kind") or h.get("category") or ""
            out.append(f"{lbl} ({cat})" if cat else lbl)
        else:
            out.append(str(h))
    return [x for x in out if x]


def _recognition_html(name, r):
    """Read-only render of the Wave 168 ``recognition`` block as an expandable
    sub-row. Returns "" when absent (pre-168 drafts) or for non-draft sections.
    Pure display: no selectors, no tokens, nothing actionable."""
    if name not in ("drafts", "review_candidates"):
        return ""
    rec = r.get("recognition")
    if not isinstance(rec, dict):
        return ""
    fam = rec.get("player_family") or "—"
    cands = rec.get("candidates") or []
    flags = rec.get("flags") or {}
    summary = [_badge("family: " + str(fam), "ok" if rec.get("player_family") else "muted")]
    if cands:
        summary.append(_badge(f"{len(cands)} candidate(s)", "muted"))
    if flags.get("drm"):
        summary.append(_badge("DRM — never bypass", "bad"))
    if flags.get("ad_overlay"):
        summary.append(_badge("ad_overlay", "warn"))
    _whints = rec.get("workflow_hints") or []
    _phints = rec.get("platform_hints") or []
    if any(h.get("kind") == "membership_workflow" for h in _whints if isinstance(h, dict)):
        summary.append(_badge("membership workflow", "warn"))
    _nhints = len(_whints) + len(_phints)
    if _nhints:
        summary.append(_badge(f"{_nhints} platform hint(s)", "muted"))

    def _kv(label, val):
        if val in (None, "", [], {}):
            return ""
        if isinstance(val, (list, dict)):
            val = json.dumps(val, sort_keys=True)
        return (f"<div style='margin:2px 0'><span style='color:#888'>{html.escape(label)}:</span> "
                f"<span style='color:#ccc'>{html.escape(str(val))}</span></div>")

    detail = "".join([
        _kv("candidates", cands),
        _kv("delivery", rec.get("delivery")),
        _kv("policy", rec.get("policy")),
        _kv("concerns", rec.get("concerns")),
        _kv("api_classes", rec.get("api_classes")),
        _kv("workflow_hints", _hint_labels(_whints)),
        _kv("platform_hints", _hint_labels(_phints)),
        _kv("notes", rec.get("notes")),
    ])
    body = (f"<details style='margin-top:4px'><summary style='cursor:pointer;color:#6cf;"
            f"font-size:12px'>recognition (review-only)</summary>"
            f"<div style='margin:6px 0 0 4px;font-size:12px'>{detail or '(no further detail)'}</div>"
            f"</details>")
    return ("<tr style='border-bottom:1px solid #222'>"
            "<td></td>"
            f"<td colspan='7' style='padding:2px 10px 8px'>{' '.join(summary)}{body}</td>"
            "</tr>")


def _row_html(name, r):
    host = html.escape(str(r["host"]))
    status = html.escape(str(r["status"]))
    sel = ", ".join(html.escape(s) for s in r["selector_groups"]) or "—"
    res = ", ".join(str(x) for x in r["resolutions"]) or "—"
    npat = (r["network_patterns_count"]
            if r["network_patterns_count"] is not None else "—")
    badges = []
    badges.append(_badge("gate-ready", "ok") if r["promotion_ready"]
                  else _badge("needs review", "warn"))
    if r["blocked_terms"]:
        badges.append(_badge("blocked: " + ",".join(r["blocked_terms"]), "bad"))
    if not r["download_trigger"]:
        badges.append(_badge("no trigger", "warn"))
    if not r["row_selectors_count"]:
        badges.append(_badge("no rows", "warn"))
    if not r["api_base"]:
        badges.append(_badge("no api.base", "muted"))
    drift = "—"
    if r["drift_available"]:
        fn = html.escape(os.path.basename(r["source"]))
        drift = (f'<a href="/api/template_manager/drift?file={fn}" '
                 f'style="color:#6cf">drift vs gold</a>')
    return (
        "<tr style='border-bottom:1px solid #222'>"
        f"<td style='padding:6px 10px'>{host}</td>"
        f"<td style='padding:6px 10px'>{status}</td>"
        f"<td style='padding:6px 10px'>{_bar(r['completeness_score'])}</td>"
        f"<td style='padding:6px 10px;font-size:12px;color:#bbb'>{sel}</td>"
        f"<td style='padding:6px 10px;font-size:12px'>{res}</td>"
        f"<td style='padding:6px 10px;text-align:center'>{npat}</td>"
        f"<td style='padding:6px 10px'>{' '.join(badges)}</td>"
        f"<td style='padding:6px 10px'>{drift}</td>"
        "</tr>") + _recognition_html(name, r)


def _section(title, name, rows):
    head = ("<tr style='text-align:left;color:#888;font-size:12px'>"
            "<th style='padding:6px 10px'>host</th><th style='padding:6px 10px'>status</th>"
            "<th style='padding:6px 10px'>completeness</th><th style='padding:6px 10px'>selectors</th>"
            "<th style='padding:6px 10px'>resolutions</th><th style='padding:6px 10px'>net</th>"
            "<th style='padding:6px 10px'>flags</th><th style='padding:6px 10px'>drift</th></tr>")
    body = "".join(_row_html(name, r) for r in rows) or (
        "<tr><td colspan='8' style='padding:10px;color:#666'>(none)</td></tr>")
    return (f"<h2 style='font-size:15px;margin:22px 0 6px'>{html.escape(title)} "
            f"<span style='color:#666;font-weight:normal'>({len(rows)})</span></h2>"
            f"<table style='border-collapse:collapse;width:100%'>{head}{body}</table>")


@template_manager_ui_bp.route("/cockpit/template-manager", methods=["GET"])
def cockpit_template_manager_page():
    """Read-only Template Manager page. NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    try:
        data = _inventory_data()
    except Exception as e:  # noqa: BLE001
        return (f"<h1>Template Manager</h1><p style='color:#c33'>inventory error: "
                f"{html.escape(str(e)[:200])}</p>"), 500
    dirs = data["dirs"]
    sanity = data["sanity"]
    sanity_html = ""
    if sanity:
        items = "".join(f"<li>{html.escape(s)}</li>" for s in sanity)
        sanity_html = (f"<div style='background:#3a1b1b;border:1px solid #9a1b1b;"
                       f"padding:8px 12px;border-radius:6px;margin:10px 0'>"
                       f"<b>Sanity violations</b><ul>{items}</ul></div>")
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Template Manager (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:1100px;margin:0 auto">
<div style="font-size:12px;margin:0 0 6px"><a href="/" style="color:#6cf;text-decoration:none">&larr; Home</a></div>
<h1 style="font-size:20px;margin:0 0 4px">Template Manager <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#23311b;border:1px solid #4a7a1b;padding:8px 12px;border-radius:6px;margin:8px 0;font-size:13px">
⚠ Read-only view — no promote / enable / swap here. <b>Needs operator click-through validation.</b>
Completeness &amp; “gate-ready” mirror the <code>promote_template.py</code> gate; they do not promote anything.
</div>
{sanity_html}
{_section("Reviewed (gold)", "reviewed", dirs.get("reviewed", []))}
{_section("Enabled", "enabled", dirs.get("enabled", []))}
{_section("Drafts", "drafts", dirs.get("drafts", []))}
{_section("Review candidates", "review_candidates", dirs.get("review_candidates", []))}
<p style="color:#555;font-size:11px;margin-top:24px">Generated read-only from templates/ — counts:
{html.escape(json.dumps(data["counts"]))}</p>
</div></body></html>"""
    return page


def register_routes(app) -> int:
    """Register the read-only Template Manager blueprint. Returns route count
    (mirrors the live-recorder / captcha-relay blueprint pattern)."""
    app.register_blueprint(template_manager_ui_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("template_manager_ui."))
