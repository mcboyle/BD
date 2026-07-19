"""app_report_center.py — additive, READ-ONLY cockpit Report Center (C).

A dedicated reporting section with sub-views — Template / Capture / Queue /
Release / Health (KB) Reports — each backed by the O data-layer APIs. Read-only,
no action affordances, auth via app.py's global hooks, not wired into app.py.

NEEDS OPERATOR CLICK-THROUGH VALIDATION.
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

from flask import Blueprint, jsonify

report_center_bp = Blueprint("report_center", __name__)
_REPO_ROOT = Path(__file__).resolve().parent.parent

SECTIONS = [
    {"key": "template", "title": "Template Reports", "api": "/api/data/template_health"},
    {"key": "capture", "title": "Capture Reports", "api": "/api/data/capture_analytics"},
    {"key": "diagnostics", "title": "Capture Diagnostics", "api": "/api/data/capture_diagnostics",
     "view": "/cockpit/reports/capture_diagnostics"},
    {"key": "replay", "title": "Replay Validation", "api": "/api/data/replay_validation",
     "view": "/cockpit/reports/replay_validation"},
    {"key": "queue", "title": "Queue Reports", "api": "/api/data/queue_analytics"},
    {"key": "release", "title": "Release Reports", "api": "/api/data/release_analytics"},
    {"key": "health", "title": "Health Reports (KB)", "api": "/api/data/kb_analytics"},
    {"key": "system", "title": "System Status (browser / deploy)",
     "api": "/api/data/deploy_health",
     "view": "/cockpit/reports/system_status"},
    {"key": "dom_recorder", "title": "DOM Recorder Status (G4)",
     "api": "/api/data/dom_recorder_status",
     "view": "/cockpit/reports/dom_recorder_status"},
    {"key": "workflow", "title": "Workflow Analytics / A6-1 (G5)",
     "api": "/api/data/workflow_analytics",
     "view": "/cockpit/reports/workflow_analytics"},
    {"key": "vpn_secrets", "title": "VPN + Secrets Status (read-only)",
     "api": "/api/data/vpn_status",
     "view": "/cockpit/reports/vpn_secrets_status"},
    {"key": "site_health", "title": "Site Health + Failure Clusters (F2-a)",
     "api": "/api/data/site_health",
     "view": "/cockpit/reports/site_health"},
]


@report_center_bp.route("/api/report_center/sections", methods=["GET"])
def api_sections():
    return jsonify({"ok": True, "sections": SECTIONS})


def _section_card(s):
    view = ("" if not s.get("view") else
            f"<div style='font-size:13px;margin-top:6px'>"
            f"<a href='{html.escape(s['view'])}' style='color:#9f9'>open panel &rarr;</a></div>")
    return (f"<div style='background:#141414;border:1px solid #262626;border-radius:8px;"
            f"padding:14px 16px'><h2 style='font-size:14px;margin:0 0 6px;color:#cfe'>"
            f"{html.escape(s['title'])}</h2>"
            f"<div style='font-size:13px'>data: "
            f"<a href='{html.escape(s['api'])}' style='color:#6cf'>{html.escape(s['api'])}</a></div>"
            f"{view}"
            f"<div style='color:#777;font-size:11px;margin-top:4px'>read-only · "
            f"loads from the dashboard data layer</div></div>")


@report_center_bp.route("/cockpit/reports", methods=["GET"])
def cockpit_reports_page():
    """Read-only Report Center. NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    cards = "".join(_section_card(s) for s in SECTIONS)
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Report Center (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:1000px;margin:0 auto">
<div style="font-size:12px;margin:0 0 6px"><a href="/cockpit/home" style="color:#6cf;text-decoration:none">&larr; Cockpit Home</a></div>
<h1 style="font-size:20px;margin:0 0 4px">Report Center <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#23311b;border:1px solid #4a7a1b;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Each section reads from the dashboard data layer (<code>/api/data/*</code>).
<b>Needs operator click-through validation.</b>
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px">
{cards}
</div>
</div></body></html>"""
    return page


_CAPTURE_DIAGNOSTICS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Capture Diagnostics (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:1100px;margin:0 auto">
<h1 style="font-size:20px;margin:0 0 4px">Capture Diagnostics <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#23311b;border:1px solid #4a7a1b;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Three axes per capture &mdash; <b>yield</b> (derivable-template completeness), <b>drift</b> (vs gold),
<b>runtime</b> (did the capture exercise the workflow). Verdict is advisory; it never overrides the promote gate.
<b>Needs operator click-through validation.</b>
</div>
<div id="note" style="color:#e8c466;font-size:13px;margin:8px 0"></div>
<div id="summary" style="display:flex;gap:10px;flex-wrap:wrap;margin:8px 0 14px"></div>
<table id="tbl" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<div id="err" style="color:#e0726f;font-size:13px;margin-top:12px"></div>
</div>
<script>
var COLORS={PROMOTABLE:"#7ee08a",REVIEW:"#e8c466",INSUFFICIENT_CAPTURE:"#e0726f",ERROR:"#888"};
function chip(label,val){var d=document.createElement("div");
  d.style.cssText="background:#141414;border:1px solid #262626;border-radius:6px;padding:6px 10px";
  var s=document.createElement("span");s.style.color="#888";s.textContent=label+": ";
  var v=document.createElement("b");v.textContent=String(val);
  d.appendChild(s);d.appendChild(v);return d;}
function cell(text,color){var td=document.createElement("td");
  td.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d";
  td.textContent=(text===null||text===undefined)?"-":String(text);
  if(color){td.style.color=color;td.style.fontWeight="600";}return td;}
fetch("/api/data/capture_diagnostics").then(function(r){return r.json();}).then(function(j){
  if(!j.ok){document.getElementById("err").textContent="error: "+(j.error||"unknown");return;}
  var d=j.data||{},agg=d.aggregate||{};
  if(d.note){document.getElementById("note").textContent=d.note;}
  var sum=document.getElementById("summary");
  [["captures",agg.n],["promotable",agg.promotable],["review",agg.review],
   ["insufficient",agg.insufficient],["errors",agg.errors],["mean score",agg.mean_completeness],
   ["json (not diagnosed)",d.json_captures]].forEach(function(p){sum.appendChild(chip(p[0],p[1]));});
  var tbl=document.getElementById("tbl");
  var head=["Host","Verdict","Score","Drift","Runtime","Missing"];
  var thead=document.createElement("thead"),htr=document.createElement("tr");
  head.forEach(function(h){var th=document.createElement("th");
    th.style.cssText="text-align:left;padding:6px 8px;border-bottom:1px solid #333;color:#9bd";
    th.textContent=h;htr.appendChild(th);});
  thead.appendChild(htr);tbl.appendChild(thead);
  var tb=document.createElement("tbody");
  (d.rows||[]).forEach(function(row){var tr=document.createElement("tr");
    if(row.verdict==="ERROR"){tr.appendChild(cell(row.path));
      tr.appendChild(cell("ERROR",COLORS.ERROR));tr.appendChild(cell(row.error));
      tr.appendChild(cell("-"));tr.appendChild(cell("-"));tr.appendChild(cell("-"));}
    else{tr.appendChild(cell(row.host));
      tr.appendChild(cell(row.verdict,COLORS[row.verdict]||"#ddd"));
      tr.appendChild(cell(row.completeness_score+"/100"));
      tr.appendChild(cell(row.drift_total));
      tr.appendChild(cell(row.runtime_readiness));
      tr.appendChild(cell((row.missing||[]).join(", ")||"none"));}
    tb.appendChild(tr);});
  tbl.appendChild(tb);
}).catch(function(e){document.getElementById("err").textContent="fetch failed: "+e;});
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/capture_diagnostics", methods=["GET"])
def cockpit_capture_diagnostics_page():
    """Read-only Capture Diagnostics panel (A4). Renders the three-axis verdict
    table from /api/data/capture_diagnostics client-side (no raw URLs cross the
    API). NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    return _CAPTURE_DIAGNOSTICS_PAGE


_REPLAY_VALIDATION_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Replay Validation (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:1000px;margin:0 auto">
<h1 style="font-size:20px;margin:0 0 4px">Replay Validation <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#23311b;border:1px solid #4a7a1b;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Headless rrweb replayability check per capture &mdash; snapshot ordering, node-id integrity, monotonic time.
Structure + ids only, never values. <b>Needs operator click-through validation.</b>
</div>
<div id="note" style="color:#e8c466;font-size:13px;margin:8px 0"></div>
<table id="tbl" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<div id="err" style="color:#e0726f;font-size:13px;margin-top:12px"></div>
</div>
<script>
function cell(text,color){var td=document.createElement("td");
  td.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d";
  td.textContent=(text===null||text===undefined)?"-":String(text);
  if(color){td.style.color=color;td.style.fontWeight="600";}return td;}
fetch("/api/data/replay_validation").then(function(r){return r.json();}).then(function(j){
  if(!j.ok){document.getElementById("err").textContent="error: "+(j.error||"unknown");return;}
  var d=j.data||{};
  if(d.note){document.getElementById("note").textContent=d.note;}
  var tbl=document.getElementById("tbl");
  var thead=document.createElement("thead"),htr=document.createElement("tr");
  ["Capture","Replayable","Errors","Warnings","Events"].forEach(function(h){
    var th=document.createElement("th");
    th.style.cssText="text-align:left;padding:6px 8px;border-bottom:1px solid #333;color:#9bd";
    th.textContent=h;htr.appendChild(th);});
  thead.appendChild(htr);tbl.appendChild(thead);
  var tb=document.createElement("tbody");
  (d.rows||[]).forEach(function(row){var tr=document.createElement("tr");
    tr.appendChild(cell(row.path));
    if(row.error!==undefined){tr.appendChild(cell("ERROR","#888"));
      tr.appendChild(cell(row.error));tr.appendChild(cell("-"));tr.appendChild(cell("-"));}
    else{tr.appendChild(cell(row.ok?"yes":"no",row.ok?"#7ee08a":"#e0726f"));
      tr.appendChild(cell(row.errors,row.errors?"#e0726f":"#ddd"));
      tr.appendChild(cell(row.warnings,row.warnings?"#e8c466":"#ddd"));
      tr.appendChild(cell(row.events));}
    tb.appendChild(tr);});
  tbl.appendChild(tb);
}).catch(function(e){document.getElementById("err").textContent="fetch failed: "+e;});
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/replay_validation", methods=["GET"])
def cockpit_replay_validation_page():
    """Read-only Replay Validation panel (A8). Renders headless rrweb
    replayability results from /api/data/replay_validation client-side
    (structure + ids only). NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    return _REPLAY_VALIDATION_PAGE


_SYSTEM_STATUS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>System Status (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:1000px;margin:0 auto">
<h1 style="font-size:20px;margin:0 0 4px">System Status <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#1b2331;border:1px solid #2f5070;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Browser/Cloak backend (G7) and deploy/operator health (G9). Presence + versions only, never secrets.
<b>Needs operator click-through validation.</b>
</div>
<h2 style="font-size:15px;color:#cfe;margin:14px 0 6px">Browser / Cloak (G7)</h2>
<table id="browser" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<h2 style="font-size:15px;color:#cfe;margin:18px 0 6px">Deploy / Operator health (G9)</h2>
<table id="deploy" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<div id="err" style="color:#e0726f;font-size:13px;margin-top:12px"></div>
</div>
<script>
function kv(tbl,key,val){
  var tr=document.createElement("tr");
  var k=document.createElement("td");
  k.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d;color:#9bd;width:240px";
  k.textContent=key;
  var v=document.createElement("td");
  v.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d";
  var s=(val===true)?"yes":(val===false)?"no":(val===null||val===undefined||val==="")?"-":String(val);
  v.textContent=s;
  if(val===true)v.style.color="#7ee08a";
  if(val===false)v.style.color="#e8c466";
  tr.appendChild(k);tr.appendChild(v);tbl.appendChild(tr);}
function load(url,tblId,keys){
  return fetch(url).then(function(r){return r.json();}).then(function(j){
    if(!j.ok){document.getElementById("err").textContent+=" "+url+": "+(j.error||"error");return;}
    var d=j.data||{},tbl=document.getElementById(tblId);
    keys.forEach(function(k){kv(tbl,k,d[k]);});
  }).catch(function(e){document.getElementById("err").textContent+=" fetch failed: "+e;});
}
load("/api/data/browser_status","browser",
  ["resolved_backend","available","version","display_set","import_error"]);
load("/api/data/deploy_health","deploy",
  ["app_version","deployed_version_marker","frontend_dist_present","rrweb_vendored","snapdom_vendored"]);
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/system_status", methods=["GET"])
def cockpit_system_status_page():
    """Read-only System Status panel (G7/G9). Renders browser/Cloak backend
    status and deploy/operator health from the data layer client-side
    (presence + versions only). NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    return _SYSTEM_STATUS_PAGE


_DOM_RECORDER_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>DOM Recorder Status (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:900px;margin:0 auto">
<h1 style="font-size:20px;margin:0 0 4px">DOM Recorder Status (G4) <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#1b2331;border:1px solid #2f5070;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Vendored asset presence (rrweb + snapdom), drop counter, and arm-fail streak.
Presence + counters only &mdash; never capture content. <b>Needs operator click-through validation.</b>
</div>
<div id="health-badge" style="display:inline-block;padding:4px 12px;border-radius:4px;font-size:13px;font-weight:600;margin-bottom:14px"></div>
<table id="tbl" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<div id="err" style="color:#e0726f;font-size:13px;margin-top:12px"></div>
</div>
<script>
var HEALTH_COLORS={ok:"#7ee08a",degraded:"#e8c466",error:"#e0726f"};
var HEALTH_BG={ok:"#1a2e1a",degraded:"#2e2a0e",error:"#2e0e0e"};
function kv(key,val){
  var tr=document.createElement("tr");
  var k=document.createElement("td");
  k.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d;color:#9bd;width:240px";
  k.textContent=key;
  var v=document.createElement("td");
  v.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d";
  var s=(val===true)?"yes":(val===false)?"no":(val===null||val===undefined||val==="")?"—":String(val);
  v.textContent=s;
  if(val===true)v.style.color="#7ee08a";
  if(val===false)v.style.color="#e8c466";
  if(key==="dom_events_dropped"&&val>0)v.style.color="#e8c466";
  if(key==="arm_fail_streak"&&val>=5)v.style.color="#e0726f";
  tr.appendChild(k);tr.appendChild(v);
  document.getElementById("tbl").appendChild(tr);}
fetch("/api/data/dom_recorder_status").then(function(r){return r.json();}).then(function(j){
  if(!j.ok){document.getElementById("err").textContent="error: "+(j.error||"unknown");return;}
  var d=j.data||{};
  var h=d.health||"unknown";
  var badge=document.getElementById("health-badge");
  badge.textContent=h.toUpperCase();
  badge.style.color=HEALTH_COLORS[h]||"#aaa";
  badge.style.background=HEALTH_BG[h]||"#222";
  badge.style.border="1px solid "+(HEALTH_COLORS[h]||"#555");
  ["vendor_complete","rrweb_present","rrweb_bytes","snapdom_present","snapdom_bytes",
   "dom_events_dropped","arm_fail_streak"].forEach(function(k){kv(k,d[k]);});
}).catch(function(e){document.getElementById("err").textContent="fetch failed: "+e;});
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/dom_recorder_status", methods=["GET"])
def cockpit_dom_recorder_status_page():
    """Read-only DOM Recorder Status panel (G4). Surfaces vendored asset
    presence, drop counter, and arm-fail streak from the data layer.
    NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    return _DOM_RECORDER_PAGE


_WORKFLOW_ANALYTICS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Workflow Analytics — A6-1 (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:1100px;margin:0 auto">
<h1 style="font-size:20px;margin:0 0 4px">Workflow Analytics / A6-1 (G5) <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#1b2331;border:1px solid #2f5070;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Derived workflow steps and modal-trigger candidates from the A6-1 capture-timeline analysis.
Structural labels only &mdash; no URLs, no values. Only drafts processed by the v3.66.175+ builder carry this data.
<b>Needs operator click-through validation.</b>
</div>
<div id="summary" style="font-size:13px;margin-bottom:14px;color:#9bd"></div>
<div id="err" style="color:#e0726f;font-size:13px;margin-bottom:12px"></div>
<div id="rows"></div>
</div>
<script>
function esc(s){return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}
function card(t){
  var d=document.createElement("div");
  d.style.cssText="background:#141414;border:1px solid #262626;border-radius:8px;padding:14px 16px;margin-bottom:10px";
  d.innerHTML=t;return d;}
fetch("/api/data/workflow_analytics").then(function(r){return r.json();}).then(function(j){
  if(!j.ok){document.getElementById("err").textContent="error: "+(j.error||"unknown");return;}
  var d=j.data||{};
  document.getElementById("summary").textContent=
    (d.total_drafts||0)+" draft(s) scanned · "+(d.with_workflow_data||0)+" with A6-1 data · "
    +(d.with_trigger_candidate||0)+" with trigger candidate";
  var cont=document.getElementById("rows");
  if(!(d.templates||[]).length){
    cont.innerHTML="<div style='color:#777;font-size:13px'>No draft templates found.</div>";return;}
  (d.templates||[]).forEach(function(t){
    var steps=(t.derived_steps||[]).map(function(s,i){
      return "<div style='padding:3px 0;font-size:12px;color:#ccc'>"+esc((i+1)+". "+s)+"</div>";
    }).join("")||"<span style='color:#555;font-size:12px'>— no workflow data (pre-175 draft)</span>";
    var trig=t.trigger_candidate
      ?"<span style='font-family:monospace;font-size:12px;color:#7ee08a'>"+esc(t.trigger_candidate)+"</span>"
        +(t.trigger_evidence?"<span style='color:#777;font-size:11px;margin-left:8px'>"+esc(t.trigger_evidence)+"</span>":"")
      :"<span style='color:#555;font-size:12px'>none</span>";
    cont.appendChild(card(
      "<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
      +"<span style='font-weight:600;color:#cfe;font-size:14px'>"+esc(t.host)+"</span>"
      +"<span style='font-size:11px;color:#666'>"+esc(t.bucket)+" · "+(t.confidence||"?")+" confidence</span>"
      +"</div>"
      +"<div style='font-size:12px;color:#9bd;margin-bottom:4px'>Trigger candidate:</div>"
      +"<div style='margin-bottom:10px'>"+trig+"</div>"
      +"<div style='font-size:12px;color:#9bd;margin-bottom:4px'>Derived steps:</div>"
      +steps
    ));
  });
}).catch(function(e){document.getElementById("err").textContent="fetch failed: "+e;});
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/workflow_analytics", methods=["GET"])
def cockpit_workflow_analytics_page():
    """Read-only Workflow Analytics panel (G5). Surfaces A6-1 derived_steps
    and trigger_candidate from draft templates. NEEDS OPERATOR CLICK-THROUGH
    VALIDATION."""
    return _WORKFLOW_ANALYTICS_PAGE


_VPN_SECRETS_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>VPN + Secrets Status (read-only)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:900px;margin:0 auto">
<h1 style="font-size:20px;margin:0 0 4px">VPN + Secrets Status <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#1b2331;border:1px solid #2f5070;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
Presence and state only &mdash; tunnel count, killswitch availability, secrets backend, credential count.
<b>Never displays secret values or key material.</b> Needs operator click-through validation.
</div>
<h2 style="font-size:15px;color:#cfe;margin:14px 0 6px">VPN</h2>
<table id="vpn" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<h2 style="font-size:15px;color:#cfe;margin:18px 0 6px">Secrets store</h2>
<table id="sec" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<div id="err" style="color:#e0726f;font-size:13px;margin-top:12px"></div>
</div>
<script>
function kv(tbl,key,val){
  var tr=document.createElement("tr");
  var k=document.createElement("td");
  k.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d;color:#9bd;width:240px";
  k.textContent=key;
  var v=document.createElement("td");
  v.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d";
  var s=(val===true)?"yes":(val===false)?"no":(val===null||val===undefined||val==="")?"—":String(val);
  v.textContent=s;
  if(val===true)v.style.color="#7ee08a";
  if(val===false)v.style.color="#e8c466";
  tr.appendChild(k);tr.appendChild(v);tbl.appendChild(tr);}
function load(url,tblId,keys){
  return fetch(url).then(function(r){return r.json();}).then(function(j){
    if(!j.ok){document.getElementById("err").textContent+=url+": "+(j.error||"error")+" ";return;}
    var d=j.data||{},tbl=document.getElementById(tblId);
    keys.forEach(function(k){kv(tbl,k,Array.isArray(d[k])?d[k].join(", "):d[k]);});
  }).catch(function(e){document.getElementById("err").textContent+="fetch failed: "+e;});}
load("/api/data/vpn_status","vpn",
  ["tunnel_count","provider_count","providers","system_killswitch_available",
   "system_killswitch_reason","active_killswitches"]);
load("/api/data/secrets_status","sec",
  ["backend","is_unlocked","credential_count","credentials_enumerable"]);
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/vpn_secrets_status", methods=["GET"])
def cockpit_vpn_secrets_status_page():
    """Read-only VPN + Secrets status panel. Presence/state only — tunnel count,
    killswitch availability, secrets backend/count. Never secret values.
    NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    return _VPN_SECRETS_PAGE


_SITE_HEALTH_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Site Health + Failure Clusters (F2-a)</title></head>
<body style="background:#0d0d0d;color:#ddd;font-family:system-ui,Segoe UI,Arial;margin:0;padding:24px">
<div style="max-width:980px;margin:0 auto">
<div style="font-size:12px;margin:0 0 6px"><a href="/cockpit/reports" style="color:#6cf;text-decoration:none">&larr; Report Center</a></div>
<h1 style="font-size:20px;margin:0 0 4px">Site Health + Failure Clusters <span style="color:#666;font-size:13px">read-only</span></h1>
<div style="background:#1b2331;border:1px solid #2f5070;padding:8px 12px;border-radius:6px;margin:8px 0 16px;font-size:13px">
F2.1 failure clustering + F2.2 per-site health over a recent window. Derived from
auth-health + session_history (counts and labels only &mdash; no URLs, no values).
<b>Needs operator click-through validation.</b>
</div>
<div style="font-size:13px;color:#9bd;margin:0 0 10px">Window: <b id="win">7</b> days &middot;
total failures: <b id="totf">&mdash;</b> &middot; sites: <b id="nsite">&mdash;</b></div>
<h2 style="font-size:15px;color:#cfe;margin:14px 0 6px">Top failure clusters (F2.1)</h2>
<table id="clusters" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<h2 style="font-size:15px;color:#cfe;margin:18px 0 6px">Per-site health (F2.2, worst first)</h2>
<table id="sites" style="width:100%;border-collapse:collapse;font-size:13px"></table>
<div id="err" style="color:#e0726f;font-size:13px;margin-top:12px"></div>
</div>
<script>
function th(row,cells){cells.forEach(function(c){var t=document.createElement("th");
  t.style.cssText="text-align:left;padding:6px 8px;border-bottom:1px solid #2a2a2a;color:#9bd";
  t.textContent=c;row.appendChild(t);});}
function td(row,val,color){var t=document.createElement("td");
  t.style.cssText="padding:6px 8px;border-bottom:1px solid #1d1d1d";
  t.textContent=(val===null||val===undefined||val==="")?"\\u2014":String(val);
  if(color)t.style.color=color;row.appendChild(t);}
function labelColor(l){return l==="critical"?"#e0726f":l==="warn"?"#e8c466":"#7ee08a";}
function dotColor(c){return c==="red"?"#e0726f":c==="yellow"?"#e8c466":c==="green"?"#7ee08a":"#888";}
function ago(s){if(s===null||s===undefined)return "\\u2014";s=Math.round(s);
  if(s<3600)return Math.round(s/60)+"m";if(s<86400)return Math.round(s/3600)+"h";
  return Math.round(s/86400)+"d";}
fetch("/api/data/site_health").then(function(r){return r.json();}).then(function(j){
  if(!j.ok){document.getElementById("err").textContent=j.error||"error";return;}
  var d=j.data||{};
  document.getElementById("win").textContent=d.lookback_days;
  document.getElementById("totf").textContent=d.total_failures;
  document.getElementById("nsite").textContent=d.site_count;
  var ct=document.getElementById("clusters");
  var hr=document.createElement("tr");th(hr,["Site","Failure type","Count","Last seen"]);ct.appendChild(hr);
  (d.clusters||[]).forEach(function(c){var tr=document.createElement("tr");
    td(tr,c.site_id);td(tr,c.event_type,"#e8c466");td(tr,c.count);
    td(tr,c.last_ts?ago((Date.now()/1000)-c.last_ts)+" ago":"\\u2014");ct.appendChild(tr);});
  if(!(d.clusters||[]).length){var tr=document.createElement("tr");td(tr,"no failures in window");ct.appendChild(tr);}
  var st=document.getElementById("sites");
  var sh=document.createElement("tr");th(sh,["Site","Auth","Health","Score","Failures","Fail rate","Median life"]);st.appendChild(sh);
  (d.sites||[]).forEach(function(s){var tr=document.createElement("tr");
    td(tr,s.site_id);td(tr,s.color||"\\u2014",dotColor(s.color));
    td(tr,s.health_label,labelColor(s.health_label));td(tr,s.health_score);
    td(tr,s.failures);
    td(tr,s.fail_rate===null?"\\u2014":(Math.round(s.fail_rate*100)+"%"));
    td(tr,s.median_lifetime_sec===null?"\\u2014":ago(s.median_lifetime_sec));st.appendChild(tr);});
  if(!(d.sites||[]).length){var tr2=document.createElement("tr");td(tr2,"no sites");st.appendChild(tr2);}
}).catch(function(e){document.getElementById("err").textContent="fetch failed: "+e;});
</script>
</body></html>"""


@report_center_bp.route("/cockpit/reports/site_health", methods=["GET"])
def cockpit_site_health_page():
    """Read-only Site Health + Failure Clusters panel (F2-a / F2.1 + F2.2).
    F2.1 failure clustering + F2.2 4-input per-site health score, derived from
    auth_health + session_history. Counts/labels only — no URLs, no values.
    NEEDS OPERATOR CLICK-THROUGH VALIDATION."""
    return _SITE_HEALTH_PAGE


def register_routes(app) -> int:
    app.register_blueprint(report_center_bp)
    return sum(1 for r in app.url_map.iter_rules()
               if r.endpoint.startswith("report_center."))
