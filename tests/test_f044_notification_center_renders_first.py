"""F044 (APPFEAT-fix r2): the Cockpit Notification Center sat on a skeleton for 16-18 s because
GET /cockpit/api/autonomy/notifications derived the next-generation alert count (operator
mission control over every site) before returning the current list. The panel now asks for the
list alone (?would=0, no derivation) and fills the count from the full GET after it renders.

1) server: ?would=0 never calls the derivation; the default GET still does (contract unchanged).
2) panel: PAGES.notifcenter, run in node against a stub api() whose full GET stays pending,
   renders before that GET resolves and then fills the count from it.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from flask import Flask

from tools import autonomy_housekeeping as hk
from tools import cockpit_templates as ct

BD_GATE_SCOPE = "module"
ROOT = Path(__file__).resolve().parent.parent
COCKPIT = ROOT / "tools" / "cockpit_console.py"


@pytest.fixture
def client(monkeypatch):
    from tools.cockpit_console import api_autonomy_notifications

    calls = []

    def mission_control():
        calls.append(1)
        return {"needs_attention": {"broken_login_templates": ["alpha", "beta"]}}

    monkeypatch.setattr(ct, "operator_mission_control", mission_control)
    monkeypatch.setattr(hk, "_guard", lambda *args: None)
    current = [
        {
            "id": "n_1",
            "ts": "2026-10-13T00:00:00Z",
            "kind": "high_drift",
            "site": "alpha",
        }
    ]
    monkeypatch.setattr(
        hk,
        "list_notifications",
        lambda: {"notifications": current, "generated_at": "2026-10-13T00:00:00Z"},
    )
    app = Flask(__name__)
    app.add_url_rule(
        "/cockpit/api/autonomy/notifications", view_func=api_autonomy_notifications
    )
    return app.test_client(), calls, current


def test_the_list_alone_skips_the_next_generation_derivation(client):
    c, calls, current = client
    r = c.get("/cockpit/api/autonomy/notifications?would=0")
    assert r.status_code == 200
    body = r.get_json()
    assert body["notifications"] == current
    assert body["would_create"] is None and body["would_create_pending"] is True, body
    assert calls == [], (
        f"F044_DERIVATION_ON_LIST_GET: mission control ran {len(calls)}x for ?would=0"
    )


def test_the_default_get_still_derives_the_count(client):
    c, calls, current = client
    body = c.get("/cockpit/api/autonomy/notifications").get_json()
    assert body["notifications"] == current
    assert body["would_create"] == 2 and "would_create_pending" not in body, body
    assert calls == [1]


def _notifcenter_js():
    src = COCKPIT.read_text(encoding="utf-8")
    m = re.search(r"\nPAGES\.notifcenter=async\(\)=>\{.*?\n\};\n", src, re.DOTALL)
    assert m, "PAGES.notifcenter renderer not found"
    return m.group(0)


HARNESS = r"""
const calls=[]; const els={}; let releaseFull=null;
global.document={getElementById:id=>els[id]||null};
function esc(s){return String(s);} function banner(){return '';}
async function api(p){calls.push(p);
  if(p==='/api/autonomy/notifications?would=0')
    return {notifications:[{ts:'t0',kind:'high_drift',message:'alpha drift'}],would_create:null,_note:'n'};
  return new Promise(r=>{releaseFull=()=>r({notifications:[],would_create:7,_note:'n'});});}
const PAGES={};
%s
(async()=>{
  const timer=new Promise(r=>setTimeout(()=>r(null),1500));
  const html=await Promise.race([PAGES.notifcenter(),timer]);
  const out={calls:calls.slice(),rendered:html!==null};
  if(html!==null){
    out.has_slot=html.includes('id="nc-would"'); out.has_row=html.includes('alpha drift');
    const cls=new Set(['muted']); els['nc-would']={textContent:'…',classList:{remove:c=>cls.delete(c)}};
    if(releaseFull) releaseFull();
    await new Promise(r=>setTimeout(r,50));
    out.count=els['nc-would'].textContent; out.muted=cls.has('muted');
  }
  console.log(JSON.stringify(out));
  process.exit(0);
})();
"""


def test_the_panel_renders_before_the_count_and_then_fills_it(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("COULD NOT LOOK: no node on this host")
    script = tmp_path / "notifcenter.js"
    script.write_text(HARNESS % _notifcenter_js(), encoding="utf-8")
    r = subprocess.run(
        [node, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": os.environ.get("PATH", ""), "LC_ALL": "C.UTF-8"},
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["rendered"], (
        f"F044_PANEL_BLOCKED: the panel awaited the full GET before rendering: {out}"
    )
    assert out["calls"][0] == "/api/autonomy/notifications?would=0", out
    assert out["has_slot"] and out["has_row"], out
    assert "/api/autonomy/notifications" in out["calls"], out
    assert out["count"] == "7" and out["muted"] is False, out
