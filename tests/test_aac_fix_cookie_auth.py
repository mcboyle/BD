"""aac-fix (O1789 bughunt adapters-auth-cookies, finder bd-worker-S35-B): F1-F12.

Cookie paste -> jar (cookie_clipboard + /api/cookie_clipboard/save), the site auth endpoints (app_sites_auth) and
the cookie_file trust gap. Every test is a FINDINGS repro (RED at BASE) plus the legitimate case it must keep.

Real Flask test client on the isolated BD_HOME the conftest autouse fixture provides; no network, no browser.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

BD_GATE_SCOPE = "module"
pytestmark = pytest.mark.bd_module_wipe

FUTURE = int(time.time()) + 400 * 86400
GOOD_JAR = [
    {
        "name": "keep",
        "value": "me",
        "domain": "www.example.test",
        "path": "/",
        "secure": True,
        "httpOnly": False,
        "sameSite": "Lax",
        "expirationDate": FUTURE,
    }
]


@pytest.fixture(scope="module", autouse=True)
def _restore_bd_modules_after_file():
    """Put the bulk_downloader module table back when this file finishes (1034 leaker census)."""
    saved = {
        m: mod
        for m, mod in sys.modules.items()
        if m == "bulk_downloader" or m.startswith("bulk_downloader.")
    }
    try:
        yield
    finally:
        for m in [
            m
            for m in sys.modules
            if m == "bulk_downloader" or m.startswith("bulk_downloader.")
        ]:
            del sys.modules[m]
        sys.modules.update(saved)


def _setup(login_url="https://www.example.test/login", jar=GOOD_JAR, name="AacSite"):
    for mod in list(sys.modules):
        if mod.startswith("bulk_downloader"):
            del sys.modules[mod]
    from bulk_downloader import app as a

    c = a.app.test_client()
    jar_path = Path(os.environ["BD_HOME"]) / "aac" / f"{name}-{time.time_ns()}.json"
    jar_path.parent.mkdir(parents=True, exist_ok=True)
    if jar is not None:
        jar_path.write_text(json.dumps(jar), encoding="utf-8")
    body = {"name": name, "cookie_file": str(jar_path)}
    if login_url:
        body["login_url"] = login_url
    resp = c.post("/api/sites", json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return a, c, resp.get_json()["id"], jar_path


def _save(c, sid, text):
    return c.post(f"/api/cookie_clipboard/save/{sid}", json={"text": text})


def _jar(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _cc():
    from bulk_downloader import cookie_clipboard as cc

    return cc


# ── F1 F9: a pasted cookie keeps its expiry and reaches the runner ───────────
def test_f1_f9_pasted_expiry_survives_reload_and_the_runner_sees_it():
    a, c, sid, jar = _setup()
    from bulk_downloader import cookies as ck

    a.runners[sid].cookies = []
    paste = json.dumps(
        [
            {
                "name": "sid",
                "value": "v1",
                "domain": ".example.test",
                "path": "/",
                "expirationDate": FUTURE,
                "secure": True,
            }
        ]
    )
    resp = _save(c, sid, paste)
    assert resp.status_code == 200, resp.get_json()
    info = ck.cookies_expiry_info(ck.load_cookies_from_file(jar))
    assert info["earliest"] == FUTURE and info["session"] == 0, (
        f"F1: reloaded as {info}"
    )
    assert [x["name"] for x in a.runners[sid].cookies] == ["sid"], (
        "F9: save never reached the runner"
    )


def test_f9_cookies_import_reaches_the_runner():
    a, c, sid, _ = _setup()
    a.runners[sid].cookies = []
    resp = c.post(
        f"/api/sites/{sid}/cookies/import", json={"text": json.dumps(GOOD_JAR)}
    )
    assert resp.status_code == 200, resp.get_json()
    assert [x["name"] for x in a.runners[sid].cookies] == ["keep"], (
        "F9: import never reached the runner"
    )


# ── F2: a half-matched paste must not replace a good jar ─────────────────────
def test_f2_curl_header_paste_parses_its_cookies_not_the_command():
    _a, c, sid, jar = _setup()
    curl = (
        "curl 'https://www.example.test/api?x=1' -H 'accept: */*' "
        "-H 'cookie: q=1; v=2; theme=dark' --compressed"
    )
    resp = _save(c, sid, curl)
    assert resp.status_code == 200, resp.get_json()
    assert [x["name"] for x in _jar(jar)] == ["q", "v", "theme"], _jar(jar)


@pytest.mark.parametrize(
    "paste",
    [
        "curl 'https://www.example.test/api?x=1;y=2' -H 'accept: */*'",
        '{"origins": [], "note": "a=b; c=d"}',
    ],
)
def test_f2_unparseable_structured_paste_leaves_the_jar(paste):
    _a, c, sid, jar = _setup()
    resp = _save(c, sid, paste)
    assert resp.status_code == 400, (resp.status_code, resp.get_json())
    assert _jar(jar) == GOOD_JAR


def test_f2_storage_state_object_parses_as_cookies():
    state = {
        "cookies": [
            {
                "name": "sid",
                "value": "x",
                "domain": ".example.test",
                "path": "/",
                "expires": FUTURE,
            }
        ],
        "origins": [],
    }
    got = _cc().auto_detect_and_parse(json.dumps(state))
    assert [(x["name"], x["expires"]) for x in got["cookies"]] == [("sid", FUTURE)], got


# ── F3 F4: domainless and all-dropped pastes ─────────────────────────────────
def test_f3_header_paste_takes_the_site_host():
    _a, c, sid, jar = _setup()
    assert _save(c, sid, "sid=abc; theme=dark").status_code == 200
    assert {x.get("domain") for x in _jar(jar)} == {"www.example.test"}, _jar(jar)


def test_f3_domainless_paste_without_a_site_url_is_refused():
    _a, c, sid, jar = _setup(login_url="")
    resp = _save(c, sid, "sid=abc; theme=dark")
    assert resp.status_code == 400 and "no domain" in resp.get_json()["error"], (
        resp.get_json()
    )
    assert _jar(jar) == GOOD_JAR


def test_f4_paste_whose_every_cookie_is_unsafe_keeps_the_jar():
    _a, c, sid, jar = _setup()
    resp = _save(
        c, sid, json.dumps([{"name": "sid", "value": "a\nb", "domain": "example.test"}])
    )
    assert resp.status_code == 400, resp.get_json()
    assert _jar(jar) == GOOD_JAR


# ── F5 F6 F7 F8: parser / converter details ──────────────────────────────────
def test_f5_no_restriction_maps_to_samesite_none_on_a_secure_cookie():
    cc = _cc()
    sec = cc.to_playwright_format(
        [{"name": "a", "value": "b", "secure": True, "sameSite": "no_restriction"}]
    )
    plain = cc.to_playwright_format(
        [{"name": "a", "value": "b", "secure": False, "sameSite": "no_restriction"}]
    )
    assert (sec[0]["sameSite"], plain[0]["sameSite"]) == ("None", "Lax")


def test_f6_header_and_curl_and_netscape_details():
    cc = _cc()
    names = lambda t: [
        (x["name"], x["value"]) for x in cc.auto_detect_and_parse(t)["cookies"]
    ]
    assert names("Cookie: sid=abc; theme=dark") == [("sid", "abc"), ("theme", "dark")]
    assert names("sid=abc") == [("sid", "abc")]
    assert names("a=; b=2") == [("a", ""), ("b", "2")]
    assert names("""curl 'https://www.example.test/' -b 'k="v"; z=1'""") == [
        ("k", '"v"'),
        ("z", "1"),
    ]
    rows = cc._parse_netscape(
        "#HttpOnly_.example.test\tTRUE\t/\tTRUE\t1794494938.5\tsid\tv\n"
    )
    assert (rows[0]["httpOnly"], rows[0]["expires"]) == (True, 1794494938), rows


def test_f7_bad_json_fields_and_non_string_text():
    cc = _cc()
    got = cc.auto_detect_and_parse(
        json.dumps(
            [
                {"name": "sid", "value": "x", "expirationDate": "Session"},
                {"name": None, "value": "y"},
                {"name": "n", "value": 123},
            ]
        )
    )
    assert [(x["name"], x["value"], x["expires"]) for x in got["cookies"]] == [
        ("sid", "x", 0),
        ("n", "123", 0),
    ]
    _a, c, sid, _ = _setup()
    for route in ("/api/cookie_clipboard/parse", f"/api/cookie_clipboard/save/{sid}"):
        resp = c.post(route, json={"text": 5})
        assert resp.status_code == 400, (
            route,
            resp.status_code,
            resp.get_data(as_text=True)[:200],
        )
        assert resp.get_json()["error"] == "text must be a string", resp.get_json()


def test_f8_to_netscape_text_domains_and_missing_expiry():
    text = _cc().to_netscape_text(
        [
            {"name": "a", "value": "b", "domain": "www.example.test", "expires": None},
            {"name": "c", "value": "d", "domain": ".example.test"},
            {"name": "e", "value": "f"},
        ]
    )
    rows = [
        ln.split("\t")[:2] + [ln.split("\t")[5]]
        for ln in text.splitlines()
        if not ln.startswith("#")
    ]
    assert rows == [
        ["www.example.test", "FALSE", "a"],
        [".example.test", "TRUE", "c"],
    ], text


# ── F10: wrong answers on the account endpoints ──────────────────────────────
def test_f10_pool_reset_of_an_unknown_index_is_a_404():
    _a, c, sid, _ = _setup()
    from bulk_downloader import account_pool as ap

    assert c.post(f"/api/sites/{sid}/account_pool/reset/999").status_code == 404
    ap.configure_pool(sid, [{"username": "u1", "password": "p1"}])
    assert (
        c.post(f"/api/sites/{sid}/account_pool/reset/0").status_code == 200
    )  # control


def test_f10_reset_cooldown_needs_true_and_a_whole_index():
    a, c, sid, _ = _setup()
    later = time.time() + 3600
    a.s_cfg[sid]["accounts"] = [
        {"username": "u1", "cooldown_until": later},
        {"username": "u2", "cooldown_until": later},
    ]
    for body in ({"all": "false"}, {"account_index": 0.9}):
        resp = c.post(f"/api/sites/{sid}/accounts/reset_cooldown", json=body)
        assert resp.status_code == 400, (body, resp.get_json())
    assert [x["cooldown_until"] for x in a.s_cfg[sid]["accounts"]] == [later, later]
    ok = c.post(
        f"/api/sites/{sid}/accounts/reset_cooldown", json={"account_index": 1}
    )  # control
    assert ok.get_json()["cleared_indices"] == [1]


def test_f10_accounts_get_and_export_never_500():
    a, c, sid, jar = _setup()
    a.s_cfg[sid]["accounts"] = [{"username": "u1", "cooldown_until": "soon"}]
    resp = c.get(f"/api/sites/{sid}/accounts")
    assert (
        resp.status_code == 200
        and resp.get_json()["accounts"][0]["cooldown_until"] == 0.0
    )
    Path(jar).write_bytes(b'[{"name": "a", "value": "\xff"}]')
    assert c.get(f"/api/sites/{sid}/cookies/export").status_code == 409


# ── F11: only a surfaced workflow can be decided ─────────────────────────────
def test_f11_post_reveal_decision_needs_a_surfaced_workflow():
    a, c, sid, _ = _setup()
    route = f"/api/sites/{sid}/post_reveal_decision"
    resp = c.post(
        route,
        json={
            "action_url": "https://evil.example.test/never-seen",
            "decision": "approve",
        },
    )
    assert resp.status_code == 404, resp.get_json()
    assert not (
        ((a.s_cfg[sid].get("learned") or {}).get("deep_detect") or {}).get(
            "post_reveal_decisions"
        )
    )
    a.s_cfg[sid].setdefault("learned", {})["deep_detect"] = {
        "pending_approvals": {
            "post_reveal|www.example.test/reveal": {
                "surface": "post_reveal",
                "key": "www.example.test/reveal",
            }
        }
    }
    for decision in (
        "approve",
        "decline",
    ):  # control: the surfaced key, and a re-decision
        ok = c.post(
            route, json={"action_url": "www.example.test/reveal", "decision": decision}
        )
        assert ok.status_code == 200, ok.get_json()


# ── F12: cookie_file is only ever read or replaced when it is a jar ──────────
def test_f12_export_and_save_refuse_a_cookie_file_that_is_not_a_jar():
    _a, c, sid, _ = _setup()
    assert (
        c.get(f"/api/sites/{sid}/cookies/export").status_code == 200
    )  # control: a real jar
    secret = Path(os.environ["BD_HOME"]) / "aac" / f"not-a-jar-{time.time_ns()}.json"
    secret.write_text(
        json.dumps({"vault": "TOP-SECRET-MARKER", "n": 1}), encoding="utf-8"
    )
    put = c.put(f"/api/sites/{sid}", json={"cookie_file": str(secret)})
    assert put.status_code == 200, put.get_data(as_text=True)[:200]
    exp = c.get(f"/api/sites/{sid}/cookies/export")
    assert exp.status_code == 409 and "TOP-SECRET-MARKER" not in exp.get_data(
        as_text=True
    )
    assert _save(c, sid, "sid=abc").status_code == 409
    assert "TOP-SECRET-MARKER" in secret.read_text(encoding="utf-8")


def test_f12_login_verify_member_url_must_be_http():
    a, c, sid, _ = _setup()
    calls = []
    # never a browser here: the runner's verify is a recorder
    a.runners[sid].verify_login_after_wizard = lambda member_url="": (
        calls.append(member_url) or {}
    )
    for bad in ("file:///etc/hostname", "javascript:alert(1)", "//www.example.test/x"):
        resp = c.post(f"/api/sites/{sid}/login_verify", json={"member_url": bad})
        assert resp.status_code == 400, (bad, resp.status_code)
    assert calls == [], f"F12: member_url reached the browser probe: {calls}"
    ok = c.post(
        f"/api/sites/{sid}/login_verify",
        json={"member_url": "https://www.example.test/m"},
    )
    assert ok.status_code == 200 and calls == ["https://www.example.test/m"]  # control


# ── r2 (lens D170-D): R1 one jar test, R2 browser cookie names, R3 no values in a refusal ──
STATE_WITH_ORIGINS = {
    "cookies": [
        {
            "name": "sid",
            "value": "v1",
            "domain": "www.example.test",
            "path": "/",
            "expires": FUTURE,
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
        }
    ],
    "origins": [
        {
            "origin": "https://www.example.test",
            "localStorage": [{"name": "k", "value": "v"}],
        }
    ],
}


@pytest.mark.parametrize(
    "doc",
    [STATE_WITH_ORIGINS, {"saved_at": 123, "cookies": GOOD_JAR}],
    ids=["storage_state_origins", "dict_with_meta"],
)
def test_r2_r1_a_jar_import_accepts_is_exported_and_replaced_by_a_save(doc):
    """D170 R1: import 200, then export 409 / login save CookieFileNotAJarError / clipboard save 409 on r1."""
    a, c, sid, _ = _setup()
    r = c.post(f"/api/sites/{sid}/cookies/import", json={"text": json.dumps(doc)})
    assert r.status_code == 200, r.get_json()
    cf = a.s_cfg[sid]["cookie_file"]
    from bulk_downloader import cookies as ck

    # the storage_state origins entry is not read as a (nameless) cookie
    assert [x["name"] for x in ck.load_cookies_from_file(cf)] == [
        x["name"] for x in doc["cookies"]
    ]
    ex = c.get(f"/api/sites/{sid}/cookies/export")
    assert ex.status_code == 200 and json.loads(ex.get_data(as_text=True)) == doc
    ck.save_cookies_to_file(
        cf,
        [
            {
                "name": "fresh",
                "value": "login",
                "domain": "www.example.test",
                "path": "/",
            }
        ],
    )
    assert [x["name"] for x in _jar(cf)] == ["fresh"]
    assert _save(c, sid, "sid=abc; theme=dark").status_code == 200
    assert [x["name"] for x in _jar(cf)] == ["sid", "theme"]


@pytest.mark.parametrize(
    "doc",
    [
        {"accounts": [{"user": "u", "password": "p"}]},
        [{"name": "a", "value": "b"}, {"user": "u"}],
        {"cookies": [{"name": "a", "value": "b"}], "sites": [{"id": 1}]},
    ],
    ids=["config_like", "list_with_a_non_cookie", "cookies_plus_other_list"],
)
def test_r2_r1_import_refuses_what_export_and_the_savers_refuse(doc):
    """The one jar test also gates import: a document that is not a jar never becomes the site's cookie file."""
    a, c, sid, jar = _setup()
    r = c.post(f"/api/sites/{sid}/cookies/import", json={"text": json.dumps(doc)})
    assert r.status_code == 400 and "not a cookie jar" in r.get_json()["error"], (
        r.get_json()
    )
    assert a.s_cfg[sid]["cookie_file"] == str(jar) and _jar(jar) == GOOD_JAR


BROWSER_NAMES = [
    "sid",
    "cart[1]",
    "user[id]",
    "a:b",
    "x@y",
    "AMCV_X@AdobeOrg",
    "",
    "na me",
    "q(1)",
    "k/v",
    "{t}",
]
REFUSED_NAMES = [" lead", "trail ", "a;b", "a=b", "bad\x01", "bad\x7f", "tab\tx"]


def test_r2_r2_json_paste_keeps_every_name_a_browser_keeps_and_names_the_rest():
    """D170 R2 (11 names: BASE 11, r1 1). The refused set is what Chromium's add_cookies refuses (r2 oracle)."""
    rows = [
        {"name": n, "value": "v", "domain": ".example.test", "path": "/"}
        for n in BROWSER_NAMES + REFUSED_NAMES
    ]
    got = _cc().auto_detect_and_parse(json.dumps(rows))
    assert [x["name"] for x in got["cookies"]] == BROWSER_NAMES, got["cookies"]
    assert [(s["name"], s["why"]) for s in got["skipped"]] == [
        (n.encode("unicode_escape").decode("ascii"), "name a browser refuses")
        for n in REFUSED_NAMES
    ]


@pytest.mark.parametrize(
    "paste,fmt",
    [
        ("sid=1; cart[1]=2; user[id]=3; a:b=4; q(1)=5; x@y=6", "cookie_header"),
        (
            "curl 'https://x.test/' -b 'sid=1; cart[1]=2; user[id]=3; a:b=4; q(1)=5; x@y=6'",
            "curl",
        ),
        (
            "curl 'https://x.test/' -H 'cookie: sid=1; cart[1]=2; user[id]=3; a:b=4; q(1)=5; x@y=6'",
            "curl",
        ),
        (
            "# Netscape HTTP Cookie File\n"
            + "".join(
                f".x.test\tTRUE\t/\tFALSE\t0\t{n}\t{i}\n"
                for i, n in enumerate(
                    ["sid", "cart[1]", "user[id]", "a:b", "q(1)", "x@y"], 1
                )
            ),
            "netscape",
        ),
    ],
    ids=["header", "curl_b", "curl_H", "netscape"],
)
def test_r2_r2_every_paste_format_keeps_the_same_names(paste, fmt):
    got = _cc().auto_detect_and_parse(paste)
    assert got["format"] == fmt and [x["name"] for x in got["cookies"]] == [
        "sid",
        "cart[1]",
        "user[id]",
        "a:b",
        "q(1)",
        "x@y",
    ], got
    assert got["skipped"] == []


def test_r2_r2_a_save_names_the_cookies_it_left_out():
    _a, c, sid, jar = _setup()
    rows = [
        {"name": "cart[1]", "value": "2", "domain": "www.example.test"},
        {"name": "a;b", "value": "SECRETVAL5", "domain": "www.example.test"},
    ]
    r = _save(c, sid, json.dumps(rows))
    assert r.status_code == 200 and [x["name"] for x in _jar(jar)] == ["cart[1]"], (
        r.get_json()
    )
    assert r.get_json()["skipped"] == [{"name": "a;b", "why": "name a browser refuses"}]
    assert "SECRETVAL5" not in r.get_data(as_text=True)


@pytest.mark.parametrize(
    "paste,status_field",
    [
        ("sid=SECRETVAL1\nx; b=SECRETVAL2\rq", "control character in a field"),
        (
            json.dumps(
                [
                    {"name": "a=b", "value": "SECRETVAL3"},
                    {"name": " c", "value": "SECRETVAL4"},
                ]
            ),
            "name a browser refuses",
        ),
    ],
    ids=["ctl_values_f4", "refused_names_only"],
)
def test_r2_r3_a_refusal_names_cookies_and_never_echoes_a_value(paste, status_field):
    """D170 R3: the F4 400 echoed the pasted values ("details": the parsed cookies). Names / count / why only."""
    _a, c, sid, jar = _setup()
    r = _save(c, sid, paste)
    body = r.get_data(as_text=True)
    assert r.status_code == 400 and "SECRETVAL" not in body, body[:300]
    det = r.get_json()["details"]
    assert det["skipped"] and all(s["why"] == status_field for s in det["skipped"]), det
    assert _jar(jar) == GOOD_JAR
