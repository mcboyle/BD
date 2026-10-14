"""cookie-file-roots (finder R38-B / pm-A3-A, HIGH): a cookie_file outside the path roots was stored by 3 write routes.

s28 closed PUT /api/sites/<sid> only.  POST /api/sites (accounts[]), POST /api/sites/import and POST /api/config/import
(site cookie_file and accounts[]) answered 200, wrote the path to sites_config.json, and SiteRunner opened it at the next
start (the active account's cookie_file is copied over the site's).

One validator, app._validate_cookie_files, now judges the site's cookie_file and every accounts[] element's on every
route.  Each (route, field) group below is ONE fresh process; each cell resets two live sites, sends one request and
measures the answer, the file bytes, the live dictionaries and the runner registry.
Scratch BD_HOME under tmp_path only; Flask test_client; no network, no browser.
"""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
REPO = Path(__file__).resolve().parents[1]

CHILD = r"""
import contextlib, copy, io, json, os, sys, threading
mode, home, arg = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
os.environ.update(BD_HOME=home, BD_INSTALL_DIR=home, BD_DISABLE_KEEPALIVE="1"); os.chdir(home)
from bulk_downloader import app as A, app_state as S
A.SITES_FILE = A.Path(home) / "sites_config.json"
out = {"euid": os.geteuid(), "roots": len(A._app_cfg.get("path_allowlist") or [])}

J = os.path.join
for d in ("d", "adir", "locked/sub"):
    os.makedirs(J(home, d), exist_ok=True)
with open(J(home, "ok.json"), "w") as fh: fh.write("[]")
for link, target in ((J(home, "link.json"), "/etc/hostname"), (J(home, "linkdir"), "/etc")):
    if not os.path.islink(link): os.symlink(target, link)
if not os.path.exists(J(home, "fifo.json")): os.mkfifo(J(home, "fifo.json"))
VALUES = {
    "good": J(home, "ok.json"),                       # control: a regular file inside the roots
    "absent": J(home, "not-there-yet.json"),          # control: inside the roots, not created yet
    "outside-abs": "/etc/hostname",
    "dotdot": J(home, "d", "..", "..", "..", "..", "..", "etc", "hostname"),
    "relative": "etc/hostname",
    "symlink-file": J(home, "link.json"),             # inside the roots by name, /etc/hostname by realpath
    "symlink-dir": J(home, "linkdir", "hostname"),
    "long": J(home, *(["A" * 200] * 25)),             # OSError 36 from the OS
    "unreadable": J(home, "locked", "sub", "c.json"), # PermissionError from the OS (parent is mode 0)
    "nul": J(home, "a\x00b.json"),
    "directory": J(home, "adir"),
    "fifo": J(home, "fifo.json"),                     # open() of it blocks for ever
    "not-string": 7,
    "pad-trailing": J(home, "ok.json") + " ",        # r2 (lens F2): judged stripped, stored padded
    "pad-leading": " " + J(home, "ok.json"),
}

def clean(sid):
    c = {k: A.DEFAULTS.get(k, "") for k in A.CFG_FIELDS}
    c.update(name=sid, download_dir=J(home, "dl", sid), cookie_file=J(home, sid + ".json"))
    return c

class FakeRunner:
    def __init__(self, sid): self.sid = sid; self.urls = []; self.jobs = {}; self._lock = threading.Lock(); self.updates = 0; self._active_account_idx = 0
    def update_config(self, cfg): self.updates += 1
    def set_cookies_from_file(self, *_a): pass

def patch_for(field, val):
    if field == "top":
        return {"cookie_file": val}
    if field == "acct":
        return {"accounts": [{"username": "u", "password": "p", "cookie_file": val}]}
    return {"accounts": [{"username": "u0", "password": "p", "cookie_file": VALUES["good"]},      # acct1: 2nd element
                         {"username": "u1", "password": "p", "cookie_file": val}]}

def send(client, route, patch):
    site = lambda name: dict({"name": name, "login_url": "https://example.test/"}, **patch)
    if route == "PUT":
        return client.put("/api/sites/c2", json=patch)
    if route == "CREATE":
        return client.post("/api/sites", json=site("n1"))
    if route == "SIMPORT":
        return client.post("/api/sites/import", json=site("n1"))
    if route == "SIMPORT-ENVELOPE":
        return client.post("/api/sites/import", json={"config": site("n1")})
    if route == "CIMPORT":              # merge onto the existing site c2
        return client.post("/api/config/import", json={"mode": "merge", "sites": [site("c2")]})
    if route == "CIMPORT-NEW":          # merge, a name no site has: a new site; a clean site travels with it
        return client.post("/api/config/import", json={"mode": "merge", "sites": [
            {"name": "other", "login_url": "https://example.test/"}, site("n1")]})
    if route == "CIMPORT-REPLACE":
        return client.post("/api/config/import?mode=replace", json={"sites": [site("n1")]})
    if route == "CIMPORT-FILE":
        body = json.dumps({"sites": [site("n1")]}).encode()
        return client.post("/api/config/import", data={"file": (io.BytesIO(body), "c.json")},
                           content_type="multipart/form-data")
    raise SystemExit("route " + route)

def holds(node, val):              # is val the cookie_file of any site or account in the saved file?
    if isinstance(node, dict):
        return any((k == "cookie_file" and type(v) is type(val) and v == val) or holds(v, val) for k, v in node.items())
    if isinstance(node, list):
        return any(holds(v, val) for v in node)
    return False

if mode == "cells":
    if arg.get("spy"):               # every route must consult THE validator: make it say no and listen
        A._validate_cookie_files = lambda cfg: (False, "CFR-SPY-REFUSAL")
    client = A.app.test_client(); out["rows"] = []
    for vname in arg["values"]:
        val = VALUES[vname]; patch = patch_for(arg["field"], val)
        for old in list(S.runners.values()):      # a real runner of the previous cell: stop its threads first
            for name in ("retire_scheduler", "retire_auto_retry", "retire_workers"):
                if hasattr(old, name): getattr(old, name)(timeout=2.0)
        S.s_cfg.clear(); S.s_meta.clear(); S.runners.clear()
        for sid in ("keep", "c2"):                # replace tears the old sites down for real: they need real runners
            c = clean(sid); S.s_cfg[sid] = c; S.s_meta[sid] = A._build_meta(c)
            S.runners[sid] = A.SiteRunner(sid, c) if arg["route"] == "CIMPORT-REPLACE" else FakeRunner(sid)
        A._save_sites_config()
        before_file = A.SITES_FILE.read_bytes(); before_cfg = copy.deepcopy(dict(S.s_cfg))
        os.chmod(J(home, "locked"), 0)
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                r = send(client, arg["route"], patch)
        finally:
            os.chmod(J(home, "locked"), 0o700)
        body = r.get_json(silent=True) or {}
        text = " ".join([str(body.get("error", ""))] + [str(e) for e in (body.get("errors") or [])]) if isinstance(body, dict) else ""
        after = json.loads(A.SITES_FILE.read_text(encoding="utf-8"))
        out["rows"].append({
            "value": vname, "status": r.status_code, "error": text[:300],
            "stored": holds(after, val),
            "file_changed": A.SITES_FILE.read_bytes() != before_file,
            "sites": sorted(S.s_cfg), "runners": sorted(S.runners),
            "memory_changed": {k: S.s_cfg.get(k) for k in before_cfg} != before_cfg,
            "runner_updates": sum(getattr(x, "updates", 0) for x in S.runners.values()),
        })

elif mode == "boot":                 # a restart over a file that already holds the rows in arg["seed"]
    data = {sid: clean(sid) for sid in arg["sites"]}
    for sid, (field, vname) in arg.get("seed", {}).items():
        data[sid].update(patch_for(field, VALUES[vname]))
    A.SITES_FILE.write_text(json.dumps(data), encoding="utf-8")
    for name in ("_start_session_keepers", "_start_watch_folder_threads", "_start_watcher", "_start_background_services"):
        if hasattr(A, name): setattr(A, name, lambda *a, **k: None)
    A._init_vpn_runtime = lambda: {"ok": True}
    err = io.StringIO(); out["raised"] = ""
    try:
        with contextlib.redirect_stderr(err):
            A._activate_configured_runtime_once(str(A.SITES_FILE))
    except Exception as e:
        out["raised"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    out["sites"] = sorted(S.s_cfg); out["runners"] = sorted(S.runners)
    out["real_runners"] = all(type(x).__name__ == "SiteRunner" for x in S.runners.values())
    out["skipped"] = sorted(s["id"] for s in ((A._SITES_CONFIG_REACHABILITY or {}).get("skipped_sites") or []))
    from bulk_downloader import app_health as H
    payload = {"ok": True}; H._attach_sites_config_health(payload)
    out["health_ok"] = payload.get("ok"); out["flagged"] = (payload.get("sites_config") or {}).get("cookie_file_outside_roots")
    out["health_shows_path"] = any(isinstance(v, str) and v in json.dumps(payload) for v in ("/etc/hostname", J(home, "link.json")))
    out["warnings"] = [ln for ln in err.getvalue().splitlines() if "hold a cookie_file" in ln]
    out["saved"] = A._save_sites_config()
    out["file_after"] = sorted(json.loads(A.SITES_FILE.read_text(encoding="utf-8")))

elif mode == "gate":                 # r2: the writer itself is the choke point
    OUT = "/etc/hostname"; case = arg["case"]
    saved_file = lambda: json.loads(A.SITES_FILE.read_text(encoding="utf-8"))
    for sid in ("keep", "c2"):
        c = clean(sid); S.s_cfg[sid] = c; S.s_meta[sid] = A._build_meta(c); S.runners[sid] = FakeRunner(sid)
    A._save_sites_config()
    stored_c2 = dict(saved_file()["c2"])
    PLANT = {"top": ("cookie_file", OUT), "dl": ("download_dir", "/etc"), "spill": ("spillover_dirs", J(home, "sp") + "\n/etc\n")}
    def plant(where):
        if where == "acct": S.s_cfg["c2"]["accounts"] = [{"username": "u", "password": "p", "cookie_file": OUT}]
        else: S.s_cfg["c2"][PLANT[where][0]] = PLANT[where][1]
    def stored_bad(where):
        f = saved_file()
        return holds(f, OUT) if where in ("top", "acct") else f["c2"].get(PLANT[where][0]) == PLANT[where][1]
    def measure(where, before):
        acc = S.s_cfg["c2"].get("accounts")
        field = "cookie_file" if where in ("top", "acct") else PLANT[where][0]
        return {"file_changed": A.SITES_FILE.read_bytes() != before, "stored_bad": stored_bad(where),
                "live": (acc[0].get("cookie_file") if isinstance(acc, list) and acc else None) if where == "acct" else S.s_cfg["c2"].get(field),
                "want_live": "" if where == "acct" else stored_c2.get(field)}
    client = A.app.test_client(); err = io.StringIO()
    with contextlib.redirect_stderr(err):
        if case == "writer":                            # no request: a runner / scheduler thread's save
            w = arg["where"]; plant(w); before = A.SITES_FILE.read_bytes()
            out["first"] = A._save_sites_config(); out.update(measure(w, before))
            S.s_cfg["keep"]["name"] = "renamed"; out["second"] = A._save_sites_config()
            out["second_file"] = saved_file()["keep"]["name"]; out["stored_bad_after"] = stored_bad(w)
        elif case == "binding":                         # the callable a calling module's call sites resolve to
            import importlib
            mod = importlib.import_module(arg["module"])
            fn = getattr(mod, "_save_sites_config", None) if arg["form"] == "name" else None
            out["resolved"] = "module" if fn is not None else "app"
            fn = fn or importlib.import_module("bulk_downloader.app")._save_sites_config
            plant("acct"); before = A.SITES_FILE.read_bytes()
            out["first"] = fn(); out.update(measure("acct", before))
        elif case == "request":                         # a route with no path check of its own, value already in memory
            w = arg["where"]; plant(w); before = A.SITES_FILE.read_bytes()
            r = client.post("/api/sites/c2/learned/import", json={"learned": {"download": {"row_selectors": ["a.x"]}}})
            out["status"] = r.status_code; out["error"] = str((r.get_json(silent=True) or {}).get("error", ""))[:200]
            out.update(measure(w, before))
        elif case == "legacy":                          # the file already holds the values: stored before the gate
            for w in ("top", "acct", "dl", "spill"): plant(w)
            A.SITES_FILE.write_text(json.dumps({k: dict(v) for k, v in S.s_cfg.items()}), encoding="utf-8")
            S.s_cfg["keep"]["name"] = "renamed"; out["first"] = A._save_sites_config()
            out["second_file"] = saved_file()["keep"]["name"]
            out["kept"] = [w for w in ("top", "dl", "spill") if stored_bad(w)]
        elif case == "cross-cell":                      # r3 (lens R80-B F2): one stored cell's value never holds another kind
            how = arg["how"]; plant("top" if how == "site-to-acct" else "acct")
            A.SITES_FILE.write_text(json.dumps({k: dict(v) for k, v in S.s_cfg.items()}), encoding="utf-8")
            before = A.SITES_FILE.read_bytes(); c = S.s_cfg["c2"]
            if how == "site-to-acct": c["accounts"] = [{"username": "u", "password": "p", "cookie_file": OUT}]
            elif how == "acct-to-site": c["cookie_file"] = OUT
            else: c["accounts"] = [{"username": "u0", "password": "p", "cookie_file": VALUES["good"]}] + c["accounts"]  # acct-reorder
            out["first"] = A._save_sites_config(); out["file_changed"] = A.SITES_FILE.read_bytes() != before
            acc = c.get("accounts") or []
            out["live_site"] = c.get("cookie_file"); out["live_accts"] = [a.get("cookie_file") for a in acc]
            out["file_accts"] = [a.get("cookie_file") for a in (saved_file()["c2"].get("accounts") or [])]
            out["want_site"] = stored_c2.get("cookie_file"); out["out"] = OUT; out["good"] = VALUES["good"]
        elif case == "default-outside-roots":           # the writer's own <BD_HOME>/cookies/<sid>.json is never refused
            A._app_cfg["path_allowlist"] = [J(home, "elsewhere")]
            c = clean("n9"); c["cookie_file"] = ""; c["download_dir"] = ""; S.s_cfg["n9"] = c
            out["first"] = A._save_sites_config(); out["n9"] = saved_file().get("n9", {}).get("cookie_file")
            out["want"] = J(os.path.realpath(home), "cookies", "n9.json")
        elif case == "cimport-accounts":                # lens F3: a wrongly typed accounts on config import
            before = A.SITES_FILE.read_bytes(); out["rows"] = []
            for accts in ({"a": 1}, [7], "x", [{"username": "u", "password": "p"}]):
                r = client.post("/api/config/import", json={"mode": "merge", "sites": [
                    {"name": "c2", "login_url": "https://example.test/", "accounts": accts}]})
                out["rows"].append({"accounts": json.dumps(accts), "status": r.status_code,
                                    "error": str((r.get_json(silent=True) or {}).get("error", ""))[:120],
                                    "file_changed": A.SITES_FILE.read_bytes() != before})
        elif case.startswith("tpl"):
            from bulk_downloader import templates as T
            carried = {"top": {"cookie_file": OUT}, "acct": {"accounts": [{"username": "u", "password": "p", "cookie_file": OUT}]},
                       "goodpath": {"cookie_file": VALUES["good"]}, "plain": {"trigger_selector": "a.cfr-trigger"}}[arg["carry"]]
            body = {"name": "cfr tpl", "description": "d", "patterns": ["cfr-probe\\.test"],
                    "learned": {"download": {"row_selectors": ["div.r a.dl"], "url_attribute": "href"}}}
            url = "/api/sites/c2/teach_save_template" if arg.get("via") == "teach" else "/api/user_templates"
            r1 = client.post(url, json=dict(body, config_defaults=carried)); b1 = r1.get_json(silent=True) or {}
            out["tpl_status"] = r1.status_code; out["tpl_error"] = str(b1.get("error", ""))[:200]
            if arg.get("any_source"):                    # a template SOURCE that hands the keys to the merge anyway
                b1 = client.post(url, json=body).get_json(silent=True) or {}
                real_get = T.get
                def get_with_keys(tid, *a, **k):
                    t = real_get(tid, *a, **k)
                    return dict(t, config_defaults=dict(t.get("config_defaults") or {}, **carried)) if isinstance(t, dict) else t
                T.get = get_with_keys
            tid = (b1.get("template") or {}).get("id"); out["tid"] = bool(tid)
            before = A.SITES_FILE.read_bytes(); r2 = None
            if arg.get("then") == "create":             # R58-B's cell: auto-pick on a create whose URL matches
                r2 = client.post("/api/sites", json={"name": "n1", "login_url": "https://cfr-probe.test/", "start_url": "https://cfr-probe.test/"})
            elif arg.get("then") == "apply" and tid:
                S.s_cfg["c2"]["accounts"] = ""
                r2 = client.post("/api/sites/c2/templates/apply", json={"template_id": tid})
            if r2 is not None:
                b2 = r2.get_json(silent=True) or {}
                out["status"] = r2.status_code; out["body"] = json.dumps(b2)[:300]
                f = saved_file(); new = [k for k in f if k not in ("keep", "c2")]
                out["new_site"] = {k: f[new[0]].get(k) for k in ("cookie_file", "trigger_selector", "applied_template")} if new else None
                out["applied"] = bool((b2.get("auto_pick") or {}).get("download_template_applied")) or b2.get("ok") is True
                out["c2_accounts"] = f["c2"].get("accounts")
            out["stored_out"] = holds(saved_file(), OUT); out["stored_good"] = holds(saved_file(), VALUES["good"])
    out["refusal_logged"] = sum("sites_config not saved" in ln for ln in err.getvalue().splitlines())

elif mode == "gapfill":              # r2: the load-time template gap-fill (rows already in the file)
    OUT = "/etc/hostname"
    from bulk_downloader import templates as T
    client = A.app.test_client()
    b1 = client.post("/api/user_templates", json={
        "name": "cfr gap", "description": "d", "patterns": ["cfr-gap\\.test"],
        "learned": {"download": {"row_selectors": ["div.r a.dl"], "url_attribute": "href"}}}).get_json(silent=True) or {}
    tid = (b1.get("template") or {}).get("id"); out["tid"] = bool(tid)
    carried = {"cookie_file": OUT, "accounts": [{"username": "u", "password": "p", "cookie_file": OUT}], "trigger_selector": "a.cfr-gap"}
    real_get = T.get
    def get_with_keys(t_id, *a, **k):
        t = real_get(t_id, *a, **k)
        return dict(t, config_defaults=dict(t.get("config_defaults") or {}, **carried)) if isinstance(t, dict) and t_id == tid else t
    T.get = get_with_keys
    row = {k: v for k, v in clean("gap").items() if k not in ("cookie_file", "accounts", "trigger_selector")}
    row.update(login_url="https://cfr-gap.test/", start_url="https://cfr-gap.test/")
    A.SITES_FILE.write_text(json.dumps({"keep": clean("keep"), "gap": row}), encoding="utf-8")
    err = io.StringIO(); out["raised"] = ""
    try:
        with contextlib.redirect_stderr(err):
            A._load_sites_config()
    except Exception as e:
        out["raised"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    live = S.s_cfg.get("gap") or {}
    out["sites"] = sorted(S.s_cfg); out["applied"] = live.get("applied_template") == tid and bool(tid)
    out["trigger_selector"] = live.get("trigger_selector")
    out["live_holds_out"] = holds({"gap": live}, OUT)
    out["saved"] = A._save_sites_config(); out["stored_out"] = holds(json.loads(A.SITES_FILE.read_text(encoding="utf-8")), OUT)

elif mode == "publish":              # r3 (lens R26-C F1): a route that publishes a NEW site judges its paths first
    cell = arg["cell"]; client = A.app.test_client(); err = io.StringIO()
    NARROW = J(home, "narrow"); INSIDE = J(home, "narrow", "dl"); LEGACY = J(home, "legacy-dl")
    for d in (INSIDE, LEGACY):
        os.makedirs(d, exist_ok=True)
    saved_file = lambda: json.loads(A.SITES_FILE.read_text(encoding="utf-8"))
    def post(url, body):
        r = client.post(url, json=body); b = r.get_json(silent=True)
        return r.status_code, (str(b.get("error", "")) if isinstance(b, dict) else "")[:300], (b if isinstance(b, dict) else {})
    def create(name, **paths):           # through the UI route, under whatever allowlist is live
        st, _e, b = post("/api/sites", dict({"name": name, "login_url": "https://example.test/"}, **paths))
        return b.get("id") if st == 200 else None
    def roots(lst):
        return post("/api/global_config", {"path_allowlist": lst})[0] == 200 and A._app_cfg.get("path_allowlist") == lst
    with contextlib.redirect_stderr(err):
        S.s_cfg.clear(); S.s_meta.clear(); S.runners.clear()
        armed = roots([home])
        if cell in ("clone-ui", "clone-spill", "clone-control"):
            src = create("src", **({"spillover_dirs": LEGACY} if cell == "clone-spill"
                                   else {"download_dir": INSIDE if cell == "clone-control" else LEGACY}))
            armed = armed and bool(src) and roots([NARROW])
        elif cell == "clone-legacy-dd":      # a row stored before any route judged it (hand edit): straight into file + memory
            armed = armed and roots([NARROW]); src = "src"
            c = clean("src"); c["download_dir"] = "/etc"
            S.s_cfg[src] = c; S.s_meta[src] = A._build_meta(c); S.runners[src] = FakeRunner(src)
            A.SITES_FILE.write_text(json.dumps({src: c}), encoding="utf-8")
        elif cell == "clone-legacy-spill-list":  # r4 (lens R114-C X1): a stored falsy non-string spillover_dirs
            src = "src"; c = clean("src"); c["spillover_dirs"] = []
            S.s_cfg[src] = c; S.s_meta[src] = A._build_meta(c); S.runners[src] = FakeRunner(src)
            A.SITES_FILE.write_text(json.dumps({src: c}), encoding="utf-8")
        elif cell.startswith("x1-"):         # r4 (lens R114-C X1): one live site c2, roots unchanged
            c2 = create("c2", download_dir=INSIDE); armed = armed and bool(c2)
        else:                                # config import cells: one live site c2
            c2_dd = LEGACY if cell == "cimport-control-legacy-unchanged" else INSIDE
            c2 = create("c2", download_dir=c2_dd)
            armed = armed and bool(c2) and roots([NARROW])
        before_file = A.SITES_FILE.read_bytes(); before_cfg = copy.deepcopy(dict(S.s_cfg))
        before_sids = sorted(S.s_cfg); before_runners = sorted(S.runners)
        site = lambda name, **kw: dict({"name": name, "login_url": "https://example.test/"}, **kw)
        if cell.startswith("clone"):
            st, e, _b = post("/api/sites/%s/clone" % src, {})
        elif cell.startswith("x1-"):         # x1-<create|put>-<field>-<value>
            _x, how, field, val = cell.split("-")
            v = {"list": [], "zero": 0, "false": False, "empty": "", "null": None}[val]
            if how == "create":
                st, e, _b = post("/api/sites", site("n1", **{"download_dir": INSIDE, field.replace("spill", "spillover_dirs").replace("dd", "download_dir"): v}))
            else:
                r = client.put("/api/sites/" + c2, json={"name": "renamed", field.replace("spill", "spillover_dirs").replace("dd", "download_dir"): v})
                b = r.get_json(silent=True); st = r.status_code; e = (str(b.get("error", "")) if isinstance(b, dict) else "")[:300]
        else:
            body, url = {
                "cimport-new-dd": ({"mode": "merge", "sites": [site("fresh", download_dir=LEGACY)]}, None),
                "cimport-new-spill": ({"mode": "merge", "sites": [site("fresh", spillover_dirs=LEGACY)]}, None),
                "cimport-replace-dd": ({"sites": [site("fresh", download_dir=LEGACY)]}, "?mode=replace"),
                "cimport-merge-changed-dd": ({"mode": "merge", "sites": [site("c2", download_dir=LEGACY)]}, None),
                "cimport-control-new-inside": ({"mode": "merge", "sites": [site("fresh", download_dir=INSIDE)]}, None),
                "cimport-control-legacy-unchanged": ({"mode": "merge", "sites": [
                    site("c2", download_dir=LEGACY, login_url="https://example.test/changed")]}, None),
            }[cell]
            st, e, _b = post("/api/config/import" + (url or ""), body)
        new = lambda d: sorted(set(d) - set(before_sids))
        out.update(armed=armed, status=st, error=e, file_changed=A.SITES_FILE.read_bytes() != before_file,
                   new_live=new(S.s_cfg), new_meta=new(S.s_meta), new_runners=new(S.runners),
                   lost_live=sorted(set(before_sids) - set(S.s_cfg)), lost_runners=sorted(set(before_runners) - set(S.runners)),
                   live_changed=sorted(s for s in before_cfg if S.s_cfg.get(s) != before_cfg[s]))
        out["next_save"] = A._save_sites_config()            # the next unrelated save (a runner / scheduler thread's)
        f = saved_file()
        out.update(file_sids=sorted(f), file_new=new(f),
                   file_new_dd=[f[s].get("download_dir") for s in new(f)],
                   file_new_spill=[f[s].get("spillover_dirs") for s in new(f)],
                   c2_file=(f.get(c2) or {}) if cell.startswith(("cimport", "x1-")) else {},
                   c2_live_name=(S.s_cfg.get(c2) or {}).get("name") if cell.startswith("x1-") else None,
                   want=dict(INSIDE=INSIDE, LEGACY=LEGACY))
        for r in list(S.runners.values()):
            for nm in ("retire_scheduler", "retire_auto_retry", "retire_workers"):
                if hasattr(r, nm):
                    try: getattr(r, nm)(timeout=2.0)
                    except Exception: pass

elif mode == "boot-account":         # r4 (lens R113-D F1): boot copies the active account's stored cookie_file into the site cell
    import time
    CD = J(home, "c"); NARROW = J(home, "narrow")
    for d in (CD, NARROW): os.makedirs(d, exist_ok=True)
    A0, A1 = J(CD, "a0.json"), J(CD, "a1.json")
    for pth in (A0, A1):
        with open(pth, "w") as fh: fh.write("[]")
    acct = lambda lbl, cf, cool: {"label": lbl, "username": "u" + lbl, "password": "", "cookie_file": cf, "cooldown_until": cool}
    r1 = clean("r1"); r1.update(cookie_file=A1, accounts=[acct("0", A0, time.time() - 10), acct("1", A1, 0)])
    if arg.get("new_site_path"): r1["cookie_file"] = J(CD, "never-stored.json")   # control: a value no stored cell holds
    o1 = clean("o1")
    A.SITES_FILE.write_text(json.dumps({"r1": r1, "o1": o1}), encoding="utf-8")
    A._app_cfg["path_allowlist"] = [NARROW]
    for name in ("_start_session_keepers", "_start_watch_folder_threads", "_start_watcher", "_start_background_services"):
        if hasattr(A, name): setattr(A, name, lambda *a, **k: None)
    A._init_vpn_runtime = lambda: {"ok": True}
    err = io.StringIO(); client = A.app.test_client()
    saved_file = lambda: json.loads(A.SITES_FILE.read_text(encoding="utf-8"))
    with contextlib.redirect_stderr(err):
        A._activate_configured_runtime_once(str(A.SITES_FILE))
        if arg.get("new_site_path"): S.s_cfg["r1"]["cookie_file"] = J(CD, "new-outside.json")
        out["live_site_after_boot"] = S.s_cfg["r1"].get("cookie_file")
        r = client.put("/api/sites/o1", json={"name": "other2"})
        out["status"] = r.status_code; out["error"] = str((r.get_json(silent=True) or {}).get("error", ""))[:200]
        f = saved_file()
        out.update(file_other=f["o1"].get("name"), file_site=f["r1"].get("cookie_file"),
                   live_site=S.s_cfg["r1"].get("cookie_file"), A0=A0, A1=A1)
    for x in list(S.runners.values()):
        for nm in ("retire_scheduler", "retire_auto_retry", "retire_workers"):
            if hasattr(x, nm):
                try: getattr(x, nm)(timeout=2.0)
                except Exception: pass

print("CFR-RESULT " + json.dumps(out), flush=True)
os._exit(0)
"""

ROUTES = (
    "PUT",
    "CREATE",
    "SIMPORT",
    "SIMPORT-ENVELOPE",
    "CIMPORT",
    "CIMPORT-NEW",
    "CIMPORT-REPLACE",
    "CIMPORT-FILE",
)
FIELDS = ("top", "acct", "acct1")
# every value a write route must refuse; "fifo" has its own test (one cell at a time, own process group)
BAD = (
    "outside-abs",
    "dotdot",
    "relative",
    "symlink-file",
    "symlink-dir",
    "long",
    "unreadable",
    "nul",
    "directory",
    "not-string",
    "pad-trailing",
    "pad-leading",
)
CONTROLS = ("absent", "good")
# the name the refusal must carry: which field of which element was judged
LABEL = {
    "top": "cookie_file",
    "acct": "accounts[0].cookie_file",
    "acct1": "accounts[1].cookie_file",
}
# what a route that stored nothing leaves behind
UNTOUCHED = {
    "sites": ["c2", "keep"],
    "runners": ["c2", "keep"],
    "file_changed": False,
    "memory_changed": False,
    "runner_updates": 0,
}


def _child(tmp_path: Path, mode: str, arg: dict, timeout: int = 120) -> dict:
    """One fresh interpreter in its own process group; the whole group is killed afterwards, whatever happened."""
    script = tmp_path / "cfr_child.py"
    script.write_text(CHILD, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-B", str(script), mode, str(home), json.dumps(arg)],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        stdout, stderr = "", ""
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                break
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                continue
        if timed_out:
            proc.communicate()
    if timed_out:
        return {"timed_out": True}
    lines = [ln for ln in stdout.splitlines() if ln.startswith("CFR-RESULT ")]
    assert lines, (
        f"CFR-CHILD-NO-RESULT mode={mode} arg={arg} rc={proc.returncode}: {stdout[-400:]} {stderr[-800:]}"
    )
    return json.loads(lines[-1][len("CFR-RESULT ") :])


def _state(row: dict) -> dict:
    return {k: row[k] for k in UNTOUCHED}


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("route", ROUTES)
def test_bad_cookie_file_is_refused_400_and_nothing_is_stored(tmp_path, route, field):
    got = _child(
        tmp_path, "cells", {"route": route, "field": field, "values": list(BAD)}
    )
    assert got["euid"] != 0 and got["roots"] > 0, (
        f"CFR-FIXTURE-NOT-ARMED euid={got['euid']} roots={got['roots']}"
    )
    assert [r["value"] for r in got["rows"]] == list(BAD)
    wrong = []
    for row in got["rows"]:
        cell = f"{route} {field} {row['value']}"
        if row["status"] != 400 or row["stored"]:
            wrong.append(
                f"CFR-BAD-COOKIE-FILE-STORED {cell}: status={row['status']} stored={row['stored']}"
            )
        elif _state(row) != UNTOUCHED:
            wrong.append(f"CFR-REFUSED-BUT-STATE-CHANGED {cell}: {_state(row)}")
        elif LABEL[field] not in row["error"]:
            wrong.append(
                f"CFR-REFUSAL-DOES-NOT-NAME-THE-FIELD {cell}: want {LABEL[field]!r} in {row['error']!r}"
            )
    assert not wrong, f"{len(wrong)} of {len(BAD)} cells:\n" + "\n".join(wrong)


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("route", ROUTES)
def test_control_good_cookie_file_is_still_stored_200(tmp_path, route, field):
    """Positive control beside every refusal: the same request with a path inside the roots is accepted and written."""
    got = _child(
        tmp_path, "cells", {"route": route, "field": field, "values": list(CONTROLS)}
    )
    wrong = [
        f"CFR-CONTROL-WRITE-REFUSED {route} {field} {row['value']}: status={row['status']} stored={row['stored']} "
        f"error={row['error']!r}"
        for row in got["rows"]
        if row["status"] != 200 or not row["stored"] or not row["file_changed"]
    ]
    assert len(got["rows"]) == len(CONTROLS) and not wrong, "\n".join(wrong)


@pytest.mark.parametrize("route", ROUTES)
def test_every_route_asks_the_one_shared_validator(tmp_path, route):
    """The validator is replaced by one that refuses everything: a route that does not ask IT stores the good path."""
    got = _child(
        tmp_path,
        "cells",
        {"route": route, "field": "acct", "values": ["good"], "spy": True},
    )
    row = got["rows"][0]
    assert (
        row["status"] == 400
        and "CFR-SPY-REFUSAL" in row["error"]
        and _state(row) == UNTOUCHED
    ), (
        f"CFR-ROUTE-DOES-NOT-ASK-THE-SHARED-VALIDATOR {route}: status={row['status']} error={row['error']!r} "
        f"{_state(row)}"
    )


@pytest.mark.parametrize(
    "route,field",
    [("CREATE", "top"), ("SIMPORT", "acct"), ("CIMPORT-NEW", "top"), ("PUT", "acct")],
)
def test_fifo_cookie_file_is_refused_before_anything_opens_it(tmp_path, route, field):
    """open() of a FIFO never returns.  One cell per process group, 30 s, group killed afterwards.
    BD_CFR_FIFO_LOCK names a lock file when several pytest runs share a host (mutant batteries): the cell then
    runs one at a time across all of them."""
    with open(os.environ.get("BD_CFR_FIFO_LOCK") or os.devnull, "w") as lock:
        if lock.name != os.devnull:
            fcntl.flock(lock, fcntl.LOCK_EX)
        got = _child(
            tmp_path,
            "cells",
            {"route": route, "field": field, "values": ["fifo"]},
            timeout=30,
        )
    assert not got.get("timed_out"), (
        f"CFR-FIFO-COOKIE-FILE-BLOCKED-THE-REQUEST {route} {field}: no answer in 30 s"
    )
    row = got["rows"][0]
    assert (
        row["status"] == 400
        and "must be a regular file" in row["error"]
        and not row["stored"]
    ), (
        f"CFR-FIFO-COOKIE-FILE-ACCEPTED {route} {field}: status={row['status']} stored={row['stored']} "
        f"error={row['error']!r}"
    )
    assert _state(row) == UNTOUCHED, (
        f"CFR-REFUSED-BUT-STATE-CHANGED {route} {field} fifo: {_state(row)}"
    )


def test_boot_over_a_pre_seeded_bad_row_loads_every_other_site(tmp_path):
    """A row stored before this cut: boot keeps s28's rule -- the site that cannot load is skipped, listed and kept."""
    got = _child(
        tmp_path,
        "boot",
        {"sites": ["keep", "badrow", "c2"], "seed": {"badrow": ["acct", "long"]}},
    )
    assert (
        got["raised"] == ""
        and got["sites"] == ["c2", "keep"]
        and got["runners"] == ["c2", "keep"]
    ), (
        f"CFR-BOOT-BAD-ROW-TOOK-OTHER-SITES-DOWN raised={got['raised']!r} sites={got['sites']} "
        f"runners={got['runners']}"
    )
    assert got["real_runners"] and got["skipped"] == ["badrow"], (
        f"CFR-BOOT-SKIP-NOT-LISTED {got['skipped']}"
    )
    assert got["saved"] is True and got["file_after"] == ["badrow", "c2", "keep"], (
        f"CFR-BOOT-SKIPPED-ROW-LOST-ON-SAVE saved={got['saved']} file={got['file_after']}"
    )
    assert got["flagged"] == ["badrow"], (
        f"CFR-BOOT-STORED-BAD-COOKIE-FILE-NOT-LISTED {got['flagged']}"
    )


def test_boot_lists_stored_rows_the_write_routes_would_refuse_and_still_loads_them(
    tmp_path,
):
    """PM 22:10Z: rows stored before this cut keep loading (no skip) but are named in /api/health and logged once."""
    seed = {
        "acctroots": ["acct", "outside-abs"],
        "toplink": ["top", "symlink-file"],
        "second": ["acct1", "dotdot"],
    }
    sites = ["keep", "acctroots", "toplink", "second", "c2"]
    got = _child(tmp_path, "boot", {"sites": sites, "seed": seed})
    assert (
        got["raised"] == "" and got["sites"] == sorted(sites) and got["skipped"] == []
    ), (
        f"CFR-BOOT-FLAGGED-SITE-NOT-LOADED raised={got['raised']!r} sites={got['sites']} skipped={got['skipped']}"
    )
    assert got["flagged"] == ["acctroots", "toplink", "second"], (
        f"CFR-BOOT-STORED-BAD-COOKIE-FILE-NOT-LISTED health cookie_file_outside_roots={got['flagged']}"
    )
    assert len(got["warnings"]) == 1 and all(
        sid in got["warnings"][0] for sid in seed
    ), f"CFR-BOOT-FLAG-NOT-LOGGED-ONCE {got['warnings']}"
    assert (
        got["health_ok"] is True
        and not got["health_shows_path"]
        and "/etc/hostname" not in got["warnings"][0]
    ), (
        f"CFR-BOOT-FLAG-SHOWS-THE-PATH ok={got['health_ok']} shows={got['health_shows_path']} {got['warnings']}"
    )


def test_boot_control_every_good_row_loads(tmp_path):
    got = _child(
        tmp_path,
        "boot",
        {"sites": ["keep", "badrow", "c2"], "seed": {"badrow": ["acct", "good"]}},
    )
    assert (
        got["raised"] == ""
        and got["sites"] == ["badrow", "c2", "keep"]
        and got["skipped"] == []
    ), (
        f"CFR-BOOT-CONTROL-GOOD-ROW-NOT-LOADED raised={got['raised']!r} sites={got['sites']} "
        f"skipped={got['skipped']}"
    )
    assert got["flagged"] is None and got["warnings"] == [], (
        f"CFR-BOOT-CONTROL-GOOD-ROW-FLAGGED {got['flagged']} {got['warnings']}"
    )


# ------------------------------------------------------------------ r2: the writer is the choke point (PM 23:56Z)
OUT = "/etc/hostname"


def _writer_callers() -> list[tuple[str, str, str, int]]:
    """Census from the filesystem: every call of the sites-config writer in bulk_downloader/ ->
    (module, enclosing function, call form, line).  Forms: "name" = _save_sites_config(), "attr" = <x>._save_sites_config()."""
    import ast

    rows = []
    for path in sorted((REPO / "bulk_downloader").rglob("*.py")):
        if "tests" in path.relative_to(REPO).parts:
            continue
        src = path.read_text(encoding="utf-8")
        if "_save_sites_config" not in src:
            continue
        tree = ast.parse(src)
        funcs = [
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        module = ".".join(path.relative_to(REPO).with_suffix("").parts)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            form = (
                "name"
                if isinstance(node.func, ast.Name)
                and node.func.id == "_save_sites_config"
                else "attr"
                if isinstance(node.func, ast.Attribute)
                and node.func.attr == "_save_sites_config"
                else ""
            )
            if not form:
                continue
            inside = [f for f in funcs if f.lineno <= node.lineno <= f.end_lineno]
            name = max(inside, key=lambda f: f.lineno).name if inside else "<module>"
            if (
                name == "_save_sites_config"
            ):  # a delegate's own forwarding call, not a caller
                continue
            rows.append((module, name, form, node.lineno))
    return rows


CALLERS = _writer_callers()
CALLER_FUNCS = sorted({(m, f, form) for m, f, form, _line in CALLERS})
_BINDING_CACHE: dict = {}


def test_census_of_writer_callers_is_not_empty_and_classified():
    """The denominator of the per-caller rows, derived here from the files (not from a list in this test)."""
    modules = sorted({m for m, _f, _form, _l in CALLERS})
    assert len(CALLERS) >= 30 and len(modules) >= 10, (
        f"CFR-CENSUS-TOO-SMALL calls={len(CALLERS)} modules={len(modules)}"
    )
    print(
        f"CFR-CENSUS calls={len(CALLERS)} functions={len(CALLER_FUNCS)} modules={len(modules)}: {' '.join(modules)}"
    )


@pytest.mark.parametrize(
    "module,func,form",
    CALLER_FUNCS,
    ids=[f"{m.split('.')[-1]}:{f}" for m, f, _ in CALLER_FUNCS],
)
def test_writer_refuses_for_every_caller(tmp_path_factory, module, func, form):
    """One row per calling function.  The callable that function's call resolves to (its module's binding, or app's for
    a function-local import / attribute call) is handed a live entry holding an outside-roots account cookie_file."""
    key = (module, form)
    if key not in _BINDING_CACHE:
        _BINDING_CACHE[key] = _child(
            tmp_path_factory.mktemp("bind"),
            "gate",
            {"case": "binding", "module": module, "form": form},
        )
    got = _BINDING_CACHE[key]
    assert (
        got["first"] is False and not got["stored_bad"] and not got["file_changed"]
    ), (
        f"CFR-WRITER-STORED-BAD-PATH caller={module}:{func} ({got['resolved']} binding): "
        f"returned={got['first']} stored={got['stored_bad']} file_changed={got['file_changed']}"
    )
    assert got["live"] == "" and got["refusal_logged"] == 1, (
        f"CFR-WRITER-LEFT-BAD-VALUE-LIVE caller={module}:{func}: accounts[0].cookie_file={got['live']!r} "
        f"logged={got['refusal_logged']}"
    )


WHERE = [
    "top",
    "acct",
    "dl",
    "spill",
]  # site cookie_file, accounts[0].cookie_file, download_dir, spillover_dirs


@pytest.mark.parametrize("where", WHERE)
def test_writer_refusal_outside_a_request_returns_false_and_does_not_block_the_next_save(
    tmp_path, where
):
    got = _child(tmp_path, "gate", {"case": "writer", "where": where})
    assert (
        got["first"] is False and not got["stored_bad"] and not got["file_changed"]
    ), (
        f"CFR-WRITER-STORED-BAD-PATH {where}: returned={got['first']} stored={got['stored_bad']}"
    )
    assert got["live"] == got["want_live"], (
        f"CFR-WRITER-LEFT-BAD-VALUE-LIVE {where}: live={got['live']!r} want={got['want_live']!r}"
    )
    assert (
        got["second"] is True
        and got["second_file"] == "renamed"
        and not got["stored_bad_after"]
    ), (
        f"CFR-WRITER-REFUSAL-BLOCKS-LATER-SAVES {where}: second={got['second']} name={got['second_file']!r} "
        f"stored={got['stored_bad_after']}"
    )


@pytest.mark.parametrize("where", WHERE)
def test_writer_refusal_inside_a_request_is_a_400(tmp_path, where):
    """A route with no path check of its own (learned import) saving an entry that holds the value."""
    got = _child(tmp_path, "gate", {"case": "request", "where": where})
    assert got["status"] == 400 and "sites_config not saved" in got["error"], (
        f"CFR-WRITER-REFUSAL-NOT-A-400 {where}: status={got['status']} error={got['error']!r}"
    )
    assert not got["stored_bad"] and not got["file_changed"], (
        f"CFR-WRITER-STORED-BAD-PATH request {where}: stored={got['stored_bad']} changed={got['file_changed']}"
    )


def test_writer_control_values_already_in_the_file_are_not_refused(tmp_path):
    """PM 22:10Z / 00:08Z: unchanged rows stored before the gate keep working -- an unrelated change still saves."""
    got = _child(tmp_path, "gate", {"case": "legacy"})
    assert (
        got["first"] is True
        and got["second_file"] == "renamed"
        and got["kept"] == ["top", "dl", "spill"]
    ), (
        f"CFR-WRITER-REFUSED-A-STORED-ROW saved={got['first']} name={got['second_file']!r} kept={got['kept']}"
    )
    assert got["refusal_logged"] == 0, (
        f"CFR-WRITER-REFUSED-A-STORED-ROW logged={got['refusal_logged']}"
    )


def test_writer_control_its_own_default_path_is_never_refused(tmp_path):
    """Allowlist without BD_HOME: the path the writer fills in itself must still save, or no new site could."""
    got = _child(tmp_path, "gate", {"case": "default-outside-roots"})
    assert got["first"] is True and got["n9"] == got["want"], (
        f"CFR-WRITER-REFUSED-ITS-OWN-DEFAULT saved={got['first']} stored={got['n9']!r} want={got['want']!r}"
    )


@pytest.mark.parametrize("via", ["user_templates", "teach"])
@pytest.mark.parametrize("carry", ["top", "acct", "goodpath"])
def test_template_naming_cookie_file_or_accounts_is_refused_at_entry(
    tmp_path, carry, via
):
    """PM 00:08Z (b): both template save routes answer 400 -- for any value, a path inside the roots included."""
    got = _child(tmp_path, "gate", {"case": "tpl", "carry": carry, "via": via})
    assert (
        got["tpl_status"] == 400
        and "may not carry cookie_file or accounts" in got["tpl_error"]
        and not got["tid"]
    ), (
        f"CFR-TEMPLATE-NAMING-COOKIE-FILE-ACCEPTED {via} {carry}: status={got['tpl_status']} error={got['tpl_error']!r}"
    )


@pytest.mark.parametrize("carry", ["top", "acct"])
def test_template_merge_on_create_never_fills_cookie_file_or_accounts(tmp_path, carry):
    """R58-B F1 at the merge itself: whatever SOURCE hands the keys over (here: templates.get made to), auto-pick on a
    create applies the template and the keys do not reach sites_config.json."""
    got = _child(
        tmp_path,
        "gate",
        {"case": "tpl", "carry": carry, "any_source": True, "then": "create"},
    )
    assert got["tid"] and got["status"] == 200 and got["new_site"], (
        f"CFR-FIXTURE-NOT-ARMED template={got['tid']} create={got.get('status')} {got.get('body')}"
    )
    # stopped at the merge, not later: the writer's gate had nothing to refuse
    assert got["refusal_logged"] == 0, (
        f"CFR-TEMPLATE-MERGE-FILLED-THE-KEY {carry}: writer refusals={got['refusal_logged']}"
    )
    assert not got["stored_out"], (
        f"CFR-TEMPLATE-COOKIE-FILE-STORED-BY-AUTO-PICK {carry}: new site={got['new_site']}"
    )
    assert got["applied"], f"CFR-TEMPLATE-NOT-APPLIED {carry}: {got.get('body')}"


def test_template_apply_route_never_fills_accounts(tmp_path):
    got = _child(
        tmp_path,
        "gate",
        {"case": "tpl", "carry": "acct", "any_source": True, "then": "apply"},
    )
    assert got["tid"] and "status" in got, (
        f"CFR-FIXTURE-NOT-ARMED template={got['tid']} {got.get('body')}"
    )
    assert got["refusal_logged"] == 0 and got["status"] == 200, (
        f"CFR-TEMPLATE-MERGE-FILLED-THE-KEY apply: status={got['status']} writer refusals={got['refusal_logged']} "
        f"{got.get('body')}"
    )
    assert not got["stored_out"] and got["c2_accounts"] in ("", []), (
        f"CFR-TEMPLATE-COOKIE-FILE-STORED-BY-APPLY stored={got['stored_out']} accounts={got['c2_accounts']!r}"
    )


def test_template_control_ordinary_defaults_still_apply(tmp_path):
    got = _child(tmp_path, "gate", {"case": "tpl", "carry": "plain", "then": "create"})
    assert (
        got["tpl_status"] == 200
        and got["status"] == 200
        and (got["new_site"] or {}).get("trigger_selector") == "a.cfr-trigger"
    ), (
        f"CFR-CONTROL-TEMPLATE-REFUSED tpl={got['tpl_status']} {got['tpl_error']!r} create={got.get('status')} "
        f"new site={got['new_site']} {got.get('body')}"
    )


def test_config_import_wrongly_typed_accounts_is_a_400(tmp_path):
    """Lens F3: was a 503 rollback out of the runner constructor."""
    rows = _child(tmp_path, "gate", {"case": "cimport-accounts"})["rows"]
    bad, good = rows[:3], rows[3]
    wrong = [
        r
        for r in bad
        if r["status"] != 400
        or "accounts must be a list of objects" not in r["error"]
        or r["file_changed"]
    ]
    assert not wrong, f"CFR-CONFIG-IMPORT-BAD-ACCOUNTS-NOT-A-400 {wrong}"
    assert good["status"] == 200 and good["file_changed"], (
        f"CFR-CONTROL-WRITE-REFUSED config import accounts {good}"
    )


def test_load_time_template_gap_fill_never_fills_cookie_file_or_accounts(tmp_path):
    """The third merge: at load, a stored row with no selectors takes a matching template's config_defaults for the
    keys the row lacks.  The template source is made to carry cookie_file + accounts beside an ordinary key."""
    got = _child(tmp_path, "gapfill", {})
    assert (
        got["tid"]
        and got["raised"] == ""
        and got["applied"]
        and got["trigger_selector"] == "a.cfr-gap"
    ), (
        f"CFR-FIXTURE-NOT-ARMED template={got['tid']} raised={got['raised']!r} applied={got['applied']} "
        f"trigger_selector={got['trigger_selector']!r} sites={got['sites']}"
    )
    assert not got["live_holds_out"] and not got["stored_out"], (
        f"CFR-TEMPLATE-COOKIE-FILE-FILLED-AT-LOAD live={got['live_holds_out']} stored={got['stored_out']}"
    )


# r3 (lens R26-C F1): the writer's refusal came AFTER the route had published a new site -- clone, config import --
# so the request got 400/503 while the site stayed live, and the next save stored it with download_dir blanked.
PUBLISH_REFUSED = [
    "clone-ui",  # created in the roots through the UI, roots narrowed through the UI, then cloned
    "clone-legacy-dd",  # source row stored before any route judged it
    "clone-spill",
    "cimport-new-dd",
    "cimport-new-spill",
    "cimport-replace-dd",
    "cimport-merge-changed-dd",
]
PUBLISH_CONTROLS = [
    "clone-control",
    "cimport-control-new-inside",
    "cimport-control-legacy-unchanged",
]


@pytest.mark.parametrize("cell", PUBLISH_REFUSED)
def test_route_refusing_a_new_sites_path_publishes_nothing(tmp_path, cell):
    got = _child(tmp_path, "publish", {"cell": cell})
    assert got["armed"], f"CFR-FIXTURE-NOT-ARMED {cell}: {got}"
    assert not (got["new_live"] or got["new_meta"] or got["new_runners"]), (
        f"CFR-REFUSED-SITE-LEFT-LIVE {cell}: s_cfg+={got['new_live']} s_meta+={got['new_meta']} "
        f"runners+={got['new_runners']}"
    )
    assert not (got["lost_live"] or got["lost_runners"] or got["live_changed"]), (
        f"CFR-REFUSED-ROUTE-CHANGED-LIVE-SITES {cell}: lost={got['lost_live']} lost_runners={got['lost_runners']} "
        f"changed={got['live_changed']}"
    )
    assert not got["file_changed"], f"CFR-REFUSED-WRITE-CHANGED-FILE {cell}"
    assert got["status"] == 400, (
        f"CFR-PUBLISH-REFUSAL-NOT-A-400 {cell}: status={got['status']} error={got['error']!r}"
    )
    assert got["next_save"] is True and not got["file_new"], (
        f"CFR-REFUSED-SITE-PERSISTED-LATER {cell}: saved={got['next_save']} new={got['file_new']} "
        f"download_dir={got['file_new_dd']} spillover_dirs={got['file_new_spill']}"
    )


# r4 (lens R114-C X1): the route check let a falsy non-string ([] / 0 / false) path field through, the route published
# (create, rename, clone), then the writer gate refused the save with "must be a string" and the publish stayed.
X1_REFUSED = ["x1-create-spill-list", "x1-create-spill-zero", "x1-create-spill-false", "x1-put-spill-list",
              "x1-put-spill-zero", "x1-put-dd-zero", "x1-put-dd-false", "clone-legacy-spill-list"]
X1_CONTROLS = ["x1-create-spill-empty", "x1-create-spill-null", "x1-put-spill-empty", "x1-put-spill-null"]


@pytest.mark.parametrize("cell", X1_REFUSED)
def test_route_refuses_a_non_string_path_field_before_it_publishes(tmp_path, cell):
    got = _child(tmp_path, "publish", {"cell": cell})
    assert got["armed"], f"CFR-FIXTURE-NOT-ARMED {cell}: {got}"
    leaked = got["new_live"] or got["new_runners"] or got["live_changed"] or got["file_new"]
    assert got["status"] == 400 and "must be a string" in got["error"] and not leaked and not got["file_changed"], (
        f"CFR-X1-NON-STRING-PUBLISHED {cell}: status={got['status']} error={got['error']!r} s_cfg+={got['new_live']} "
        f"changed={got['live_changed']} file+={got['file_new']} file_changed={got['file_changed']}"
    )
    if cell.startswith("x1-put"):
        assert got["c2_live_name"] == "c2" and got["c2_file"].get("name") == "c2", (
            f"CFR-X1-RENAME-SERVED {cell}: live={got['c2_live_name']!r} file={got['c2_file'].get('name')!r}"
        )


@pytest.mark.parametrize("cell", X1_CONTROLS)
def test_route_control_empty_or_null_path_field_still_publishes(tmp_path, cell):
    got = _child(tmp_path, "publish", {"cell": cell})
    assert got["armed"], f"CFR-FIXTURE-NOT-ARMED {cell}: {got}"
    stored = len(got["file_new"]) == 1 if "create" in cell else got["c2_file"].get("name") == "renamed"
    assert got["status"] == 200 and got["next_save"] is True and stored, (
        f"CFR-X1-CONTROL-REFUSED {cell}: status={got['status']} error={got['error']!r} file+={got['file_new']} "
        f"c2={got['c2_file'].get('name')!r}"
    )


@pytest.mark.parametrize("cell", PUBLISH_CONTROLS)
def test_route_control_good_or_held_paths_still_publish(tmp_path, cell):
    got = _child(tmp_path, "publish", {"cell": cell})
    want = got.get("want") or {}
    assert got["armed"], f"CFR-FIXTURE-NOT-ARMED {cell}: {got}"
    assert got["status"] == 200 and got["next_save"] is True and got["file_changed"], (
        f"CFR-CONTROL-WRITE-REFUSED {cell}: status={got['status']} error={got['error']!r} saved={got['next_save']}"
    )
    if cell == "cimport-control-legacy-unchanged":  # a value the site already holds is not re-judged
        assert (
            got["c2_file"].get("download_dir") == want["LEGACY"]
            and got["c2_file"].get("login_url") == "https://example.test/changed"
        ), f"CFR-CONTROL-HELD-PATH-NOT-KEPT {cell}: {got['c2_file']}"
    else:
        assert len(got["file_new"]) == 1 and got["file_new_dd"] == [want["INSIDE"]], (
            f"CFR-CONTROL-NEW-SITE-NOT-STORED {cell}: new={got['file_new']} download_dir={got['file_new_dd']}"
        )


def test_writer_stored_site_cookie_file_never_holds_an_account(tmp_path):
    """r3 (lens R80-B F2): the site's stored legacy cookie_file let a NEW accounts[0].cookie_file through."""
    got = _child(tmp_path, "gate", {"case": "cross-cell", "how": "site-to-acct"})
    assert got["first"] is False and not got["file_changed"], (
        f"CFR-WRITER-CROSS-CELL-HOLD site-to-acct: saved={got['first']} file_changed={got['file_changed']}"
    )
    live = got["live_accts"][0]
    assert live == "", f"CFR-WRITER-LEFT-BAD-VALUE-LIVE cross-cell site-to-acct: live={live!r} want=''"


def test_writer_stored_account_cookie_file_holds_the_site_cell(tmp_path):
    """r4 (lens R113-D F1): SiteRunner copies the active account's cookie_file into the site cell; a value an
    account stored before the roots narrowed keeps working there (r3 refused every later save)."""
    got = _child(tmp_path, "gate", {"case": "cross-cell", "how": "acct-to-site"})
    assert got["first"] is True and got["file_changed"] and got["live_site"] == got["out"], (
        f"CFR-GRANDFATHERED-ACCOUNT-REFUSED acct-to-site: saved={got['first']} live_site={got['live_site']!r}"
    )


def test_boot_with_a_grandfathered_active_account_still_saves_other_sites(tmp_path):
    """r4 (lens R113-D F1, probe_boot2): roots narrowed after r1 stored accounts a0/a1; boot makes a0 active and copies
    a0.json into the site cell. An unrelated site's PUT is stored, and the site cell keeps the active account's file."""
    got = _child(tmp_path, "boot-account", {})
    assert got["live_site_after_boot"] == got["A0"], f"CFR-FIXTURE-NOT-ARMED boot-account: {got}"
    assert got["status"] == 200 and got["file_other"] == "other2", (
        f"CFR-GRANDFATHERED-ACCOUNT-BLOCKS-SAVES boot: status={got['status']} error={got['error']!r} "
        f"file_other={got['file_other']!r}"
    )
    assert got["live_site"] == got["A0"] and got["file_site"] == got["A0"], (
        f"CFR-GRANDFATHERED-ACCOUNT-REVERTED boot: live={got['live_site']!r} file={got['file_site']!r} want={got['A0']!r}"
    )


def test_boot_control_a_new_site_cookie_file_outside_the_narrowed_roots_is_still_refused(tmp_path):
    """r4 keeps the narrowed roots for NEW paths: a site value no stored cell holds is refused and reverted."""
    got = _child(tmp_path, "boot-account", {"new_site_path": True})
    assert got["status"] == 400 and "cookie_file" in got["error"] and got["file_other"] != "other2", (
        f"CFR-NEW-PATH-NOT-REFUSED boot: status={got['status']} error={got['error']!r} file_other={got['file_other']!r}"
    )


def test_writer_control_reordered_stored_account_cookie_file_still_saves(tmp_path):
    """An account's stored value is held by any stored account cell: a new account in front must not refuse the old."""
    got = _child(tmp_path, "gate", {"case": "cross-cell", "how": "acct-reorder"})
    assert got["first"] is True and got["file_accts"] == [got["good"], got["out"]], (
        f"CFR-CONTROL-WRITE-REFUSED reordered account: saved={got['first']} accounts={got['file_accts']}"
    )
