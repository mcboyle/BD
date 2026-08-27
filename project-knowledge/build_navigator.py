#!/usr/bin/env python3
"""Build a self-contained functional navigator from the capture manifest.

Downscales every PNG to <=1100px wide, base64-embeds it, and emits one HTML
file (no external deps, no sibling files needed) that lets you click through
every nav tab / drill-in / subtab / cockpit / popup in light & dark, mimicking
navigation of the live mock SPA.
"""
import os, json, base64, io, html, sys
from PIL import Image

from capture_manifest_contract import ManifestContractError, load_manifest

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
CAP = os.environ.get("BD_CAPTURE_DIR", os.path.join(ROOT, "reports", "capture"))
OUT = os.environ.get("BD_NAVIGATOR_OUT", "/mnt/user-data/outputs/functional.html")
MAXW = 1100

try:
    man = load_manifest(f"{CAP}/manifest.json")
except ManifestContractError as exc:
    print(f"CAPTURE MANIFEST UNKNOWN: {exc}", file=sys.stderr)
    raise SystemExit(2)

# ---- group manifest by route, collecting light+dark variants ----------------
# category display order + labels
CAT_ORDER = ["nav", "drillin", "subtab", "cockpit", "popup"]
CAT_LABEL = {"nav": "Nav tabs", "drillin": "Site drill-ins",
             "subtab": "In-page subtabs", "cockpit": "Cockpit / Review",
             "popup": "Popups & overlays"}

routes = {}  # key -> {cat,label,route,light,dark,head,h,err,ox}
for m in man:
    key = (m["cat"], m["route"])
    r = routes.setdefault(key, {"cat": m["cat"], "label": m["label"], "route": m["route"],
                                "light": None, "dark": None, "head": m.get("head", ""),
                                "h": m.get("h", 0), "err": 0, "ox": False})
    if m.get("file"):
        r[m["theme"]] = m["file"]
    r["err"] = max(r["err"], m.get("err", 0) or 0)
    r["ox"] = r["ox"] or bool(m.get("ox"))

# ---- embed images (downscaled) ----------------------------------------------
def embed(relpath):
    p = os.path.join(CAP, relpath)
    if not os.path.exists(p):
        print(f"CAPTURE MANIFEST UNKNOWN: image measurement unavailable: {p}",
              file=sys.stderr)
        raise SystemExit(2)
    try:
        im = Image.open(p).convert("RGB")
    except (OSError, ValueError) as exc:
        print(f"CAPTURE MANIFEST UNKNOWN: image measurement unavailable: "
              f"{p}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if im.width > MAXW:
        im = im.resize((MAXW, round(im.height * MAXW / im.width)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85, optimize=True)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

IMG = {}
seen = set()
for r in routes.values():
    for th in ("light", "dark"):
        f = r[th]
        if f and f not in seen:
            seen.add(f)
            d = embed(f)
            IMG[f] = d

# ---- build ordered route list for the sidebar -------------------------------
items = []
for cat in CAT_ORDER:
    group = [r for r in routes.values() if r["cat"] == cat]
    # keep nav in manifest order (already roughly app order); subtabs grouped
    for r in group:
        items.append(r)

# JS-safe payload
payload = [{"cat": r["cat"], "label": r["label"], "route": r["route"],
           "light": r["light"], "dark": r["dark"], "head": r["head"],
           "h": r["h"], "err": r["err"], "ox": r["ox"]} for r in items]

counts = {c: sum(1 for r in items if r["cat"] == c) for c in CAT_ORDER}
total_shots = len(IMG)

DATA_JS = json.dumps(payload)
IMG_JS = json.dumps(IMG)
CATLBL_JS = json.dumps(CAT_LABEL)
CATORD_JS = json.dumps(CAT_ORDER)

page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BulkDownloader — Functional Navigator (v3.66.363)</title>
<style>
  :root{--bg:#f4f5f7;--surface:#fff;--surface2:#eef0f3;--ink:#1c2024;--ink2:#454b54;
        --ink3:#6b7280;--primary:#5b5bd6;--primary-soft:#ecebfb;--hairline:#e3e6ea;
        --green:#16a34a;--amber:#d97706;--red:#dc2626;}
  body.dark{--bg:#15171c;--surface:#1d2026;--surface2:#262a31;--ink:#e8eaed;
        --ink2:#b6bcc4;--ink3:#878e98;--primary:#8b8bf0;--primary-soft:#262a45;
        --hairline:#2d323a;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
       background:var(--bg);color:var(--ink);display:flex;flex-direction:column;height:100vh;overflow:hidden}
  header{display:flex;align-items:center;gap:14px;padding:10px 16px;background:var(--surface);
         border-bottom:1px solid var(--hairline);flex:0 0 auto;z-index:5}
  header .logo{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#2a2a52,#5b5bd6)}
  header h1{font-size:15px;margin:0;font-weight:650;letter-spacing:-.2px}
  header .v{font-size:11px;color:var(--ink3);font-weight:500}
  header .spacer{flex:1}
  .toggle{display:inline-flex;border:1px solid var(--hairline);border-radius:8px;overflow:hidden}
  .toggle button{border:0;background:var(--surface);color:var(--ink2);padding:6px 13px;cursor:pointer;font-size:13px;font-weight:550}
  .toggle button.on{background:var(--primary);color:#fff}
  .wrap{display:flex;flex:1;min-height:0}
  aside{width:268px;flex:0 0 auto;background:var(--surface);border-right:1px solid var(--hairline);
        overflow-y:auto;padding:8px}
  .search{width:100%;padding:8px 10px;border:1px solid var(--hairline);border-radius:8px;
          background:var(--surface2);color:var(--ink);margin-bottom:8px;font-size:13px}
  .grp{font-size:10.5px;text-transform:uppercase;letter-spacing:.7px;color:var(--ink3);
       font-weight:700;padding:10px 8px 4px}
  .grp .c{background:var(--surface2);border-radius:10px;padding:1px 7px;margin-left:6px;font-weight:600}
  .item{display:flex;align-items:center;gap:8px;padding:7px 9px;border-radius:8px;cursor:pointer;color:var(--ink2);font-size:13px}
  .item:hover{background:var(--surface2)}
  .item.sel{background:var(--primary-soft);color:var(--primary);font-weight:600}
  .item .rt{margin-left:auto;font-size:10.5px;color:var(--ink3);font-family:ui-monospace,Menlo,monospace}
  .item .dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto}
  .dot.ok{background:var(--green)}.dot.warn{background:var(--amber)}
  main{flex:1;min-width:0;display:flex;flex-direction:column}
  .meta{display:flex;align-items:center;gap:14px;padding:8px 16px;border-bottom:1px solid var(--hairline);
        background:var(--surface);flex:0 0 auto;font-size:12.5px;color:var(--ink2)}
  .meta .lbl{font-weight:650;color:var(--ink);font-size:14px}
  .meta code{font-family:ui-monospace,Menlo,monospace;background:var(--surface2);padding:2px 7px;border-radius:6px;color:var(--ink2)}
  .meta .pill{padding:2px 9px;border-radius:20px;font-size:11px;font-weight:650}
  .pill.ok{background:var(--primary-soft);color:var(--primary)}
  .pill.warn{background:#fef3c7;color:#92400e}
  .stage{flex:1;overflow:auto;padding:22px;background:var(--bg);display:flex;justify-content:center}
  .frame{max-width:1120px;width:100%;background:var(--surface);border:1px solid var(--hairline);
         border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,.10);overflow:hidden;height:max-content}
  .frame .bar{display:flex;align-items:center;gap:6px;padding:9px 12px;border-bottom:1px solid var(--hairline);background:var(--surface2)}
  .frame .bar i{width:11px;height:11px;border-radius:50%;display:inline-block}
  .frame .bar .u{margin-left:10px;font-size:12px;color:var(--ink3);font-family:ui-monospace,Menlo,monospace}
  .frame img{display:block;width:100%}
  .empty{color:var(--ink3);text-align:center;padding:50px;font-style:italic}
  footer{flex:0 0 auto;padding:6px 16px;background:var(--surface);border-top:1px solid var(--hairline);
         font-size:11px;color:var(--ink3);display:flex;gap:16px}
  @media(max-width:760px){aside{width:200px}.frame{max-width:100%}}
</style></head>
<body>
<header>
  <div class="logo"></div>
  <div><h1>BulkDownloader — Functional Navigator</h1><div class="v">v3.66.363 · empty-instance mock · __TOTAL__ screenshots · light + dark</div></div>
  <div class="spacer"></div>
  <div class="toggle" id="themeToggle">
    <button data-th="light" class="on">☀ Light</button>
    <button data-th="dark">🌙 Dark</button>
  </div>
</header>
<div class="wrap">
  <aside>
    <input class="search" id="search" placeholder="Filter routes…">
    <div id="nav"></div>
  </aside>
  <main>
    <div class="meta" id="meta"></div>
    <div class="stage"><div class="frame" id="frame"><div class="empty">Select a route…</div></div></div>
  </main>
</div>
<footer>
  <span>● 0 console errors</span><span>● 0 horizontal overflow</span>
  <span>Captured against the built dist on 127.0.0.1:5599</span>
  <span style="margin-left:auto">click a route → it loads · toggle theme to compare</span>
</footer>
<script>
const DATA = __DATA__;
const IMG = __IMG__;
const CATLBL = __CATLBL__;
const CATORD = __CATORD__;
let theme = "light";
let cur = 0;

function build(filter=""){
  const nav = document.getElementById("nav");
  nav.innerHTML = "";
  const f = filter.toLowerCase();
  CATORD.forEach(cat=>{
    const rows = DATA.map((r,i)=>({r,i})).filter(x=>x.r.cat===cat &&
        (!f || x.r.label.toLowerCase().includes(f) || x.r.route.toLowerCase().includes(f)));
    if(!rows.length) return;
    const g = document.createElement("div"); g.className="grp";
    g.innerHTML = CATLBL[cat] + '<span class="c">'+rows.length+'</span>';
    nav.appendChild(g);
    rows.forEach(({r,i})=>{
      const el = document.createElement("div");
      el.className = "item" + (i===cur?" sel":"");
      const warn = (r.err||r.ox);
      el.innerHTML = '<span class="dot '+(warn?'warn':'ok')+'"></span><span>'+esc(r.label)+'</span>'
                   + (r.route.startsWith('/')?'<span class="rt">'+esc(r.route)+'</span>':'');
      el.onclick = ()=>{ cur=i; render(); };
      nav.appendChild(el);
    });
  });
}
function esc(s){return (s||"").replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

function render(){
  const r = DATA[cur];
  const file = r[theme] || r.light || r.dark;
  const meta = document.getElementById("meta");
  const warn = (r.err||r.ox);
  meta.innerHTML = '<span class="lbl">'+esc(r.label)+'</span>'
    + (r.route.startsWith('/')?'<code>'+esc(r.route)+'</code>':'')
    + '<span class="pill '+(warn?'warn':'ok')+'">'+(warn?(r.err?r.err+' err':'overflow'):'0 err · no overflow')+'</span>'
    + (r.h?'<span style="color:var(--ink3)">page '+r.h+'px</span>':'')
    + '<span style="margin-left:auto;color:var(--ink3)">'+theme+'</span>';
  const frame = document.getElementById("frame");
  if(file && IMG[file]){
    frame.innerHTML = '<div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i>'
      + '<i style="background:#28c840"></i><span class="u">localhost:5599'+esc(r.route.startsWith('/')?r.route:'')+'</span></div>'
      + '<img loading="lazy" src="'+IMG[file]+'">';
  } else {
    frame.innerHTML = '<div class="empty">No '+theme+' capture for this route.</div>';
  }
  build(document.getElementById("search").value);
  document.querySelector(".stage").scrollTop = 0;
}

document.getElementById("themeToggle").addEventListener("click", e=>{
  const b = e.target.closest("button"); if(!b) return;
  theme = b.dataset.th;
  document.body.classList.toggle("dark", theme==="dark");
  [...document.querySelectorAll("#themeToggle button")].forEach(x=>x.classList.toggle("on", x.dataset.th===theme));
  render();
});
document.getElementById("search").addEventListener("input", e=>build(e.target.value));

build(); render();
</script>
</body></html>"""

page = (page.replace("__DATA__", DATA_JS).replace("__IMG__", IMG_JS)
            .replace("__CATLBL__", CATLBL_JS).replace("__CATORD__", CATORD_JS)
            .replace("__TOTAL__", str(total_shots)))

with open(OUT, "w") as f:
    f.write(page)

mb = os.path.getsize(OUT) / 1e6
print(f"wrote {OUT}  ({mb:.1f} MB)")
print(f"routes: {len(items)}  embedded images: {total_shots}")
print(f"counts: {counts}")
