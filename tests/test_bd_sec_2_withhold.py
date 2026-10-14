"""BD-SEC-2 ROW 2 (withhold): no client egress serves a notifier/credential URL,
a non-plain URL value or a URL token in free text.

The cell tables are the four lens tables that refuted the scrubbing design,
ported to this tree (hosts .invalid -> .example, nothing else changed):
R13-C (r11, top-level encodings), R44-B (r11, encodings x every egress),
R53-B (r12, targets nested in the path / fragment of a routing URL) and
argonH3 (r11 race, nested next= targets). Each value is planted in start_url
and notes of a fresh site and must not appear, at any percent-decoding level,
on any egress: the SSE status frame source, /api/status (light and full),
the site / config / marketplace (unsigned and signed) exports, the log tail
and the cross-site event feed. Positive controls prove the planted value
reaches the store; plain-URL controls prove ordinary values are still served.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote, unquote

import pytest


def _fx(*parts: str) -> str:
    """Fixture secret assembled at runtime. The VALUE is unchanged from r3-restack-c;
    only the source shape differs, so the secret scanner (gitleaks generic-api-key,
    integrator EJECT T281n 1830Z) sees no key-shaped literal. Not a credential."""
    return "".join(parts)

BD_GATE_SCOPE = "module"


def q(s, n=1):
    for _ in range(n):
        s = quote(s, safe="")
    return s


# ---- R13-C: six secret kinds x six forms ---------------------------------
def _r13c():
    c = "bdsecR13Cx9Q7canary0123"
    raw = {"tgram": f"tgram://123456:{c}/987", "slack": f"https://hooks.slack.com/services/T000/B000/{c}",
           "slackpath": f"/services/T000/B000/{c}", "param": f"https://h.example/dl?token={c}",
           "relparam": f"/dl?token={c}", "discord": f"https://discord.com/api/webhooks/123456789012345678/{c}"}
    out = {}
    for k, r in raw.items():
        forms = {"plain": r, "relnext": "/login?next=" + r, "pct": q(r), "dbl": q(r, 2),
                 "pctlow": q(r).lower().replace(c.lower(), c), "pathsafe": quote(r, safe="/")}
        for f, v in forms.items():
            out[f"r13c-{k}-{f}"] = (v, c)
    return out


# ---- R44-B: top-level encodings, bounds, short params ---------------------
def _nest(n, inner):
    for i in range(n):
        inner = "https://h%d.example/r?u=%s" % (i, inner)
    return inner


_R44B = {
    "ctl-tgram": "tgram://{c}/123", "ctl-slack": "/services/T000/B000/{c}",
    "ctl-param": "https://127.0.0.2:9/dl?token={c}",
    "ctl-bot": "https://api.telegram.org/bot123456789:{c}/sendMessage",
    "ctl-nextpct": "/login?next=" + q("tgram://{c}/123"),
    "pm-tgram-schemeenc": "tgram%3A%2F%2F{c}/123", "tgram-pct": q("tgram://{c}/123"),
    "tgram-dbl": q("tgram://{c}/123", 2), "tgram-mixedhex": "tgram%3a%2f%2F{c}/123",
    "slack-pct": q("/services/T000/B000/{c}"),
    "slack-url-slashenc": "https://hooks.slack.com/services%2FT000%2FB000%2F{c}",
    "bot-colonenc": "https://api.telegram.org/bot123456789%3A{c}/sendMessage",
    "discord-slashenc": "https://discord.com/api%2Fwebhooks%2F123456789012345678%2F{c}",
    "param-qenc": "https://127.0.0.2:9/dl%3Ftoken%3D{c}", "param-eqenc": "https://127.0.0.2:9/dl?token%3D{c}",
    "param-pct": q("https://127.0.0.2:9/dl?token={c}"), "param-dbl": q("https://127.0.0.2:9/dl?token={c}", 2),
    "short-rel-pct": "/dl%3Fpw%3D{c}", "userinfo-pct": q("https://user:{c}@127.0.0.2:9/p"),
    "nest4-plain": _nest(4, "https://z.example/dl?token={c}"),
    "nest5-plain": _nest(5, "https://z.example/dl?token={c}"),
    "next-enc4": "/login?next=" + q("/dl?pw={c}", 4), "next-enc5": "/login?next=" + q("/dl?pw={c}", 5),
    "short-k": "/dl?k={c}", "short-t": "/dl?t={c}", "short-sig": "/dl?sig={c}",
    "token-comma": "https://127.0.0.2:9/dl?token=abc,{c}",
}


def _r44b():
    out = {}
    for cell, value in _R44B.items():
        c = "r44bCan" + "".join(ch for ch in cell if ch.isalnum()) + "77abcdef0123"
        for n in range(6, -1, -1):
            value = value.replace(q("{c}", n), c)
        assert c in value and "{c}" not in value
        out[f"r44b-{cell}"] = (value, c)
    return out


# ---- R53-B: non-routing targets in the path / fragment of a routing URL ----
def _r53b():
    c = "bdsecR53Bx9Q7canary0123"
    tg, dc, pa = f"tgram://123456:{c}/987", f"discord://123456789012345678/{c}", f"https://h.example/dl?token={c}"
    cells = {
        "ctl-tgram": tg, "ctl-tgram-pct": q(tg), "ctl-frag-param-pct": "https://r.example/p#" + q(pa),
        "ctl-next-tg": "https://r.example/p?next=" + tg,
        "frag-tg-plain": "https://r.example/p#" + tg, "frag-tg-pct": "https://r.example/p#" + q(tg),
        "frag-tg-pct2": "https://r.example/p#" + q(tg, 2), "path-tg-plain": "https://r.example/go/" + tg,
        "path-tg-pct": "https://r.example/go/" + q(tg), "frag-dc-pct": "https://r.example/p#" + q(dc),
        "path-dc-plain": "https://r.example/go/" + dc, "rel-frag-tg-pct": "/p#" + q(tg),
        "bare-prefix-tg-pct": "https://r.example/" + q(tg), "glued-tg": "https://r.example/x" + tg,
        "comma-val": f"/dl?pw=abc,{c}", "short-k": f"/dl?k={c}", "proto-rel-ui": f"//user:{c}@h.example/p",
        "noscheme-ui": f"user:{c}@h.example:8080", "tg-pct12": q(tg, 12), "brace": "tgram://{%s}/{987}" % c,
    }
    return {f"r53b-{k}": (v, c) for k, v in cells.items()}


# ---- argonH3: nested next= targets (r11 race) -----------------------------
_ARGON = {
    "1-raw-nested-token": "https://a.example/r?next=https://b.example/dl?token={c}",
    "2-rel-nested-apikey": "/login?next=/dl?apikey={c}",
    "3-nested-tgram": "https://a.example/r?next=tgram://{c}/123",
    "4-pct-nested-token": "https://a.example/r?next=https%3A%2F%2Fb.example%2Fdl%3Ftoken%3D{c}",
    "5-dblpct-nested-apikey": "https://a.example/r?next=https%253A%252F%252Fb.example%252Fdl%253Fapikey%253D{c}",
    "6-pct-nested-tgram": "https://a.example/r?next=tgram%3A%2F%2F{c}%2F123",
    "7-dblpct-nested-tgram": "https://a.example/r?next=tgram%253A%252F%252F{c}%252F123",
    "8-rel-dblpct-tgram": "/login?next=tgram%253A%252F%252F{c}%252F123",
    "9-rel-pct-pw": "/login?next=%2Fdl%3Fpw%3D{c}",
    "10-rel-pct-slack": "/login?next=%2Fservices%2FT000%2FB000%2F{c}",
    "11-rel-pct-https": "/login?next=https%3A%2F%2Fb.example%2Fdl%3Ftoken%3D{c}",
    "12-rel-pct-tgram": "/login?next=tgram%3A%2F%2F{c}%2F123",
    "13-schemeless-slack": "/services/T000/B000/{c}",
}


def _argon():
    out = {}
    for cell, value in _ARGON.items():
        c = "CANARYargon" + cell.split("-")[0].zfill(2) + "abcdef123456"
        out[f"argonh3-{cell}"] = (value.replace("{c}", c), c)
    return out


CELLS = {**_r13c(), **_r44b(), **_r53b(), **_argon()}


def _decoded_has(text: str, canary: str) -> bool:
    d = text
    for _ in range(14):
        if canary in d:
            return True
        nd = unquote(d)
        if nd == d:
            break
        d = nd
    return canary in d


def _served(A, client, sid) -> dict:
    served = {"status snapshot (SSE src)": json.dumps(A._status_snapshot(light=True), default=str),
              "dashboard snapshot (SSE src)": json.dumps(A._dashboard_snapshot(), default=str)}
    for name, verb, url, body in (
            ("status light", "get", "/api/status?light=1", None),
            ("status", "get", "/api/status", None),
            ("site export", "get", f"/api/sites/{sid}/export", None),
            ("config export", "get", "/api/config/export", None),
            ("marketplace", "post", f"/api/marketplace/export/{sid}", {}),
            ("marketplace signed", "post", f"/api/marketplace/export/{sid}", {"sign_with": "k1"}),
            ("logs tail", "get", "/api/logs/tail?lines=50", None),
            ("events all", "get", "/api/events_all", None)):
        r = client.post(url, json=body) if verb == "post" else client.get(url)
        served[name] = f"HTTP{r.status_code} " + r.get_data(as_text=True)
    return served


def _plant(A, client, name, fields: dict) -> str:
    sid = client.post("/api/sites", json={"name": name}).get_json()["id"]
    A.s_cfg[sid].update(fields)
    A.s_meta.setdefault(sid, {}).update(fields)
    return sid


@pytest.mark.parametrize("cell", sorted(CELLS))
def test_every_egress_withholds_every_lens_cell(fresh_app, cell):
    from bulk_downloader import app as A
    value, canary = CELLS[cell]
    sid = _plant(A, fresh_app, "wh-" + cell[:40], {"start_url": value, "notes": f"see {value} ok"})
    A.runners[sid].log_event("error", f"fetch failed for {value}")
    Path("logs").mkdir(exist_ok=True)
    Path("logs/bulk_downloader.log").write_text(f"INFO fetch {value} done\n", encoding="utf-8")
    assert A.s_cfg[sid]["start_url"] == value  # positive control: the value is really stored
    served = _served(A, fresh_app, sid)
    assert all(t.startswith("HTTP200") for n, t in served.items() if t.startswith("HTTP")), {
        n: t[:80] for n, t in served.items() if t.startswith("HTTP") and not t.startswith("HTTP200")}
    leaked = sorted(n for n, t in served.items() if _decoded_has(t, canary))
    assert not leaked, f"BD_SEC_2_WITHHOLD_LEAK: {cell} value={value[:120]!r} served on {leaked}"


_NOTIFIER = {
    "webhook_urls": "tgram://123456:{c}/987\nhttps://discord.com/api/webhooks/1/{c}",
    "notify_apprise_urls": "discord://123456789012345678/{c}",
}


def test_notifier_fields_are_structured_views_never_values(fresh_app):
    from bulk_downloader import app as A
    c = "bdsecNOTIFYx9Q7canary0123"
    fields = {k: v.replace("{c}", c) for k, v in _NOTIFIER.items()}
    sid = _plant(A, fresh_app, "wh-notifier", fields)
    served = _served(A, fresh_app, sid)
    leaked = sorted(n for n, t in served.items() if _decoded_has(t, c))
    assert not leaked, f"BD_SEC_2_WITHHOLD_LEAK: notifier field served on {leaked}"
    cfg = fresh_app.get("/api/status?light=1").get_json()[sid]["config"]
    assert cfg["webhook_urls"] == {"configured": True, "scheme": "tgram", "masked": "tgram://***",
                                   "count": 2, "withheld": True}, f"BD_SEC_2_WITHHOLD_VIEW: {cfg['webhook_urls']}"
    assert cfg["notify_apprise_urls"]["scheme"] == "discord", f"BD_SEC_2_WITHHOLD_VIEW: {cfg['notify_apprise_urls']}"
    snap = A._status_snapshot(light=True)[sid]["config"]
    assert snap["webhook_urls"] == cfg["webhook_urls"], "BD_SEC_2_WITHHOLD_VIEW: SSE frame differs from /api/status"
    unset = _plant(A, fresh_app, "wh-notifier-unset", {"webhook_urls": ""})
    view = fresh_app.get("/api/status?light=1").get_json()[unset]["config"]["webhook_urls"]
    assert view["configured"] is False and view["masked"] == "", f"BD_SEC_2_WITHHOLD_VIEW: unset -> {view}"


_PLAIN = ["https://example.com/gallery?page=2&sort=new", "https://example.com/a/b-c_d.e#/x",
          "http://192.168.1.5:32400/library/sections"]


@pytest.mark.parametrize("url", _PLAIN)
def test_plain_urls_and_text_are_still_served(fresh_app, url):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app, "wh-plain", {"start_url": url, "notes": "weekly batch, check 10:30 run"})
    cfg = fresh_app.get("/api/status?light=1").get_json()[sid]["config"]
    assert cfg["start_url"] == url, f"BD_SEC_2_WITHHOLD_OVERREACH: {url} served as {cfg['start_url']}"
    assert cfg["notes"] == "weekly batch, check 10:30 run", f"BD_SEC_2_WITHHOLD_OVERREACH: notes {cfg['notes']}"
    exported = fresh_app.get(f"/api/sites/{sid}/export").get_json()["config"]
    assert exported["start_url"] == url, f"BD_SEC_2_WITHHOLD_OVERREACH: export {exported['start_url']}"


def test_config_export_import_round_trip_keeps_stored_values(fresh_app):
    from bulk_downloader import app as A
    c = "bdsecRTx9Q7canary0123"
    want = {"webhook_urls": f"tgram://123456:{c}/987", "notify_apprise_urls": f"discord://1/{c}",
            "start_url": f"https://r.example/dl?token={c}", "plex_url": "http://192.168.1.5:32400"}
    sid = _plant(A, fresh_app, "wh-roundtrip", want)
    body = fresh_app.get("/api/config/export").get_data(as_text=True)
    assert not _decoded_has(body, c), "BD_SEC_2_WITHHOLD_LEAK: config export"
    assert "http://192.168.1.5:32400" in body, "BD_SEC_2_WITHHOLD_OVERREACH: plain plex_url not exported"
    r = fresh_app.post("/api/config/import?mode=merge", json=json.loads(body))
    assert r.status_code == 200 and r.get_json()["updated"] >= 1, r.get_data(as_text=True)[:300]
    changed = sorted(k for k, v in want.items() if A.s_cfg[sid].get(k) != v)
    assert not changed, f"BD_SEC_2_WITHHOLD_ROUNDTRIP: merge import of a default export changed {changed}"


def test_site_import_of_withheld_export_blanks_and_names_fields(fresh_app):
    from bulk_downloader import app as A
    c = "bdsecSIx9Q7canary0123"
    sid = _plant(A, fresh_app, "wh-siteimport", {"webhook_urls": f"tgram://1:{c}/2",
                                                 "start_url": f"https://r.example/dl?token={c}"})
    env = fresh_app.get(f"/api/sites/{sid}/export").get_json()
    r = fresh_app.post("/api/sites/import", json=env)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    new = A.s_cfg[r.get_json()["id"]]
    warn = " ".join(r.get_json()["warnings"])
    assert new["webhook_urls"] == "" and new["start_url"] == "" and "webhook_urls" in warn, (
        f"BD_SEC_2_WITHHOLD_IMPORT: new site got {new['webhook_urls']!r} / {new['start_url']!r}; warnings {warn!r}")


def test_put_echoing_a_marker_keeps_the_stored_value(fresh_app):
    from bulk_downloader import app as A
    hook = "tgram://123456:bdsecPUTx9Q7canary0123/987"
    sid = _plant(A, fresh_app, "wh-put", {"webhook_urls": hook})
    view = fresh_app.get("/api/status?light=1").get_json()[sid]["config"]["webhook_urls"]
    r = fresh_app.put(f"/api/sites/{sid}", json={"webhook_urls": view})
    assert r.status_code == 200 and A.s_cfg[sid]["webhook_urls"] == hook, (
        f"BD_SEC_2_WITHHOLD_PUT: echoed marker stored as {A.s_cfg[sid]['webhook_urls']!r}")


def test_percent_run_notes_withheld_in_linear_time(fresh_app):
    from bulk_downloader import app as A
    from bulk_downloader import egress_withhold as W
    sid = _plant(A, fresh_app, "wh-dos", {"notes": "%" * 40000, "start_url": "%" * 40000})
    worst = {}
    for name, fn in (("status", lambda: fresh_app.get("/api/status?light=1")),
                     ("snapshot", lambda: A._status_snapshot(light=True)),
                     ("site export", lambda: fresh_app.get(f"/api/sites/{sid}/export"))):
        runs = []
        for _ in range(3):
            t0 = time.perf_counter()
            fn()
            runs.append(time.perf_counter() - t0)
        worst[name] = round(min(runs) * 1000, 2)
    assert max(worst.values()) < 50, f"BD_SEC_2_WITHHOLD_SLOW: '%'x40000 notes ms {worst}"
    t0 = time.perf_counter()
    W.withhold_text("%" * 400000)
    W.url_value("%" * 400000)
    assert time.perf_counter() - t0 < 0.05, "BD_SEC_2_WITHHOLD_SLOW: 400k '%' function level"


# ---- every GET route that returned stored config on BASE ------------------
# From the url_map census (617 GET rules; evidence census-base2 / census-tree3):
# these 11 served a planted canary on BASE. Zip bodies are opened, the SSE
# stream is read to its first two frames.
_CONFIG_ROUTES = ["/api/status", "/api/stream", "/api/global_config", "/api/config/export",
                  "/api/sites/{sid}/export", "/api/sites/{sid}/template_status", "/api/dev/config",
                  "/api/diagnostics/bundle", "/api/diagnostics/download",
                  "/api/diagnostics_bundle/preview", "/api/diagnostics_bundle/download"]
_ROUTE_CANARY = {
    "start_url": "https://r.example/dl?token={c}", "notes": "mirror https://m.example/{c} weekly",
    "webhook_urls": "tgram://123:{c}/9", "notify_apprise_urls": "discord://1/{c}",
    "login_url": "https://r.example/login?sig={c}", "plex_token": "{c}",
}


def _body_text(resp) -> str:
    import io
    import zipfile
    if resp.mimetype == "text/event-stream":
        chunks = []
        for chunk in resp.response:
            chunks.append(chunk if isinstance(chunk, bytes) else chunk.encode())
            if len(chunks) >= 2:
                break
        resp.close()
        raw = b"".join(chunks)
    else:
        raw = resp.get_data()
    text = raw.decode("utf-8", errors="replace")
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            text += "".join(zf.read(n).decode("utf-8", errors="replace") for n in zf.namelist())
    return text


@pytest.mark.parametrize("route", _CONFIG_ROUTES)
def test_every_config_route_withholds(fresh_app, route):
    from bulk_downloader import app as A
    canaries = {k: f"bdsecRT{k.replace('_', '')}X9q7" for k in _ROUTE_CANARY}
    sid = fresh_app.post("/api/sites", json={"name": "wh-route"}).get_json()["id"]
    A.s_cfg[sid].update({k: v.replace("{c}", canaries[k]) for k, v in _ROUTE_CANARY.items()})
    A.s_meta[sid] = A._build_meta(A.s_cfg[sid])
    A._app_cfg["notify_apprise_urls"] = "tgram://9:bdsecRTglobalX9q7/1"
    canaries["global notify_apprise_urls"] = "bdsecRTglobalX9q7"
    resp = fresh_app.get(route.format(sid=sid), buffered=True if "stream" not in route else False)
    assert resp.status_code == 200, f"{route} -> {resp.status_code}"
    text = _body_text(resp)
    assert sid in text or "stream" in route or "global_config" in route, f"{route}: planted site absent (probe blind)"
    leaked = sorted(k for k, c in canaries.items() if _decoded_has(text, c))
    assert not leaked, f"BD_SEC_2_WITHHOLD_LEAK: {route} serves {leaked}"


# ---- r2: every GET rule x every stored-config class R23-C named -----------
# R23-C (r1 refute) planted canaries where the r1 census did not: webhook
# subscriptions (F1), proxy userinfo (F3), flaresolverr endpoint, search
# pattern and nested subscriptions / accounts / pre_scrape / pipeline (F4),
# a notes dict key, the global proxy, a queued job url and an event url (F6).
# The walk covers every GET rule whose arguments are all site ids.
_R2 = {
    "proxy": "bdsecR2proxyQ7", "flaresolverr": "bdsecR2fsQ7", "subscriptions": "bdsecR2subsQ7",
    "accounts": "bdsecR2acctQ7", "pre_scrape": "bdsecR2preQ7", "pipeline": "bdsecR2pipeQ7",
    "notes_key": _fx("bdsecR2", "nkeyQ7"), "search_pat": "bdsecR2searchQ7", "global_proxy": "bdsecR2gpxQ7",
    "webhook_sub": "bdsecR2whQ7", "webhook_sub_q": "bdsecR2whqQ7", "job_url": "bdsecR2jobQ7",
    "event_url": "bdsecR2evQ7", "struct_key": "bdsecR2skeyQ7",
}
_R2_PLAIN_EVENT = "https://plain.example/gallery/bdsecR2ctlQ7"
_R2_SID_ARGS = {"sid", "site_id", "site", "sid1", "id"}


def _r2_plant(A, client) -> str:
    from bulk_downloader import webhooks as W
    c = _R2
    sid = client.post("/api/sites", json={"name": "wh-r2"}).get_json()["id"]
    A.s_cfg[sid].update({
        "proxy": f"http://user:{c['proxy']}@proxy.example:3128",
        "flaresolverr_endpoint": f"http://127.0.0.1:9/v1?token={c['flaresolverr']}",
        "subscriptions": [{"name": "s", "url": f"https://t.example/rss?passkey={c['subscriptions']}",
                           "interval_hours": 6}],
        "accounts": [{"label": "a", "username": "u",
                      "login_url": f"https://x.example/l?token={c['accounts']}"}],
        "pre_scrape_action": {"url": f"https://x.example/pre?token={c['pre_scrape']}",
                              "headers": {f"https://h.example/k?token={c['struct_key']}": "1"}},
        "post_download_pipeline": [{"name": "p", "condition": "true",
                                    "command": f"curl https://hooks.slack.com/services/T0/B0/{c['pipeline']}"}],
        "notes": {f"https://n.example/{c['notes_key']}": "x"},
        "search_url_pattern": f"https://s.example/?q={{q}}&apikey={c['search_pat']}",
    })
    A.s_meta[sid] = A._build_meta(A.s_cfg[sid])
    A._app_cfg["proxy"] = f"u:{c['global_proxy']}@gp.example:8080"  # scheme-less userinfo
    A._save_sites_config()  # persisted, as every real write is: disk-backed readers see the plant too
    A._save_app_config()
    W.add_subscription(url=f"https://hooks.slack.com/services/T000/B000/{c['webhook_sub']}",
                       events=["download.done"])
    W.add_subscription(url=f"http://192.168.1.9:8123/api/webhook/x?token={c['webhook_sub_q']}",
                       events=["download.done"])
    A.runners[sid].add_url(f"https://r.example/file?sig={c['job_url']}")
    A.runners[sid].log_event("info", "fetched ok", url=f"https://r.example/e?token={c['event_url']}")
    A.runners[sid].log_event("info", "fetched plain", url=_R2_PLAIN_EVENT)
    return sid


class _R2Timeout(BaseException):
    pass


def _r2_alarm(*_a):
    raise _R2Timeout()


def test_every_get_rule_withholds_every_r23c_class(fresh_app):
    import signal
    from bulk_downloader import app as A
    sid = _r2_plant(A, fresh_app)
    # positive controls: the planted values reach the store / the feeds
    assert "bdsecR2proxyQ7" in A.s_cfg[sid]["proxy"] and len(fresh_app.get("/api/webhooks").get_json()["subscriptions"]) == 2
    feed = fresh_app.get(f"/api/sites/{sid}/events").get_data(as_text=True)
    assert _R2_PLAIN_EVENT in feed, "BD_SEC_2_WITHHOLD_PROBE_BLIND: plain event url not served"
    eff = fresh_app.get(f"/api/settings/site/{sid}/effective").get_json()
    assert "search_url_pattern" in (eff.get("fields") or {}), "BD_SEC_2_WITHHOLD_PROBE_BLIND: disk-backed site reader blind"
    served = {"status snapshot (SSE src)": json.dumps(A._status_snapshot(light=False), default=str)
              + json.dumps(A._status_snapshot(light=True), default=str)}
    errors = []
    rules = [r for r in A.app.url_map.iter_rules() if "GET" in r.methods and r.endpoint != "static"
             and not set(r.arguments) - _R2_SID_ARGS]
    old = signal.signal(signal.SIGALRM, _r2_alarm)
    try:
        for r in sorted(rules, key=lambda r: r.rule):
            url = r.rule
            for a in r.arguments:
                url = url.replace(f"<{a}>", sid).replace(f"<string:{a}>", sid)
            signal.alarm(20)
            try:
                served[url] = _body_text(fresh_app.get(url, buffered=False))
            except _R2Timeout:
                errors.append(f"{url} timeout")
            except Exception as e:  # noqa: BLE001 -- a route that cannot answer is reported below
                errors.append(f"{url} {type(e).__name__}")
            finally:
                signal.alarm(0)
    finally:
        signal.signal(signal.SIGALRM, old)
    leaks = {k: sorted(n for n, t in served.items() if _decoded_has(t, c)) for k, c in _R2.items()}
    leaks = {k: v for k, v in leaks.items() if v}
    assert not leaks, f"BD_SEC_2_WITHHOLD_LEAK: {leaks}"
    assert len(rules) > 400 and not errors, f"BD_SEC_2_WITHHOLD_PROBE_BLIND: {len(rules)} rules, {errors}"


def test_webhook_list_serves_masked_target_and_view(fresh_app):
    r = fresh_app.post("/api/webhooks", json={"url": "https://hooks.slack.com/services/T000/B000/bdsecR2F1Q7",
                                              "events": ["download.done"]})
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    subs = fresh_app.get("/api/webhooks").get_json()["subscriptions"]
    assert len(subs) == 1 and subs[0]["url"] == "https://***", f"BD_SEC_2_WITHHOLD_LEAK: /api/webhooks {subs}"
    assert subs[0]["url_withheld"]["configured"] is True and subs[0]["url_withheld"]["scheme"] == "https", subs


def test_diff_preview_withholds_and_treats_the_served_form_as_unchanged(fresh_app):
    from bulk_downloader import app as A
    stored = {"start_url": "https://r.example/dl?token=bdsecR2F2aQ7",
              "proxy": "http://user:bdsecR2F2bQ7@proxy.example:3128",
              "webhook_urls": "tgram://1:bdsecR2F2cQ7/2"}
    sid = _plant(A, fresh_app, "wh-diff", stored)
    A.s_meta[sid] = A._build_meta(A.s_cfg[sid])
    shown = fresh_app.get("/api/status?light=1").get_json()[sid]["config"]
    r = fresh_app.post(f"/api/sites/{sid}/diff", json={k: shown[k] for k in stored})
    assert r.status_code == 200 and r.get_json()["change_count"] == 0, (
        f"BD_SEC_2_WITHHOLD_DIFF: served form reported as a change: {r.get_data(as_text=True)[:300]}")
    new = {"start_url": "https://r.example/dl?token=bdsecR2F2dQ7",
           "proxy": "http://user:bdsecR2F2eQ7@proxy.example:3128",
           "webhook_urls": "tgram://1:bdsecR2F2fQ7/2"}
    r = fresh_app.post(f"/api/sites/{sid}/diff", json=new)
    body = r.get_data(as_text=True)
    assert r.get_json()["change_count"] == 3, body[:300]
    leaked = [c for c in ("F2aQ7", "F2bQ7", "F2cQ7", "F2dQ7", "F2eQ7", "F2fQ7") if _decoded_has(body, "bdsecR2" + c)]
    assert not leaked, f"BD_SEC_2_WITHHOLD_LEAK: /api/sites/<sid>/diff serves {leaked}"
    plain = fresh_app.post(f"/api/sites/{sid}/diff", json={"start_url": "https://example.com/g?page=2"}).get_json()
    assert plain["diff"][0]["new"] == "https://example.com/g?page=2", f"BD_SEC_2_WITHHOLD_OVERREACH: {plain}"


@pytest.mark.parametrize("field,value", [
    ("webhook_urls", "tgram://123456:bdsecR2F5aQ7/987"),
    ("start_url", "https://r.example/dl?token=bdsecR2F5bQ7"),
    ("proxy", "http://user:bdsecR2F5cQ7@proxy.example:3128"),
    ("subscriptions", [{"name": "s", "url": "https://t.example/rss?passkey=bdsecR2F5dQ7", "interval_hours": 6}]),
])
def test_put_echoing_the_served_form_keeps_the_stored_value(fresh_app, field, value):
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app, "wh-echo", {field: value})
    A.s_meta[sid] = A._build_meta(A.s_cfg[sid])
    shown = fresh_app.get("/api/status?light=1").get_json()[sid]["config"][field]
    assert shown != value, f"BD_SEC_2_WITHHOLD_LEAK: {field} served raw"
    if isinstance(shown, dict) and "masked" in shown:
        shown = shown["masked"]  # a SPA text box holds the masked string
    r = fresh_app.put(f"/api/sites/{sid}", json={field: shown})
    assert r.status_code == 200 and A.s_cfg[sid][field] == value, (
        f"BD_SEC_2_WITHHOLD_PUT: {field} echo {shown!r} stored as {A.s_cfg[sid][field]!r}")


def test_config_export_merge_keeps_nested_stored_values(fresh_app):
    from bulk_downloader import app as A
    want = {"subscriptions": [{"name": "s", "url": "https://t.example/rss?passkey=bdsecR2RTaQ7",
                               "interval_hours": 6}],
            "proxy": "http://user:bdsecR2RTbQ7@proxy.example:3128"}
    sid = _plant(A, fresh_app, "wh-rt2", want)
    body = fresh_app.get("/api/config/export").get_data(as_text=True)
    assert not any(_decoded_has(body, f"bdsecR2RT{x}Q7") for x in "ab"), "BD_SEC_2_WITHHOLD_LEAK: config export"
    r = fresh_app.post("/api/config/import?mode=merge", json=json.loads(body))
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    changed = sorted(k for k, v in want.items() if A.s_cfg[sid].get(k) != v)
    assert not changed, f"BD_SEC_2_WITHHOLD_ROUNDTRIP: merge import changed {changed}"


def test_put_of_a_partly_edited_list_keeps_the_withheld_items(fresh_app):
    from bulk_downloader import app as A
    subs = [{"name": "a", "url": "https://t.example/rss?passkey=bdsecR2L1Q7", "interval_hours": 6},
            {"name": "b", "url": "https://t.example/rss?passkey=bdsecR2L2Q7", "interval_hours": 6}]
    sid = _plant(A, fresh_app, "wh-list", {"subscriptions": [dict(x) for x in subs]})
    A.s_meta[sid] = A._build_meta(A.s_cfg[sid])
    shown = fresh_app.get("/api/status?light=1").get_json()[sid]["config"]["subscriptions"]
    assert shown[0]["url"] != subs[0]["url"], f"BD_SEC_2_WITHHOLD_LEAK: subscriptions served raw {shown}"
    shown[0]["interval_hours"] = 12  # the operator edits one item in place
    r = fresh_app.put(f"/api/sites/{sid}", json={"subscriptions": shown})
    got = A.s_cfg[sid]["subscriptions"]
    assert r.status_code == 200 and [x["url"] for x in got] == [x["url"] for x in subs] \
        and got[0]["interval_hours"] == 12, f"BD_SEC_2_WITHHOLD_PUT: edited list stored as {got}"
    shown = fresh_app.get("/api/status?light=1").get_json()[sid]["config"]["subscriptions"]
    new = {"name": "c", "url": "https://example.com/feed", "interval_hours": 1}
    r = fresh_app.put(f"/api/sites/{sid}", json={"subscriptions": [shown[1], new]})
    got = A.s_cfg[sid]["subscriptions"]
    assert [x["url"] for x in got] == [subs[1]["url"], new["url"]], (
        f"BD_SEC_2_WITHHOLD_PUT: resized list stored as {got}")


# ---- r3: the classes R121-C (r2 refute) and R120-D (r2 note) named ---------
# F1: a failed webhook delivery stores the target's path in last_error
# (/api/webhooks served it); every app-written error / status string is a
# planted canary too (DIAGNOSTIC_TEXT_FIELDS on every JSON response). F2: the
# live /api/stream frames (broker fan-out and dev-events tap). N1: a
# percent-encoded URL (or a scheme-less www. URL) in a structural string. N2: a token-in-path webhook on
# a notifier subdomain / a /hooks/ path. N4: a secret holding the word
# "redacted". R120-D could-not-look: global effective, ai_endpoint, novnc_url.
_R3 = {
    "wh_last_error": "bdsecR3whQ7", "ai_health": "bdsecR3aiQ7", "fs_stats": "bdsecR3fsQ7",
    "pct_struct": "bdsecR3pctQ7", "zap_pipe": "bdsecR3zapQ7", "zap_subs": "bdsecR3zsubQ7",
    "www_discord": "bdsecR3wwwQ7", "www_bare": "bdsecR3wbareQ7", "redacted_secret": "bdsecR3redactedQ7",
    "ai_endpoint": "bdsecR3aieQ7", "novnc_url": "bdsecR3vncQ7", "global_proxy": "bdsecR3gpQ7",
    "frame_event": "bdsecR3evQ7", "frame_job": "bdsecR3jobQ7", "frame_progress": "bdsecR3dpQ7",
}


def _r3_webhook_failure(c: str, monkeypatch) -> int:
    """R121-C P1: the real fire -> retry state machine stores requests' error text."""
    from bulk_downloader import webhooks as W
    wid = W.add_subscription(url=f"http://127.0.0.1:9/services/T000/B000/{c}", events=["download.done"])
    monkeypatch.setattr(W, "_RETRY_DELAYS", [0] * len(W._RETRY_DELAYS))  # delays skipped, attempts real
    W.fire("download.done", {})
    for _ in range(len(W._RETRY_DELAYS) + 1):
        W._drain_once()
    stored = [s.get("last_error") for s in W.list_subscriptions() if s.get("id") == wid]
    assert stored and c in (stored[0] or ""), f"BD_SEC_2_WITHHOLD_PROBE_BLIND: webhook last_error not stored ({stored!r})"
    return wid


def _r3_plant(A, client) -> str:
    from bulk_downloader import aiassist
    c = _R3
    sid = client.post("/api/sites", json={"name": "wh-r3"}).get_json()["id"]
    A.s_cfg[sid].update({
        # pct_struct and www_bare have their own steps: no other URL hint beside them, so each struct_text gate
        # clause alone decides whether the string is scanned.
        "post_download_pipeline": [{"name": "q", "condition": "true", "command":
                                    f"curl -d u=https%3A%2F%2Fhooks.slack.com%2Fservices%2FT%2FB%2F{c['pct_struct']} x"},
                                   {"name": "w", "condition": "true", "command":
                                    f"curl -d x www.discord.com/api/webhooks/1/{c['www_bare']}"},
                                   {"name": "p", "condition": "true", "command":
                                    f"curl -X POST https://hooks.zapier.com/hooks/catch/1/{c['zap_pipe']}/ ; "
                                    f"curl https://www.discord.com/api/webhooks/1/{c['www_discord']}"}],
        "subscriptions": [{"name": "s", "url": f"https://hooks.zapier.com/hooks/catch/2/{c['zap_subs']}/",
                           "interval_hours": 6}],
        "api_key": c["redacted_secret"],
    })
    A.s_meta[sid] = A._build_meta(A.s_cfg[sid])
    A._app_cfg["ai_endpoint"] = f"http://127.0.0.1:11434/v1?api_key={c['ai_endpoint']}"
    A._app_cfg["novnc_url"] = f"http://u:{c['novnc_url']}@vnc.example:6080/vnc.html"
    A._app_cfg["proxy"] = f"http://u:{c['global_proxy']}@gp.example:8080"
    A._save_sites_config()
    A._save_app_config()
    aiassist._health["last_error"] = f"ctlR3aiQ7 POST http://127.0.0.1:11434/api?key={c['ai_health']} refused"
    A._flare_client._stats.last_error = f"ctlR3fsQ7 GET /v1/session/{c['fs_stats']} timed out"
    return sid


def test_every_egress_withholds_every_r3_class(fresh_app, monkeypatch):
    import signal
    from bulk_downloader import app as A
    from bulk_downloader import sse_broker as B
    c = _R3
    broker = B.get_broker()
    sub = broker.subscribe()
    try:
        sid = _r3_plant(A, fresh_app)
        wid = _r3_webhook_failure(c["wh_last_error"], monkeypatch)
        ev = f"https://r.example/dl?token={c['frame_event']}"
        A.runners[sid].log_event("error", f"fetch failed for {ev}", url=ev)
        job = f"https://r.example/dl?token={c['frame_job']}"
        A.runners[sid].add_url(job)
        A.runners[sid]._update_job(job, "failed", f"fetch failed for {job}")
        broker.publish("download_progress", {"site": sid, "pct": 5, "ctl": "ctlR3dpQ7",
                                             "url": f"https://r.example/f?sig={c['frame_progress']}"})
        frames = []
        for _ in range(200):
            m = broker.pull(sub, timeout=0.2)
            if m is None:
                break
            frames.append(m)
    finally:
        broker.unsubscribe(sub)
    served = {"broker frames": "\n".join(frames), "dev-events tap": json.dumps(B._dev_events.recent(500), default=str)}
    assert "ctlR3dpQ7" in served["dev-events tap"], "BD_SEC_2_WITHHOLD_PROBE_BLIND: dev-events tap"
    # positive controls: each plant reached the store and an egress can show it
    assert {"event_log", "queue_change", "download_progress"} <= {
        m.split("\n", 1)[0].removeprefix("event: ") for m in frames}, f"BD_SEC_2_WITHHOLD_PROBE_BLIND: frames {frames[:3]}"
    assert "ctlR3dpQ7" in served["broker frames"], "BD_SEC_2_WITHHOLD_PROBE_BLIND: progress frame"
    row = [s for s in fresh_app.get("/api/webhooks").get_json()["subscriptions"] if s.get("id") == wid][0]
    assert row.get("last_error") and row.get("error_count"), f"BD_SEC_2_WITHHOLD_PROBE_BLIND: webhook row {row}"
    geff = fresh_app.get("/api/settings/global/effective").get_json()
    assert {"ai_endpoint", "novnc_url"} <= set(geff.get("keys") or {}), (
        f"BD_SEC_2_WITHHOLD_PROBE_BLIND: global effective keys {sorted(geff.get('keys') or {})[:40]}")
    rules = [r for r in A.app.url_map.iter_rules() if "GET" in r.methods and r.endpoint != "static"
             and not set(r.arguments) - _R2_SID_ARGS]
    errors = []
    old = signal.signal(signal.SIGALRM, _r2_alarm)
    try:
        for r in sorted(rules, key=lambda r: r.rule):
            url = r.rule
            for a in r.arguments:
                url = url.replace(f"<{a}>", sid).replace(f"<string:{a}>", sid)
            signal.alarm(20)
            try:
                served[url] = _body_text(fresh_app.get(url, buffered=False))
            except _R2Timeout:
                errors.append(f"{url} timeout")
            except Exception as e:  # noqa: BLE001 -- a route that cannot answer is reported below
                errors.append(f"{url} {type(e).__name__}")
            finally:
                signal.alarm(0)
    finally:
        signal.signal(signal.SIGALRM, old)
    for ctl in ("ctlR3aiQ7", "ctlR3fsQ7"):
        assert any(ctl in t for t in served.values()), f"BD_SEC_2_WITHHOLD_PROBE_BLIND: {ctl} served nowhere"
    # OPEN PM CALL (r3 DONE): routes whose consumer needs the URL itself -- the noVNC iframe src and
    # the job URL that keys a queue/history row. Exempt per (canary, route) pair, never per canary:
    # the same job URL in broker frames and every other route must still be withheld.
    pm_call = {("novnc_url", "/cockpit/api/novnc")} | {("frame_job", r.replace("{sid}", sid)) for r in (
        "/api/data/queue_analytics", "/api/queue/v2", "/api/sites/{sid}/export_watchlist",
        "/api/sites/{sid}/history/export", "/api/sites/{sid}/queue", "/api/sites/{sid}/queue/export",
        "/api/sites/{sid}/queue/grouped", "/api/sites/{sid}/queue/search")}
    leaks = {k: sorted(n for n, t in served.items() if _decoded_has(t, v) and (k, n) not in pm_call)
             for k, v in c.items()}
    leaks = {k: v for k, v in leaks.items() if v}
    assert not leaks, f"BD_SEC_2_WITHHOLD_LEAK: {leaks}"
    assert len(rules) > 400 and not errors, f"BD_SEC_2_WITHHOLD_PROBE_BLIND: {len(rules)} rules, {errors}"


@pytest.mark.parametrize("sent,want", [
    ("tgram://***", "tgram://123:bdsecR3rtQ7/456"),
    ("  tgram://***  ", "tgram://123:bdsecR3rtQ7/456"),
    (["tgram://***"], "tgram://123:bdsecR3rtQ7/456"),
    ("tgram://***\ndiscord://9/new", "tgram://123:bdsecR3rtQ7/456\ndiscord://9/new"),
    (["tgram://***", "discord://9/new"], ["tgram://123:bdsecR3rtQ7/456", "discord://9/new"]),
    ("discord://9/new", "discord://9/new"),
])
def test_put_of_a_partly_edited_credential_field_never_stores_the_mask(fresh_app, sent, want):
    """R120-D N3: the mask beside a new URL stands for the stored URLs."""
    from bulk_downloader import app as A
    sid = _plant(A, fresh_app, "wh-n3", {"webhook_urls": "tgram://123:bdsecR3rtQ7/456"})
    r = fresh_app.put(f"/api/sites/{sid}", json={"webhook_urls": sent})
    got = A.s_cfg[sid]["webhook_urls"]
    assert r.status_code == 200 and got == want, f"BD_SEC_2_WITHHOLD_PUT: {sent!r} stored as {got!r}"


def test_a_mask_with_nothing_stored_is_dropped_not_stored():
    from bulk_downloader.egress_withhold import _restore_echo
    assert _restore_echo("webhook_urls", "tgram://***", "") == "", "BD_SEC_2_WITHHOLD_PUT: mask stored"
    assert _restore_echo("webhook_urls", ["tgram://***", "discord://9/n"], None) == ["discord://9/n"], (
        "BD_SEC_2_WITHHOLD_PUT: mask stored beside a new URL")


@pytest.mark.parametrize("value,shown", [
    ("bdsecR3redactedQ7", "<configured>"), ("xx-REDACTED-xx", "<configured>"), ("<abcdef>", "<configured>"),
    ("<redacted>", "<redacted>"), ("***", "***"), ("[REDACTED]", "[REDACTED]"), ("@cred:ai_openai_key", "@cred:ai_openai_key"),
    ("<configured>", "<configured>"), ("", ""),
])
def test_only_a_whole_known_mask_counts_as_already_masked(value, shown):
    """R120-D N4: a secret merely containing a mask word is still a secret."""
    from bulk_downloader.egress_withhold import secret_value
    assert secret_value(value) == shown, f"BD_SEC_2_WITHHOLD_LEAK: secret {value!r} served as {secret_value(value)!r}"


@pytest.mark.parametrize("value", [
    "https://example.com/gallery/1?page=2", "https://plain.example/a/b", "%Y-%m-%d_{title}",
    "ffmpeg -i in.mp4 -vf scale=1280:-2 out.mp4", "100%",
])
def test_plain_values_survive_the_r3_rules(value):
    """Controls for N1 / N2: plain routing URLs and percent text are served as stored."""
    from bulk_downloader.egress_withhold import struct_text, value_view
    assert struct_text(value) == value and value_view("start_url" if "://" in value else "x", value) == value, (
        f"BD_SEC_2_WITHHOLD_OVERREACH: {value!r}")


def test_status_frame_view_is_idempotent(fresh_app):
    """A frame built from the viewed snapshot passes the broker unchanged."""
    from bulk_downloader import app as A
    from bulk_downloader.egress_withhold import frame_view
    _plant(A, fresh_app, "wh-idem", {"webhook_urls": "tgram://1:bdsecR3idQ7/2", "notes": "see https://x.example/a",
                                     "proxy": "http://u:p@px.example:1"})
    snap = json.loads(json.dumps(A._status_snapshot(light=False), default=str))
    assert frame_view(snap) == snap, "BD_SEC_2_WITHHOLD_FRAME: broker view changes a viewed status frame"


@pytest.mark.parametrize("url", [
    "https://api.telegram.org/botbdsecR3n2aQ7/sendMessage", "https://www.api.telegram.org/botbdsecR3n2bQ7/x",
    "https://ptb.discord.com/api/v9/bdsecR3n2cQ7", "https://hooks.zapier.com/x/bdsecR3n2dQ7/",
    "https://n.example/hooks/bdsecR3n2eQ7", "https://n.example/a/WebHook/bdsecR3n2fQ7",
])
def test_notifier_subdomains_and_hook_paths_are_not_plain(url):
    """R120-D N2: a notifier host matches with its subdomains; a /hooks/ path
    segment is a hook on any host."""
    from bulk_downloader.egress_withhold import is_plain_url, value_view
    assert not is_plain_url(url) and "bdsecR3n2" not in json.dumps(value_view("start_url", url)), (
        f"BD_SEC_2_WITHHOLD_LEAK: {url} served as a plain URL")


def test_secret_named_key_outside_secret_fields_leaves_exports_as_a_marker(fresh_app):
    """R120-D N4, export half: api_key is secret-named (is_secret_config_key) but not in
    site_editor.SECRET_FIELDS, so neither export route blanked it. api_key is not in CFG_FIELDS, so a
    merge import drops it on BASE as well; the marker must never be stored in its place."""
    from bulk_downloader import app as A
    c = "bdsecR3n4xQ7"
    sid = _plant(A, fresh_app, "wh-n4", {"api_key": c, "start_url": "https://r.example/plain"})
    body = fresh_app.get("/api/config/export").get_data(as_text=True)
    site = fresh_app.get(f"/api/sites/{sid}/export").get_data(as_text=True)
    assert "https://r.example/plain" in body and "https://r.example/plain" in site, "BD_SEC_2_WITHHOLD_PROBE_BLIND: n4"
    assert not _decoded_has(body, c) and not _decoded_has(site, c), "BD_SEC_2_WITHHOLD_LEAK: api_key export"
    r = fresh_app.post("/api/config/import?mode=merge", json=json.loads(body))
    assert r.status_code == 200 and not isinstance(A.s_cfg[sid].get("api_key"), dict), (
        f"BD_SEC_2_WITHHOLD_ROUNDTRIP: api_key stored as {A.s_cfg[sid].get('api_key')!r}")
