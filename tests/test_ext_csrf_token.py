"""ext-csrf-token r1 -- the extension's X-BD-Token POSTs pass the CSRF Origin rule.

FINDINGS extension-bridge F1 (S57-B): the extension's URL-queue calls send only
X-BD-Token from Origin chrome-extension://<id>, and _check_csrf refused every one
("cross-origin request refused"). PM spec ANSWER-pm-B-sweep-rows-1252Z "ext-bridge F1:
Option 1": _check_csrf exempts a request carrying a VALID X-BD-Token (the same _token_eq
as _check_token, over every accepted token); an invalid token is still CSRF-checked.

Auth set (S2): a valid token passes on quick_add, route_urls, route_preview and
sites/<id>/queue_url; a missing or wrong token is refused (by the token gate, 401,
which runs first). Auth unset (S3, PM ANSWER-pm-B-batch-1440Z item 1): the PRESENCE of a
non-blank X-BD-Token exempts; without one the extension-Origin POST stays 403. That rests
on the preflight: a foreign Origin is never granted X-BD-Token (no Access-Control-Allow-
Headers grant), and the source holds exactly one Allow-Headers setter that never lists it.
Same-origin and no-Origin controls pass.

Tokens are random canaries; failure messages carry statuses and counts only.
"""
# The subject is the _check_csrf gate in app.py, not the tree.
BD_GATE_SCOPE = "module"

import importlib
import json
import re
import secrets
import threading
from pathlib import Path

import pytest

HOST = "127.0.0.1:5000"
EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
SAME = "http://127.0.0.1:5000"
EVIL = "https://evil.test"
XO = "cross-origin request refused"

ROUTES = [
    ("/api/quick_add", {"url": "https://example.com/v/3"}),
    ("/api/route_urls", {"text": "https://example.com/v/2", "ai_filter": True}),
    ("/api/route_preview", {"url": "https://example.com/v/2"}),
    ("/api/sites/siteA/queue_url", {"url": "https://example.com/v/4"}),
]
ROUTE_IDS = ["quick_add", "route_urls", "route_preview", "queue_url"]


def _mod(name):
    return importlib.import_module(f"bulk_downloader.{name}")


class _FakeRunner:
    def __init__(self):
        self.jobs = {}
        self.urls = []
        self._lock = threading.Lock()
        self.loaded = []

    def load_urls(self, urls):
        self.loaded.extend(urls)
        return len(urls), 0


@pytest.fixture
def env(monkeypatch):
    """One site (siteA, example.com) with an in-memory runner; auth OFF until set."""
    A = _mod("app")
    st = _mod("app_state")
    A.app.config["TESTING"] = True
    saved = (dict(st.s_cfg), dict(st.runners))
    st.s_cfg.clear()
    st.s_cfg.update({"siteA": {"name": "Site A", "url_patterns": "example\\.com",
                               "login_url": "https://example.com/login"}})
    st.runners.clear()
    st.runners["siteA"] = _FakeRunner()

    def auth(primary="", secondary=""):
        monkeypatch.setattr(A, "_expected_token", lambda: primary)
        monkeypatch.setattr(A, "_bd_token_secondary", lambda: secondary)

    auth()
    yield A, auth
    st.s_cfg.clear()
    st.s_cfg.update(saved[0])
    st.runners.clear()
    st.runners.update(saved[1])


def _post(A, path, body, origin=None, token=None):
    hx = {"Host": HOST, "Content-Type": "application/json"}
    if origin:
        hx["Origin"] = origin
    if token is not None:
        hx["X-BD-Token"] = token
    r = A.app.test_client().post(path, data=json.dumps(body), headers=hx)
    err = (r.get_json(silent=True) or {}).get("error") if r.is_json else None
    return r.status_code, err


def _passed(status, err):
    return status not in (401, 403) and err != XO


# ── S2 (auth set): a valid X-BD-Token from the extension Origin passes ───────

@pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)
def test_ext_origin_valid_primary_token_passes(env, path, body):
    A, auth = env
    tok = secrets.token_hex(16)
    auth(primary=tok)
    ctl = _post(A, path, body, token=tok)
    got = _post(A, path, body, origin=EXT, token=tok)
    assert _passed(*ctl), f"ECT-CONTROL no-Origin control refused status={ctl[0]}"
    assert _passed(*got) and got[0] == ctl[0], (
        f"ECT-VALID-PRIMARY ext-Origin + valid X-BD-Token refused: status={got[0]} "
        f"xo={got[1] == XO} control={ctl[0]}")


@pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)
def test_ext_origin_valid_secondary_token_passes(env, path, body):
    """The secondary accepted token (BD_TOKEN) is as valid as the primary, as in _check_token."""
    A, auth = env
    pri, sec = secrets.token_hex(16), secrets.token_hex(16)
    auth(primary=pri, secondary=sec)
    got = _post(A, path, body, origin=EXT, token=sec)
    assert _passed(*got), (
        f"ECT-VALID-SECONDARY ext-Origin + valid secondary X-BD-Token refused: status={got[0]} "
        f"xo={got[1] == XO}")


@pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)
def test_auth_set_missing_or_wrong_token_refused(env, path, body):
    A, auth = env
    auth(primary=secrets.token_hex(16))
    st = _mod("app_state")
    before = len(st.runners["siteA"].loaded)
    none = _post(A, path, body, origin=EXT)
    wrong = _post(A, path, body, origin=EXT, token=secrets.token_hex(16))
    assert none[0] == 401 and wrong[0] == 401, (
        f"ECT-S2-REFUSE ext-Origin without / with a wrong token: statuses={none[0]},{wrong[0]} "
        f"(expected 401 from the token gate)")
    assert len(st.runners["siteA"].loaded) == before, "ECT-S2-REFUSE a refused request queued URLs"


@pytest.mark.parametrize("which", ["wrong", "empty", "ws"])
def test_invalid_token_still_origin_checked_auth_set(env, which):
    """Called directly (as after a passing session cookie): an invalid X-BD-Token must not
    exempt the request from the Origin rule."""
    A, auth = env
    auth(primary=secrets.token_hex(16))
    tok = {"wrong": secrets.token_hex(16), "empty": "", "ws": "   "}[which]
    with A.app.test_request_context("/api/quick_add", method="POST",
                                     headers={"Host": HOST, "Origin": EXT, "X-BD-Token": tok}):
        r = A._check_csrf()
    status = r[1] if isinstance(r, tuple) else None
    assert status == 403, f"ECT-INVALID-EXEMPT invalid X-BD-Token ({which}) skipped the Origin rule: status={status}"


# ── S3 (auth unset): X-BD-Token presence exempts; no token -> 403 ────────────

@pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)
def test_auth_unset_token_presence_exempts(env, path, body):
    A, _auth = env
    ctl = _post(A, path, body)
    got = _post(A, path, body, origin=EXT, token=secrets.token_hex(16))
    assert _passed(*ctl), f"ECT-CONTROL auth unset: no-Origin control refused status={ctl[0]}"
    assert _passed(*got) and got[0] == ctl[0], (
        f"ECT-S3-PRESENCE auth unset: ext-Origin + X-BD-Token refused: status={got[0]} "
        f"xo={got[1] == XO} control={ctl[0]}")


@pytest.mark.parametrize("which", ["none", "empty", "ws"])
@pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)
def test_auth_unset_no_token_still_refused_403(env, path, body, which):
    """PM pin: 403 with auth unset (401 is the auth-set pin, test_auth_set_missing_or_wrong_token_refused)."""
    A, _auth = env
    st = _mod("app_state")
    before = len(st.runners["siteA"].loaded)
    got = _post(A, path, body, origin=EXT, token={"none": None, "empty": "", "ws": "   "}[which])
    assert got == (403, XO), (
        f"ECT-S3-NOTOKEN auth unset: ext-Origin with {which} X-BD-Token -> status={got[0]} "
        f"xo={got[1] == XO} (expected 403 cross-origin)")
    assert len(st.runners["siteA"].loaded) == before, "ECT-S3-NOTOKEN a refused request queued URLs"


# ── controls: same-origin and no-Origin pass in both modes; evil Origin refused ─

@pytest.mark.parametrize("mode", ["auth_set", "auth_unset"])
@pytest.mark.parametrize(("path", "body"), ROUTES, ids=ROUTE_IDS)
def test_same_origin_and_no_origin_controls(env, path, body, mode):
    A, auth = env
    tok = None
    if mode == "auth_set":
        tok = secrets.token_hex(16)
        auth(primary=tok)
    no_o = _post(A, path, body, token=tok)
    same = _post(A, path, body, origin=SAME, token=tok)
    assert _passed(*no_o) and _passed(*same), (
        f"ECT-CONTROL {mode}: no-Origin={no_o[0]} same-origin={same[0]}")
    evil = _post(A, path, body, origin=EVIL)
    assert evil[0] in (401, 403), f"ECT-CONTROL {mode}: evil Origin, no token -> {evil[0]}"


# ── CORS: no response allows X-BD-Token to a foreign Origin (PM 1440Z test a) ─

def test_foreign_origin_preflight_with_x_bd_token_not_granted(env):
    A, _auth = env
    c = A.app.test_client()
    pre = {"Host": HOST, "Origin": EVIL, "Access-Control-Request-Method": "POST",
           "Access-Control-Request-Headers": "content-type, x-bd-token"}
    paths = [p for p, _b in ROUTES] + ["/api/sites/siteA/teach_verify"]
    allow_tok, reflect = [], []
    seen_headers = 0
    for p in paths:
        for method in ("OPTIONS", "POST"):
            r = getattr(c, method.lower())(p, headers=pre)
            ah = r.headers.get("Access-Control-Allow-Headers")
            if ah is not None:
                seen_headers += 1
            if ah and ("x-bd-token" in ah.lower() or ah.strip() == "*"):
                allow_tok.append(f"{method} {p}")
            if r.headers.get("Access-Control-Allow-Origin") in (EVIL, "*") and p != paths[-1]:
                reflect.append(f"{method} {p}")
    # positive control: the teach preflight DOES answer with CORS headers (the probe can see them)
    assert seen_headers >= 1, "ECT-CORS-CONTROL no Access-Control-Allow-Headers seen anywhere"
    assert not allow_tok, f"ECT-CORS X-BD-Token allowed to an arbitrary Origin on {len(allow_tok)}: {allow_tok}"
    assert not reflect, f"ECT-CORS Origin reflected on a queue route {len(reflect)}: {reflect}"


# ── grep guard (PM 1440Z test b): one Allow-Headers setter, never X-BD-Token ──

_ALLOW_HEADERS = re.compile(r"allow[-_ ]?headers", re.IGNORECASE)


def test_one_allow_headers_setter_never_lists_x_bd_token():
    """Denominator: every *.py under bulk_downloader/ on disk (recursive). A setter is any line
    naming Allow-Headers in any spelling (header string, werkzeug attribute, CORS kwarg)."""
    root = Path(__file__).resolve().parents[1] / "bulk_downloader"
    files = sorted(root.rglob("*.py"))
    hits = []
    for f in files:
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if _ALLOW_HEADERS.search(line) and not line.lstrip().startswith("#"):
                hits.append((f.relative_to(root.parent).as_posix(), n, line))
    assert len(files) > 100, f"ECT-ACAH-GUARD denominator too small: {len(files)} files under {root}"
    assert len(hits) == 1, (
        f"ECT-ACAH-GUARD expected exactly 1 Allow-Headers setter in {len(files)} files, found {len(hits)}: "
        f"{[(p, n) for p, n, _l in hits]}")
    path, n, line = hits[0]
    assert path == "bulk_downloader/app.py" and "x-bd-token" not in line.lower() and "*" not in line, (
        f"ECT-ACAH-GUARD the Allow-Headers setter at {path}:{n} lists X-BD-Token or *")
