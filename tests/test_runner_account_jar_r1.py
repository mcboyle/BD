"""runner-account-jar r1 (O1789 PM pass 10:45Z item 2, spec A2/A3).

session-jar-integrity gave each account its own jar: the keeper for account N
refreshes cookies.cookie_jar_path(sid, N) (<stem>__a<N>.json) and mirrors it to
vault key (sid, N). The runner never read it.

A2: every account switch (SiteRunner start-up push, legacy rotation, pool
rotation, round-robin) pointed config["cookie_file"] at accounts[N]["cookie_file"]
raw -- usually "" -- so the runner loaded no jar, or a jar no keeper refreshes.
Restart: the runner's cell is the persisted site record, so the runner records
the account-0 jar as cookie_jar_base and cookie_jar_path resolves a cell that is
exactly <base>__a<N> from it (no __a1__a1, keeper 0 never on account 1's jar).
r2 F1 (correctness + security): a CONFIGURED stem ending __a<N> is a jar of its
own -- r1 stripped it, orphaning operator__a7.json and merging site B's
foo__a1.json into site A's foo.json.
A3: the runner's three cookie saves (login_async settle, finish_manual_login,
finish_manual_download) called save_cookies_to_file without account_idx, so the
vault key for account N was (<stem>__a<N>, "0").

Hermetic: jars under tmp_path, do_login / finalize / vault faked, no site traffic.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

from bulk_downloader import cookies as ck
from bulk_downloader import login, profile_sync, session_keeper
from bulk_downloader import runner_transport as transport
from bulk_downloader import vault_sync
from bulk_downloader.account_pool import configure_pool, remove_pool
from bulk_downloader.db import db_init
from bulk_downloader.runner import SiteRunner
from bulk_downloader.runner_accounts import AccountsMixin

BD_GATE_SCOPE = "module"

SID = "acctJarSite"


def _jar(value):
    return [{"name": "sess", "value": value, "domain": ".site.test", "path": "/",
             "expires": time.time() + 86400}]


def _values(cookies):
    return [c["value"] for c in cookies or []]


class _Vault:
    def __init__(self):
        self.store = {}

    def set_session(self, sid, acc, data, **_kw):
        self.store[(sid, acc)] = data
        return True

    def get_session(self, sid, acc):
        return self.store.get((sid, acc))


@pytest.fixture(autouse=True)
def _isolate(clean_workdir, monkeypatch):
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    yield clean_workdir


def _site_cfg(tmp_path, n=3):
    # the site names its jar; accounts carry no cookie_file (the common shape:
    # the keeper derives <stem>__a<N> from the site's jar, never from the account)
    return {
        "cookie_file": str(tmp_path / f"{SID}.json"),
        "accounts": [{"username": f"user{i}", "password": f"pw{i}"} for i in range(n)],
    }


def _keeper_jars(tmp_path, cfg):
    """What the keepers left on disk: account N's refreshed session, in the jar
    cookie_jar_path names for it (the keeper's own resolver)."""
    paths = {}
    for i in range(len(cfg["accounts"])):
        p = ck.cookie_jar_path(SID, i, {"cookie_file": str(tmp_path / f"{SID}.json")})
        ck.save_cookies_to_file(p, _jar(f"KEEPER-REFRESH-A{i}"))
        paths[i] = p
    return paths


class _Rotor:
    _rotate_account_if_available = AccountsMixin._rotate_account_if_available

    def __init__(self, cfg):
        self.site_id = SID
        self.config = cfg
        self._active_account_idx = 0
        self.cookies = []

    def _persist_pool_state(self):
        pass

    def _persist_account_state(self):
        pass

    def set_cookies(self, cookies):
        self.cookies = list(cookies)

    def login_async(self, **_kw):
        pass


def _assert_on_account(r, jars, idx, where):
    got = _values(r.cookies)
    assert got == [f"KEEPER-REFRESH-A{idx}"], (
        f"RUNNER_ACCOUNT_JAR_NOT_SEEN[{where}]: switched to account {idx}; the keeper refreshed "
        f"{jars[idx]} but the runner holds {got} from cookie_file={r.config.get('cookie_file')!r}")
    assert Path(r.config["cookie_file"]) == jars[idx], (
        f"RUNNER_ACCOUNT_JAR_PATH[{where}]: account {idx} active, cookie_file="
        f"{r.config.get('cookie_file')!r}, keeper jar {jars[idx]}")


# --- A2: the runner reads account N's jar through cookie_jar_path(sid, N) -----


def test_a2_startup_push_points_at_active_accounts_jar(tmp_path):
    db_init()
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    cfg["accounts"][0]["cooldown_until"] = time.time() + 3600  # account 0 cooling: start on 1
    r = SiteRunner(SID, cfg)
    assert r._active_account_idx == 1
    assert Path(r.config["cookie_file"]) == jars[1], (
        f"RUNNER_ACCOUNT_JAR_PATH[startup]: account 1 active, cookie_file="
        f"{r.config.get('cookie_file')!r}, keeper jar {jars[1]}")


@pytest.mark.parametrize("pooled", [False, True], ids=["legacy", "pool"])
def test_a2_rotation_loads_keeper_refreshed_jar(tmp_path, pooled):
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    r = _Rotor(cfg)
    if pooled:
        configure_pool(SID, cfg["accounts"])
    try:
        assert r._rotate_account_if_available("rate limited") is True
        assert r._active_account_idx == 1
        _assert_on_account(r, jars, 1, "pool" if pooled else "legacy")
        # rotating twice never stacks suffixes (cookie_file already names __a1)
        assert r._rotate_account_if_available("rate limited") is True
        assert r._active_account_idx == 2
        _assert_on_account(r, jars, 2, ("pool" if pooled else "legacy") + "-second")
    finally:
        remove_pool(SID)


def test_a2_rotation_wraps_to_account0_site_jar(tmp_path):
    """Account 0 keeps the site's own jar (no migration), also when the cell
    already names another account's jar."""
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    assert jars[0] == tmp_path / f"{SID}.json"
    r = _Rotor(cfg)
    r._active_account_idx = 2
    cfg["cookie_file"] = str(jars[2])  # the runner left account 2's jar in the cell
    cfg["cookie_jar_base"] = str(jars[0])  # ... and recorded the base beside it
    cfg["accounts"][0]["cooldown_until"] = 0
    cfg["accounts"][1]["cooldown_until"] = time.time() + 3600
    assert r._rotate_account_if_available("rate limited") is True
    assert r._active_account_idx == 0
    _assert_on_account(r, jars, 0, "wrap-to-0")


def test_a2_control_plain_jar_unchanged(tmp_path):
    """Negative control (passes on BASE): a site with no accounts keeps its
    cookie_file, and an unsuffixed cell resolves exactly as before."""
    plain = AccountsMixin._get_active_account(type("_P", (), {"config": {
        "username": "u", "password": "", "cookie_file": "/x/plain.json"}})())
    assert plain[2] == "/x/plain.json"
    cell = {"cookie_file": str(tmp_path / "site.json")}
    assert ck.cookie_jar_path(SID, 0, cell) == tmp_path / "site.json"
    assert ck.cookie_jar_path(SID, 3, cell) == tmp_path / "site__a3.json"


def test_cookie_jar_path_drops_persisted_account_suffix(tmp_path):
    """The runner writes account N's jar into the site cell, and that cell is
    persisted (s_cfg -> sites_config.json) and snapshotted by keeper start
    (keeper_cfg = dict(cfg)). After rotate 0->1 + save + restart every keeper
    must still resolve its own jar."""
    base = tmp_path / f"{SID}.json"
    cell = {"cookie_file": str(tmp_path / f"{SID}__a1.json"),  # persisted after 0->1
            "cookie_jar_base": str(base)}
    got = {i: ck.cookie_jar_path(SID, i, cell) for i in range(3)}
    want = {0: base, 1: tmp_path / f"{SID}__a1.json", 2: tmp_path / f"{SID}__a2.json"}
    assert got == want, (
        f"RUNNER_ACCOUNT_JAR_STACKED: site cell {cell['cookie_file']!r} -> keeper jars {got}, "
        f"want {want} (keeper 0 on account 1's jar / __a1__a1)")


def test_a2_rotation_cell_survives_restart(tmp_path):
    """End to end: rotate 0->1, persist the cell (dict copy, as keeper start /
    sites_config.json do), and every keeper still resolves its own jar."""
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    r = _Rotor(cfg)
    assert r._rotate_account_if_available("rate limited") is True
    persisted = {k: v for k, v in cfg.items() if not k.startswith("_")}
    got = {i: ck.cookie_jar_path(SID, i, dict(persisted)) for i in range(3)}
    assert got == jars, f"RUNNER_ACCOUNT_JAR_STACKED[restart]: cell {persisted} -> {got}, want {jars}"


# --- r2 F1: a configured stem ending __a<N> is not a runner-written account jar --


class _Keeper:
    """The keeper's own state for SessionKeeper._load_cookies_as_playwright."""
    _load = session_keeper.SessionKeeper._load_cookies_as_playwright

    def __init__(self, sid, idx, cfg):
        self.site_id, self.account_idx, self.config = sid, idx, cfg


@pytest.mark.parametrize("stem,accounts", [
    ("operator__a7", 0), ("operator__a1", 3), ("operator", 0)],
    ids=["suffixed-single", "suffixed-multi", "control-plain"])
def test_r2_configured_suffixed_jar_keeps_loading(tmp_path, stem, accounts):
    """cx-lens-1 F1 trigger: account 0, cookie_file names an existing
    <stem>.json with one cookie, vault off. BASE loads 1; r1 loaded 0 (it
    resolved operator.json, which does not exist)."""
    jar = tmp_path / f"{stem}.json"
    ck.save_cookies_to_file(jar, _jar("CONFIGURED"))
    cfg = {"cookie_file": str(jar)}
    if accounts:
        cfg["accounts"] = [{"username": f"u{i}", "password": "pw"} for i in range(accounts)]
    got = _values(_Keeper("opSite", 0, cfg)._load())
    assert got == ["CONFIGURED"], (
        f"RUNNER_ACCOUNT_JAR_ORPHANED: keeper 0 of cookie_file={jar.name} loads {got}; "
        f"resolved {ck.cookie_jar_path('opSite', 0, cfg)}")


@pytest.mark.parametrize("b_accounts", [0, 2], ids=["single", "multi"])
def test_r2_sibling_sites_jars_stay_isolated(tmp_path, b_accounts):
    """C3 security F1 paired proof: site A foo.json, site B foo__a1.json, both
    account 0. Keeper/relogin writes for B must not land in A's jar."""
    a = {"cookie_file": str(tmp_path / "foo.json")}
    b = {"cookie_file": str(tmp_path / "foo__a1.json")}
    if b_accounts:
        b["accounts"] = [{"username": f"u{i}", "password": "pw"} for i in range(b_accounts)]
    ck.save_cookies_to_file(ck.cookie_jar_path("siteA", 0, a), _jar("FIXTURE-A"), account_idx=0)
    ck.save_cookies_to_file(ck.cookie_jar_path("siteB", 0, b), _jar("FIXTURE-B"), account_idx=0)
    held = {"A0": _values(_Keeper("siteA", 0, a)._load()),
            "B0": _values(_Keeper("siteB", 0, b)._load())}
    assert held == {"A0": ["FIXTURE-A"], "B0": ["FIXTURE-B"]}, (
        f"RUNNER_ACCOUNT_JAR_CROSS_SITE: {held}; A0 -> {ck.cookie_jar_path('siteA', 0, a)}, "
        f"B0 -> {ck.cookie_jar_path('siteB', 0, b)}")


def test_r2_base_only_heals_its_own_directory(tmp_path):
    """cookie_jar_base never moves a jar out of the cookie_file's directory or
    onto a different stem: only an exact <base stem>__a<N> sibling resolves."""
    other = tmp_path / "elsewhere"
    cell = {"cookie_file": str(tmp_path / "foo__a1.json"),
            "cookie_jar_base": str(other / "foo.json")}
    assert ck.cookie_jar_path(SID, 0, cell) == tmp_path / "foo__a1.json"
    cell = {"cookie_file": str(tmp_path / "foobar__a1.json"),
            "cookie_jar_base": str(tmp_path / "foo.json")}
    assert ck.cookie_jar_path(SID, 0, cell) == tmp_path / "foobar__a1.json"


def test_a2_startup_heals_persisted_account_cell(tmp_path):
    db_init()
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    cfg["cookie_file"] = str(jars[1])  # persisted while account 1 was active
    cfg["cookie_jar_base"] = str(jars[0])
    r = SiteRunner(SID, cfg)
    assert r._active_account_idx == 0
    assert Path(r.config["cookie_file"]) == jars[0], (
        f"RUNNER_ACCOUNT_JAR_PATH[startup-heal]: account 0 active, cookie_file="
        f"{r.config.get('cookie_file')!r}, keeper jar {jars[0]}")


def test_a2_get_active_account_names_accounts_jar(tmp_path):
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    r = _Rotor(cfg)
    r._active_account_idx = 2
    r._get_active_account = AccountsMixin._get_active_account.__get__(r)
    user, _pw, cf = r._get_active_account()
    assert user == "user2"
    assert Path(cf) == jars[2], f"RUNNER_ACCOUNT_JAR_PATH[_get_active_account]: {cf!r} != {jars[2]}"


# round-robin: drive the real _do_download success tail (porndig-1 fixture shape)

_PAGE = "https://www.tube2.example/videos/1/acct-jar.html"
_HREF = "https://videos.tube2.example/download/index/1/2/tok/13/tube2.example_acct-jar_1080p"
_HTML = f"""<!doctype html><html><body>
<a class="dl agepass_check" href="{_HREF}"><span>1080p</span><span>238 MB</span></a>
<script>document.addEventListener('click', e => {{
  if (e.target.closest('.agepass_check')) {{ e.preventDefault(); e.stopPropagation(); }}
}}, true);</script></body></html>"""


class _DlRunner(transport.TransportMixin):
    _FIRST_DOWNLOAD_TIMEOUT_MS = 1500

    def __init__(self, cfg):
        self.site_id = SID
        self.config = dict(cfg, name="acct-jar-fixture", use_http_dl=True,
                           verify_hash=False, verify_integrity=False,
                           accounts_mode="round_robin", accounts_rotate_every=1)
        self._active_account_idx = 0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self.jobs = {}
        self.log = logging.getLogger("acctjar")
        self.cookies = []
        self.events = []
        self.updates = []

    def set_cookies(self, cookies):
        self.cookies = list(cookies)

    def log_event(self, *a, **_k):
        self.events.append(a)

    def _probe_for_higher_tier(self, url, **_k):
        return url

    def _build_mirror_urls(self, _url):
        return []

    def _run_http_attempts_with_resume(self, page_url, page, ctx, file_url, attempt_urls, final_path):
        Path(final_path).write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64)
        size = Path(final_path).stat().st_size
        return size, size

    def _update_job(self, url, status, message="", **_k):
        self.updates.append((status, message))

    def _handle_failure(self, url, message):
        self.updates.append(("failed", message))

    def _size_on_disk_after_tagging(self, _path, downloaded_size):
        return downloaded_size

    def _screenshot(self, *_a, **_k):
        return ""


@contextmanager
def _page():
    from playwright.sync_api import sync_playwright  # no importorskip: a skip would read as caught
    with sync_playwright() as p:
        br = p.chromium.launch()
        try:
            ctx = br.new_context(accept_downloads=True)
            pg = ctx.new_page()
            pg.route("**/*", lambda route: (
                route.fulfill(status=200, content_type="text/html", body=_HTML)
                if route.request.url == _PAGE else route.abort()))
            pg.goto(_PAGE, wait_until="domcontentloaded")
            yield ctx, pg
        finally:
            br.close()


def test_a2_round_robin_loads_keeper_refreshed_jar(tmp_path, monkeypatch):
    monkeypatch.setattr(transport, "_HTTPX_AVAILABLE", True)
    monkeypatch.setattr(transport, "db_skip_identity", lambda *_a: ("different", ""))
    monkeypatch.setattr(transport, "db_log", lambda *_a, **_k: None)
    monkeypatch.setattr(transport.staging_claim, "reserve",
                        lambda path, _identity: (path, path.with_suffix(".part")))
    monkeypatch.setattr(transport.staging_claim, "release", lambda *_a: None)
    cfg = _site_cfg(tmp_path)
    jars = _keeper_jars(tmp_path, cfg)
    r = _DlRunner(cfg)
    dl_dir = tmp_path / "dl"
    dl_dir.mkdir()
    with _page() as (ctx, pg):
        best = {"locator": pg.locator("a.dl").first, "score": 1080, "size": 700 * 1024, "text": "1080p 238 MB"}  # under the size-sanity gate
        r._do_download(pg, ctx, _PAGE, best, dl_dir, "1080p")
    assert ("done" in [s for s, _m in r.updates]), f"fixture did not reach the success tail: {r.updates}"
    assert r._active_account_idx == 1, f"round-robin did not rotate: {r.events}"
    _assert_on_account(r, jars, 1, "round-robin")


# --- A3: the runner's saves key the vault (stem, N) ----------------------------


@pytest.fixture
def acct1_runner(tmp_path, monkeypatch):
    db_init()
    v = _Vault()
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: v)
    cfg = _site_cfg(tmp_path)
    cfg.update(login_url="https://example.com/login", auto_teach_first_run=False,
               manual_use_persistent_profile=False)
    r = SiteRunner(SID, cfg)
    # account 1 active, on its own jar (isolates A3 from the A2 push)
    r._active_account_idx = 1
    r.config["cookie_file"] = str(ck.cookie_jar_path(SID, 1, {"cookie_file": str(tmp_path / f"{SID}.json")}))
    r.start_manual_login = lambda *a, **k: (False, "test: no manual window")
    return r, v, tmp_path


def _assert_vault_account1(v, value, where):
    keys = sorted(v.store)
    assert keys == [(SID, "1")], (
        f"RUNNER_ACCOUNT_VAULT_KEY[{where}]: account 1's save mirrored to {keys}, want [({SID!r}, '1')]")
    assert value in json.dumps(v.store[(SID, "1")]), v.store


def test_a3_login_settle_saves_under_account(acct1_runner):
    r, v, tmp_path = acct1_runner
    with mock.patch("bulk_downloader.runner_auth.do_login",
                    return_value=(True, "ok", _jar("LOGIN-A1"))):
        r.login_async()
        r._login_thread.join(15)
    assert _values(ck.load_cookies_from_file(r.config["cookie_file"])) == ["LOGIN-A1"]
    assert not (tmp_path / f"{SID}.json").exists(), "account 1's login wrote account 0's jar"
    _assert_vault_account1(v, "LOGIN-A1", "login_async")


def test_a3_manual_login_finish_saves_under_account(acct1_runner, monkeypatch):
    r, v, _tmp = acct1_runner
    monkeypatch.setattr(login, "finalize_manual_login",
                        lambda h: (True, "captured 1 cookies", _jar("MANUAL-A1"),
                                   {"clicks": [], "inputs": []}))
    monkeypatch.setattr(profile_sync, "sync_manual_to_runtime",
                        lambda site_id, ensure=None: {"synced": {}})
    r._manual_cookie_snapshot = []
    r._manual_login_handle = object()
    ok, msg = r.finish_manual_login()
    assert ok, msg
    _assert_vault_account1(v, "MANUAL-A1", "finish_manual_login")


def test_a3_manual_download_finish_saves_under_account(acct1_runner):
    r, v, _tmp = acct1_runner

    class _Session:
        target_url = "https://www.tube2.example/videos/9/x.html"

        def finalize(self, timeout=30):
            return True, "ok", _jar("TAKEOVER-A1"), {"clicks": [], "inputs": []}

    r._manual_download_session = _Session()
    r.finish_manual_download()
    _assert_vault_account1(v, "TAKEOVER-A1", "finish_manual_download")


# r3 (r2 security F1 MED): cookie_jar_base is server-derived -- a clone must
# not inherit it and a request must not set it, else the stored cookie_file
# names one jar while reads/writes hit another site's.

def _r3_parent(fresh_app, jars):
    from bulk_downloader import app as app_module
    r = fresh_app.post("/api/sites", json={
        "name": "jar-parent", "cookie_file": str(jars / "foo.json"),
        "accounts": [{"username": "u0", "password": "p0"},
                     {"username": "u1", "password": "p1"}]})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    sid = r.get_json()["id"]
    assert app_module.s_cfg[sid].get("cookie_jar_base") == str(jars / "foo.json"), (
        "precondition: the runner recorded the parent's account-0 jar")
    return app_module, sid


@pytest.mark.parametrize("name", ["foo__a7.json", "foo__a2.json"])
def test_r3_clone_does_not_inherit_parents_jar_base(fresh_app, clean_workdir, name):
    jars = clean_workdir / "jars"
    jars.mkdir()
    app_module, psid = _r3_parent(fresh_app, jars)
    rc = fresh_app.post(f"/api/sites/{psid}/clone", json={})
    assert rc.status_code == 200, rc.get_data(as_text=True)[:300]
    csid = rc.get_json()["id"]
    assert "cookie_jar_base" not in app_module.s_cfg[csid], (
        "clone inherited the parent's cookie_jar_base")
    rp = fresh_app.put(f"/api/sites/{csid}", json={"cookie_file": str(jars / name)})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    parent0 = ck.cookie_jar_path(psid, 0, app_module.s_cfg[psid])
    clone0 = ck.cookie_jar_path(csid, 0, app_module.s_cfg[csid])
    assert clone0 == jars / name, f"clone acct0 jar {clone0} != its cookie_file {name}"
    assert clone0 != parent0, "clone acct0 jar is the parent's acct0 jar"
    ck.save_cookies_to_file(parent0, _jar("R3-PARENT"), account_idx=0)
    ck.save_cookies_to_file(clone0, _jar("R3-CLONE"), account_idx=0)
    assert _values(ck.load_cookies_from_file(parent0)) == ["R3-PARENT"], (
        "the clone's save clobbered the parent's session")


def test_r3_put_ignores_client_supplied_jar_base(fresh_app, clean_workdir):
    jars = clean_workdir / "jars"
    jars.mkdir()
    app_module, psid = _r3_parent(fresh_app, jars)
    r = fresh_app.post("/api/sites", json={"name": "jar-victim"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    vsid = r.get_json()["id"]
    rp = fresh_app.put(f"/api/sites/{vsid}", json={
        "cookie_jar_base": str(jars / "foo.json"),
        "cookie_file": str(jars / "foo__a3.json")})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    assert app_module.s_cfg[vsid].get("cookie_jar_base") != str(jars / "foo.json"), (
        "PUT stored the client's cookie_jar_base")
    assert ck.cookie_jar_path(vsid, 0, app_module.s_cfg[vsid]) == jars / "foo__a3.json"
    # the UI round-trips meta: echoing the server's own value is not a refusal
    # and does not move the parent's base
    rp = fresh_app.put(f"/api/sites/{psid}", json={"cookie_jar_base": "/elsewhere/x.json"})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    assert app_module.s_cfg[psid]["cookie_jar_base"] == str(jars / "foo.json")


def test_r3_base_refused_when_symlink_or_foreign_parent(tmp_path):
    """(c) via the resolver: a stored base heals only an exact sibling, and
    never when the base itself is a symlink (it could name any file)."""
    cell = {"cookie_file": str(tmp_path / "foo__a1.json"),
            "cookie_jar_base": str(tmp_path / "foo.json")}
    assert ck.cookie_jar_path(SID, 0, cell) == tmp_path / "foo.json", "control: sibling heals"
    target = tmp_path / "outside.txt"
    target.write_text("x")
    (tmp_path / "evil.json").symlink_to(target)
    cell = {"cookie_file": str(tmp_path / "evil__a1.json"),
            "cookie_jar_base": str(tmp_path / "evil.json")}
    assert ck.cookie_jar_path(SID, 0, cell) == tmp_path / "evil__a1.json", (
        "a symlinked cookie_jar_base was honoured")
    cell = {"cookie_file": str(tmp_path / "foo__a1.json"),
            "cookie_jar_base": str(tmp_path / "other" / "foo.json")}
    assert ck.cookie_jar_path(SID, 0, cell) == tmp_path / "foo__a1.json", (
        "a cookie_jar_base in another directory was honoured")


# r4 (r3 security F1 MED): a stored cookie_jar_base must not outlive the
# cookie_file it was derived from -- site A's PUT to foo__a7.json kept base
# foo.json, so A's account-0 jar became site B's foo.json.

@pytest.mark.parametrize("name", ["foo__a7.json", "foo__a2.json"])
def test_r4_put_cookie_file_drops_stale_jar_base(fresh_app, clean_workdir, name):
    jars = clean_workdir / "jars"
    jars.mkdir()
    app_module, asid = _r3_parent(fresh_app, jars)
    rp = fresh_app.put(f"/api/sites/{asid}", json={"cookie_file": str(jars / name)})
    assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
    assert app_module.s_cfg[asid].get("cookie_jar_base") != str(jars / "foo.json"), (
        "stale cookie_jar_base survived PUT cookie_file")
    r = fresh_app.post("/api/sites", json={"name": "jar-other",
                                           "cookie_file": str(jars / "foo.json")})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    bsid = r.get_json()["id"]
    a0 = ck.cookie_jar_path(asid, 0, app_module.s_cfg[asid])
    b0 = ck.cookie_jar_path(bsid, 0, app_module.s_cfg[bsid])
    assert a0 == jars / name, f"A acct0 jar {a0} != its cookie_file {name}"
    assert a0 != b0, "A acct0 jar is site B's acct0 jar"
    a1 = ck.cookie_jar_path(asid, 1, app_module.s_cfg[asid])
    assert a1 == jars / f"{Path(name).stem}__a1.json", f"A acct1 jar {a1} not derived from the new cookie_file"
    ck.save_cookies_to_file(b0, _jar("R4-B"), account_idx=0)
    ck.save_cookies_to_file(a0, _jar("R4-A"), account_idx=0)
    assert _values(ck.load_cookies_from_file(b0)) == ["R4-B"], (
        "site A's save clobbered site B's session")


def test_r4_put_same_or_blank_cookie_file_keeps_jar_base(fresh_app, clean_workdir):
    """Control: an edit that does not change cookie_file (blank = preserve,
    or the stored value echoed) keeps the runner's base."""
    jars = clean_workdir / "jars"
    jars.mkdir()
    app_module, asid = _r3_parent(fresh_app, jars)
    for cf in ("", str(jars / "foo.json")):
        rp = fresh_app.put(f"/api/sites/{asid}", json={"cookie_file": cf})
        assert rp.status_code == 200, rp.get_data(as_text=True)[:300]
        assert app_module.s_cfg[asid].get("cookie_jar_base") == str(jars / "foo.json"), (
            f"PUT cookie_file={cf!r} (no change) dropped the base")
