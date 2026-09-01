#!/usr/bin/env python3
"""Configure every site for production through BD's own API.

MEASURED VALUES ONLY. Where tonight's runs established a fact -- the Gamma
family's download trigger and row selectors, evilangel's and dfxtra's real
quality ladders, the upsell interstitial's dismiss control -- it is set. Where
nothing was measured, the field is left at the app's default and REPORTED as
needing operator input, rather than filled with a plausible guess. A confident
wrong selector is worse than an empty one: it fails silently and looks configured.

Every download_dir is created and verified to exist under an allowlisted root,
because validate_download_dir refuses anything else and a missing directory is a
download that goes nowhere.
"""
import json, os, pathlib, sys, urllib.error, urllib.request

TOKEN = os.environ["BD_TOKEN"]
BASE = "http://127.0.0.1:5555"
HOME = pathlib.Path.home()
DL_ROOT = HOME / "Downloads" / "bulk_downloader"

# MEASURED on live authenticated pages tonight. Empty means not measured.
GAMMA_TRIGGER = "[class*='ScenePlayerHeaderPlus-IconItem']"
GAMMA_ROWS = "a[class*='DownloadOption']"
GAMMA_DISMISS = "a:has-text('No Thanks')"

SITES = {
    # site        base url                                login url                                   qpref (measured)      family
    "evilangel":  ("https://members.evilangel.com/en",    "https://www.evilangel.com/en/login",       "2160,1080,720,540,480,360,240,160", "gamma"),
    "adulttime":  ("https://members.adulttime.com/en",    "https://www.adulttime.com/en/login",       "best",                "gamma"),
    "dfxtra":     ("https://www.dfxtra.com/en",           "https://www.dfxtra.com/en/login",          "2160,1080,720,576,432,288", "gamma"),
    "bang":       ("https://www.bang.com",                "https://www.bang.com/login",               "best",                ""),
    "bangbros":   ("https://site-ma.bangbros.com",        "https://site-ma.bangbros.com/login",       "best",                ""),
    "brazzers":   ("https://site-ma.brazzers.com",        "https://site-ma.brazzers.com/login",       "best",                ""),
    "naughtyamerica": ("https://members.naughtyamerica.com", "https://www.naughtyamerica.com/login",  "best",                ""),
    "nubiles_porn": ("https://members.nubiles-porn.com",  "https://members.nubiles-porn.com/login",   "best",                ""),
    "nubilefilms": ("https://members.nubilefilms.com",    "https://members.nubilefilms.com/login",    "best",                ""),
    "vixenplus":  ("https://www.vixenplus.com",           "https://www.vixenplus.com/login",          "best",                ""),
    "vip4k":      ("https://vip4k.com/en",                "https://vip4k.com/en/login",               "best",                ""),
    "wowgirls":   ("https://venus.wowgirls.com",          "https://auth.wowgirls.com/login",          "best",                ""),
    "reptyle":    ("https://app.reptyle.com",             "https://app.reptyle.com/login",            "best",                ""),
    "pegasproductions": ("https://www.pegasproductions.com", "https://www.pegasproductions.com/login","best",                ""),
    "kink":       ("https://www.kink.com",                "https://www.kink.com/login",               "best",                ""),
    "nookies":    ("https://nookies.com",                 "https://nookies.com/login",                "best",                ""),
    "tiny4k":     ("https://tiny4k.com",                  "https://tiny4k.com/login",                 "best",                ""),
    "ultrafilms": ("https://ultrafilms.com",              "https://ultrafilms.com/login",             "best",                ""),
    "teenmegaworld": ("https://members.teenmegaworld.net","https://teenmegaworld.net/login",          "best",                ""),
    "kellymadison": ("https://members.kellymadisonmedia.com","https://members.kellymadisonmedia.com/login","best",            ""),
}
# login_url was VERIFIED only for evilangel (a real form was filled and rejected
# there). The rest follow the site's own pattern and are UNVERIFIED.
VERIFIED_LOGIN = {"evilangel"}
JAR_ALIAS = {"bang": "bang_com", "nubiles_porn": "nubiles_porn_com",
             "kellymadison": "kellymadisonmedia_com", "teenmegaworld": "teenmegaworld_net"}


def call(path, payload=None, method="POST"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {TOKEN}"})
    def _body(raw):
        # A NON-JSON BODY IS AN ANSWER TOO. An HTML 404/405 decoded as JSON
        # raises and hides which endpoint was actually wrong.
        try:
            return json.loads(raw.decode("utf-8", "replace") or "{}")
        except Exception:
            return {"_raw": raw.decode("utf-8", "replace")[:120]}
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, _body(r.read())
    except urllib.error.HTTPError as e:
        return e.code, _body(e.read())


def main():
    _, sec = call("/api/secrets/status", method="GET")
    keys = sec.get("stored_keys") or []
    _, existing = call("/api/sites_list", method="GET")
    have = {row.get("name"): row.get("site_id")
            for row in ((existing or {}).get("sites") or [])
            if isinstance(row, dict)}

    made, flagged = 0, []
    for site, (base, login, qpref, family) in SITES.items():
        key = next((k for k in keys if f"-site-{site.replace('_','-')}-" in k
                    or f"-site-{site}-" in k), None)
        jar = HOME / "BulkDownloader" / "cookies" / f"{JAR_ALIAS.get(site, site + '_com')}.json"
        dl = DL_ROOT / site
        dl.mkdir(parents=True, exist_ok=True)

        cfg = {
            "name": site,
            "login_url": login,
            "cookie_file": str(jar) if jar.is_file() else "",
            "download_dir": str(dl),
            "quality_preference": qpref,
            "min_resolution": 720,
            "headless": True,
            "max_concurrent": 2,
            "delay": 3,
            "max_retries": 2,
            "skip_if_exists": True,
            "verify_integrity": True,
            "use_curl_cffi": True,
            "use_stealth": True,
            "use_real_chrome": True,
            "use_persistent_profile": True,
            "auto_relogin_enabled": bool(key),
            "auto_relogin_interval_hours": 12,
            "sched_enabled": False,
        }
        if key:
            cfg["password"] = f"@cred:{key}"
        if family == "gamma":
            cfg.update({"trigger_selector": GAMMA_TRIGGER,
                        "dl_selector": GAMMA_ROWS,
                        "dismiss_selectors": GAMMA_DISMISS})
        sid = have.get(site)
        if sid:
            st, body = call(f"/api/sites/{sid}", cfg, method="PUT")
        else:
            st, body = call("/api/sites", cfg, method="POST")
            sid = body.get("site_id") or body.get("id") or "?"
        ok = st in (200, 201) and body.get("ok", True)
        made += 1 if ok else 0
        notes = []
        if not key:
            notes.append("NO CREDENTIAL")
        if not jar.is_file():
            notes.append("no cookie jar")
        if site not in VERIFIED_LOGIN:
            notes.append("login_url UNVERIFIED")
        if qpref == "best":
            notes.append("quality ladder unmeasured")
        if family != "gamma":
            notes.append("selectors unmeasured")
        if notes:
            flagged.append((site, notes))
        print(f"{site:18} HTTP {st} {'ok' if ok else 'FAILED'} "
              f"cred={'yes' if key else 'NO':3} jar={'yes' if jar.is_file() else 'no':3} "
              f"{str(body.get('error') or '')[:44]}")

    print(f"\nconfigured {made}/{len(SITES)} site(s); download dirs created under {DL_ROOT}")
    print("\nNEEDS OPERATOR INPUT OR MEASUREMENT (not guessed):")
    for site, notes in flagged:
        print(f"   {site:18} {', '.join(notes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
