"""scs-fix r2 F3 -- the cockpit Home strip must not say "Health OK" when /api/health failed.

/api/health answers 503 with {"ok": false, ...} when the app is degraded, and apiRoot()
throws on any non-2xx. PAGES.home caught that with `.catch(()=>({}))` and then computed
healthy=(ok!==false)&&(db_ok!==false) on the empty object, so the strip painted
"Health OK" exactly when the probe reported a failure (and when the fetch never answered).

These run the real api()/apiRoot()/PAGES.home source from tools/cockpit_console.py under
Node with a stubbed fetch, and read the rendered Home strip.
"""

# An ordinary module test: its subject is the cockpit Home renderer, not the tree.
BD_GATE_SCOPE = "module"

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO / "tools"))

import cockpit_console as CC  # noqa: E402

DIAG = "BD-SCS-F3-HEALTH-OK-ON-FAILURE"

_HARNESS = r"""
const SCEN=JSON.parse(process.argv[2]);
const document={querySelector:()=>null};
function resp(status,body,raw){
  const txt=raw!=null?raw:JSON.stringify(body);
  return {ok:status>=200&&status<300,status,text:async()=>txt,json:async()=>JSON.parse(txt)};
}
globalThis.fetch=async(url,o)=>{
  if(url==='/cockpit/api/debt')return resp(200,{correction:0,capability:0,validation:0,entries:0});
  if(url==='/cockpit/api/tasks')return resp(200,{tasks:[]});
  if(url==='/api/health'){
    if(SCEN.reject)throw new TypeError('Failed to fetch');
    return resp(SCEN.status,SCEN.body,SCEN.raw);
  }
  throw new Error('unexpected fetch '+url);
};
"""


def _slice(src: str, start: str, end: str) -> str:
    i = src.index(start)
    j = src.index(end, i)
    return src[i:j]


def _render_home(scenario: dict) -> str:
    node = shutil.which("node")
    assert node is not None, "COULD NOT LOOK: Node is unavailable, so PAGES.home was not run"
    src = CC._PAGE
    helpers = _slice(src, "async function api(", "const esc=")
    home = _slice(src, "PAGES.home=async()=>{", "\n};\n") + "\n};\n"
    js = (_HARNESS + helpers + "const PAGES={};\n" + home
          + "PAGES.home().then(h=>process.stdout.write(h))"
          + ".catch(e=>{console.error('HOME-THREW '+e);process.exit(3);});\n")
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "home.js"
        f.write_text(js, encoding="utf-8")
        r = subprocess.run([node, str(f), json.dumps(scenario)],
                           capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, f"PAGES.home did not render: rc={r.returncode}\n{r.stderr}"
    return r.stdout


def _strip(html: str) -> str:
    return _slice(html, '<div class="hrel">', "</div>")


def test_health_503_degraded_is_not_ok():
    body = {"ok": False, "db_ok": False, "version": "9.9.9", "degraded": "db_error: OperationalError"}
    strip = _strip(_render_home({"status": 503, "body": body}))
    assert "Health <b>OK</b>" not in strip, f"{DIAG}: 503 ok:false rendered as healthy: {strip}"
    assert "Health <b>FAIL</b>" in strip and 'class="dot bad"' in strip, strip
    assert "v9.9.9" in strip, f"503 body version lost: {strip}"


def test_health_fetch_failure_is_unknown_not_ok():
    strip = _strip(_render_home({"reject": True}))
    assert "Health <b>OK</b>" not in strip, f"{DIAG}: failed fetch rendered as healthy: {strip}"
    assert "Health <b>unknown</b>" in strip and 'class="dot bad"' in strip, strip


def test_health_unreadable_body_is_unknown_not_ok():
    strip = _strip(_render_home({"status": 200, "raw": "<html>login</html>"}))
    assert "Health <b>OK</b>" not in strip, f"{DIAG}: non-JSON 200 rendered as healthy: {strip}"
    assert "Health <b>unknown</b>" in strip, strip


def test_health_502_html_body_is_fail():
    # non-2xx whose body is not JSON: the status alone must fail the strip
    strip = _strip(_render_home({"status": 502, "raw": "<html>bad gateway</html>"}))
    assert "Health <b>FAIL</b>" in strip, f"{DIAG}: 502 with an HTML body not rendered as FAIL: {strip}"


def test_health_503_empty_json_body_is_fail():
    # non-2xx whose JSON body lacks ok:false: the status alone must fail the strip
    strip = _strip(_render_home({"status": 503, "raw": "{}"}))
    assert "Health <b>FAIL</b>" in strip, f"{DIAG}: 503 with body {{}} not rendered as FAIL: {strip}"


def test_health_ok_still_renders_ok():
    # positive control: the probe can still say yes
    strip = _strip(_render_home({"status": 200, "body": {"ok": True, "db_ok": True, "version": "3.66.1"}}))
    assert "Health <b>OK</b>" in strip and 'class="dot "' in strip and "v3.66.1" in strip, strip
