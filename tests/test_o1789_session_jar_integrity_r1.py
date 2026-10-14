"""session-jar-integrity r1 (O1789 session-auth-capture HIGH-F2 + HIGH-F3).

F2: one jar path per SITE while keepers run per (site, account), and every
save mirrored into vault key (sid, "0") -- account 1's keeper overwrote
account 0's saved session, jar and vault both. Account 0 keeps
cookies/<sid>.json and vault (sid, "0"); account N>0 gets
cookies/<sid>__a<N>.json and vault (sid, "N").

F3: finish_manual_login flipped a failed finalize to success on ANY polled
snapshot (pre-login anonymous cookies included) and replaced the good jar.
The snapshot now has to pass the login-success cookie check.

F14 (PM ANSWER-pm-B-F14-S15-0910Z): the keeper read and wrote a cwd-relative
cookies/<sid>.json. One resolver, cookies.cookie_jar_path: the site's
cookie_file when set, else <BD_HOME>/cookies, never the cwd.

Both: the previous good jar is kept as <file>.bak; each save writes a unique
temp file (S34: two writers shared <file>.tmp).
"""

import ast
import importlib
import json
import pathlib
import types

import pytest

from bulk_downloader import cookies as ck
from bulk_downloader import login, profile_sync, runner_auth, runner_browser
from bulk_downloader import session_keeper as sk
from bulk_downloader import vault_sync

BD_GATE_SCOPE = "module"

ACC0 = [{"name": "sess", "value": "ACCOUNT-ZERO", "domain": ".site.test", "path": "/"}]
ACC1 = [{"name": "sess", "value": "ACCOUNT-ONE", "domain": ".site.test", "path": "/"}]
GOOD = [
    {"name": "auth_session", "value": "GOOD" * 8, "domain": ".site.test", "path": "/"}
]
ANON = [{"name": "_ga", "value": "GA1.1.anon", "domain": ".site.test", "path": "/"}]
# four substantial, non-auth-named cookies: with no pre-login snapshot they
# must not count as "new since submit" (before_cookies=None)
ANON4 = [
    {"name": n, "value": "x" * 20, "domain": ".site.test", "path": "/"}
    for n in ("_ga", "_gid", "cf_bm", "visitor")
]


class _Vault:
    def __init__(self):
        self.store = {}

    def set_session(self, sid, acc, data, **_kw):
        self.store[(sid, acc)] = data
        return True

    def get_session(self, sid, acc):
        return self.store.get((sid, acc))


class _Ctx:
    def __init__(self, jar):
        self.jar = list(jar)

    def cookies(self):
        return list(self.jar)


def _keeper(idx):
    return sk.SessionKeeper("siteA", idx, {}, lambda *a: (False, "x"))


def _values(cookies):
    return [c["value"] for c in cookies or []]


def _heartbeat_jar(k):
    """name -> value of what the httpx heartbeat reader loaded. O1789 SAC-F1
    (cookie-egress-fix) made _load_cookies return a domain-scoped CookieJar,
    not a flat dict; {} when it read no jar."""
    return {c.name: c.value for c in k._load_cookies()}


# --- F2: per-account jar + vault ------------------------------------------


@pytest.mark.parametrize("with_vault", [False, True])
def test_s20_account1_persist_keeps_account0_session(monkeypatch, tmp_path, with_vault):
    monkeypatch.chdir(tmp_path)
    v = _Vault()
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: v if with_vault else None)
    k0, k1 = _keeper(0), _keeper(1)
    k0._ctx, k1._ctx = _Ctx(ACC0), _Ctx(ACC1)
    k0._persist_cookies()
    k1._persist_cookies()
    got0 = _values(k0._load_cookies_as_playwright())
    got1 = _values(k1._load_cookies_as_playwright())
    v0 = _values((v.store.get(("siteA", "0")) or {}).get("cookies"))
    assert got0 == ["ACCOUNT-ZERO"] and got1 == ["ACCOUNT-ONE"], (
        f"S20 vault={with_vault}: after account 1 persisted, account 0 loads {got0}, "
        f"account 1 loads {got1}; vault keys={sorted(v.store)} v[0]={v0}"
    )
    if with_vault:
        assert v0 == ["ACCOUNT-ZERO"], (
            f"S20: vault (siteA,'0') overwritten by account 1: {v0}"
        )
        assert _values(v.store[("siteA", "1")]["cookies"]) == ["ACCOUNT-ONE"]
        assert sorted(v.store) == [("siteA", "0"), ("siteA", "1")], sorted(v.store)
    # the httpx-heartbeat reader is per account too
    assert _heartbeat_jar(k0) == {"sess": "ACCOUNT-ZERO"}, "O1789-SJ-F2-HEARTBEAT-ACC0-JAR"
    assert _heartbeat_jar(k1) == {"sess": "ACCOUNT-ONE"}, "O1789-SJ-F2-HEARTBEAT-ACC1-JAR"
    assert _values(json.loads((tmp_path / "cookies" / "siteA.json").read_text())) == [
        "ACCOUNT-ZERO"
    ]
    assert _values(
        json.loads((tmp_path / "cookies" / "siteA__a1.json").read_text())
    ) == ["ACCOUNT-ONE"]


def test_s20_control_single_account_roundtrip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    k0 = _keeper(0)
    k0._ctx = _Ctx(ACC0)
    k0._persist_cookies()
    assert _values(k0._load_cookies_as_playwright()) == ["ACCOUNT-ZERO"]
    # account 0 keeps the pre-fix path: existing installs need no migration
    assert (tmp_path / "cookies" / "siteA.json").is_file()
    assert _heartbeat_jar(k0) == {"sess": "ACCOUNT-ZERO"}, "O1789-SJ-F2-CTL-HEARTBEAT-JAR"


def test_s20_jar_paths_per_account(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("BD_HOME", str(home))
    jars = home.resolve() / "cookies"
    assert ck.cookie_jar_path("siteA", 0) == jars / "siteA.json"
    assert ck.cookie_jar_path("siteA", 1) == jars / "siteA__a1.json"
    assert ck.cookie_jar_path("siteA", 2) == jars / "siteA__a2.json"
    cf = tmp_path / "custom" / "my-jar.json"
    cfg = {"cookie_file": str(cf)}
    assert ck.cookie_jar_path("siteA", 0, cfg) == cf
    assert ck.cookie_jar_path("siteA", 1, cfg) == cf.with_name("my-jar__a1.json")


@pytest.mark.parametrize("idx", [0, 1])
def test_s20_save_mirrors_only_its_own_vault_key(monkeypatch, tmp_path, idx):
    v = _Vault()
    v.store[("siteA", "0")] = {"cookies": ACC0}
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: v)
    jar = ck.cookie_jar_path("siteA", idx, root=tmp_path)
    ck.save_cookies_to_file(jar, ACC1, account_idx=idx)
    assert sorted(v.store) == sorted({("siteA", "0"), ("siteA", str(idx))}), sorted(
        v.store
    )
    assert _values(v.store[("siteA", str(idx))]["cookies"]) == ["ACCOUNT-ONE"]
    if idx:
        assert _values(v.store[("siteA", "0")]["cookies"]) == ["ACCOUNT-ZERO"]


@pytest.mark.parametrize("cwd_at", ["home", "elsewhere", "cookie_file"])
def test_s20_app_relogin_callback_writes_its_own_account(monkeypatch, tmp_path, cwd_at):
    app = importlib.import_module("bulk_downloader.app")
    # conftest: BD_HOME == tmp_path. F14: "elsewhere" = the cwd is not BD_HOME;
    # "cookie_file" = also the site names its jar (stem kept = the vault sid)
    cwd = tmp_path if cwd_at == "home" else tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    jar0 = (
        tmp_path / ("custom" if cwd_at == "cookie_file" else "cookies") / "siteA.json"
    )
    site = {
        "keep_alive_enabled": True,
        "accounts": [
            {"username": "u0", "password": "p0"},
            {"username": "u1", "password": "p1"},
        ],
    }
    if cwd_at == "cookie_file":
        site["cookie_file"] = str(jar0)
    v = _Vault()
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: v)
    ck.save_cookies_to_file(jar0, ACC0)
    before0 = jar0.read_bytes()
    monkeypatch.delenv("BD_DISABLE_KEEPALIVE", raising=False)
    monkeypatch.setattr(app, "_SITE_RUNTIME_RETIRING", False)
    monkeypatch.setattr(app, "s_cfg", {"siteA": site})
    callbacks = []
    monkeypatch.setattr(
        sk,
        "start_keeper",
        lambda sid, idx, cfg, cb: callbacks.append((sid, idx, cfg, cb)),
    )
    monkeypatch.setattr(
        sk,
        "login_attempts_for_day",
        lambda site_id: {"status": "OK", "count": 0},
        raising=False,
    )
    monkeypatch.setattr(
        sk,
        "reserve_login_attempt",
        lambda *a, **k: {
            "granted": True,
            "status": "OK",
            "count": 1,
            "cap": 3,
            "reason": None,
        },
        raising=False,
    )
    monkeypatch.setattr(
        login, "do_login", lambda *a, **k: (True, "fixture", list(ACC1))
    )
    app._start_session_keepers()
    assert [c[1] for c in callbacks] == [0, 1], callbacks
    sid, idx, cfg, cb = callbacks[1]
    ok, detail = cb(sid, idx, cfg)
    assert ok is True and "relogin ok" in str(detail), detail
    if cwd != tmp_path:
        assert not (cwd / "cookies").exists(), (
            "F14: relogin wrote a jar under the cwd, not the resolver's path"
        )
    assert jar0.read_bytes() == before0, (
        "S20: account 1's relogin replaced account 0's jar"
    )
    assert _values(json.loads(jar0.with_name("siteA__a1.json").read_text())) == [
        "ACCOUNT-ONE"
    ]
    assert _values(v.store[("siteA", "0")]["cookies"]) == ["ACCOUNT-ZERO"]
    assert _values(v.store[("siteA", "1")]["cookies"]) == ["ACCOUNT-ONE"]


# --- F14: one resolver, BD_HOME-rooted, never the cwd ---------------------


@pytest.mark.parametrize("cwd_at", ["elsewhere", "home"])
def test_f14_keeper_jar_under_bd_home_not_cwd(monkeypatch, tmp_path, cwd_at):
    home = tmp_path / "home"
    home.mkdir()
    cwd = home if cwd_at == "home" else tmp_path / "elsewhere"
    cwd.mkdir(exist_ok=True)
    monkeypatch.setenv("BD_HOME", str(home))
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    jar = home / "cookies" / "siteA.json"
    ck.save_cookies_to_file(jar, ACC0)
    k = _keeper(0)
    got = _values(k._load_cookies_as_playwright())
    assert got == ["ACCOUNT-ZERO"], (
        f"F14: {jar} holds the session; keeper (cwd={cwd_at}) loads {got}"
    )
    assert _heartbeat_jar(k) == {"sess": "ACCOUNT-ZERO"}, "O1789-SJ-F14-HEARTBEAT-BD-HOME-JAR"
    k._ctx = _Ctx(ACC1)
    k._persist_cookies()
    assert _values(json.loads(jar.read_text())) == ["ACCOUNT-ONE"], (
        "F14: keeper did not write back to the jar it loaded"
    )
    if cwd_at == "elsewhere":
        assert not (cwd / "cookies").exists(), "F14: keeper wrote a jar under the cwd"


def test_f14_bd_home_root_never_follows_a_later_chdir(monkeypatch, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    # BD_HOME unset: the directory the process started in, not today's cwd
    monkeypatch.delenv("BD_HOME", raising=False)
    monkeypatch.setattr(ck, "_START_DIR", a.resolve())
    monkeypatch.chdir(b)
    assert ck.cookie_jar_path("siteA") == a.resolve() / "cookies" / "siteA.json", (
        "F14: BD_HOME unset -> jar follows the cwd"
    )
    # a relative BD_HOME is resolved once, so a later chdir does not move it
    rel = f"rel-{tmp_path.name}"
    monkeypatch.setenv("BD_HOME", rel)
    monkeypatch.chdir(a)
    first = ck.cookie_jar_path("siteA")
    monkeypatch.chdir(b)
    assert first.is_absolute() and ck.cookie_jar_path("siteA") == first, (
        "F14: relative BD_HOME re-resolved against the new cwd"
    )


def test_f14_keeper_uses_site_cookie_file(monkeypatch, tmp_path):
    (tmp_path / "elsewhere").mkdir()
    monkeypatch.chdir(tmp_path / "elsewhere")
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    cf = tmp_path / "custom" / "my-jar.json"
    ck.save_cookies_to_file(cf, ACC0)
    cfg = {"cookie_file": str(cf)}
    k0 = sk.SessionKeeper("siteA", 0, dict(cfg), lambda *a: (False, "x"))
    k1 = sk.SessionKeeper("siteA", 1, dict(cfg), lambda *a: (False, "x"))
    got = _values(k0._load_cookies_as_playwright())
    assert got == ["ACCOUNT-ZERO"], (
        f"F14: site cookie_file holds the session; keeper loads {got}"
    )
    assert _heartbeat_jar(k0) == {"sess": "ACCOUNT-ZERO"}, (
        "O1789-SJ-F14-HEARTBEAT-COOKIE-FILE: httpx heartbeat reader ignores the site cookie_file"
    )
    k1._ctx = _Ctx(ACC1)
    k1._persist_cookies()
    assert _values(json.loads(cf.read_text())) == ["ACCOUNT-ZERO"]
    assert _values(json.loads(cf.with_name("my-jar__a1.json").read_text())) == [
        "ACCOUNT-ONE"
    ]


def _cookie_dir_literals(tree):
    """Spots a hand-built cookie-dir path: Path("cookies"), or a string /
    f-string beginning "cookies/"."""
    hits = []
    for n in ast.walk(tree):
        if (
            isinstance(n, ast.Call)
            and getattr(n.func, "id", getattr(n.func, "attr", "")) == "Path"
            and n.args
            and isinstance(n.args[0], ast.Constant)
            and n.args[0].value == "cookies"
        ):
            hits.append(n.lineno)
        elif isinstance(n, ast.JoinedStr) and n.values:
            head = n.values[0]
            if isinstance(head, ast.Constant) and str(head.value).startswith(
                "cookies/"
            ):
                hits.append(n.lineno)
        elif (
            isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and n.value.startswith("cookies/")
        ):
            hits.append(n.lineno)
    return sorted(set(hits))  # an f-string head is also a Constant: one line, one hit


def _func(tree, name):
    return next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_f14_grep_guard_no_hand_built_cookie_path():
    # positive control: the detector sees the pre-fix form
    ctl = ast.parse('p = Path("cookies") / f"{sid}.json"\nq = f"cookies/{sid}.json"\n')
    assert len(_cookie_dir_literals(ctl)) == 2
    root = pathlib.Path(sk.__file__).parent
    keeper = ast.parse((root / "session_keeper.py").read_text(encoding="utf-8"))
    assert _cookie_dir_literals(keeper) == [], (
        "F14: session_keeper.py builds a cookie path"
    )
    for name in ("_load_cookies_as_playwright", "_persist_cookies", "_load_cookies"):
        src = ast.unparse(_func(keeper, name))
        assert "cookie_jar_path(" in src, f"F14: keeper {name} bypasses the resolver"
    relogin = _func(
        ast.parse((root / "app.py").read_text(encoding="utf-8")), "_do_login_for_keeper"
    )
    assert _cookie_dir_literals(relogin) == [], "F14: app relogin builds a cookie path"
    assert "cookie_jar_path(" in ast.unparse(relogin)


# --- .bak + unique temp (both) ----------------------------------------------


def test_save_keeps_previous_jar_as_bak_each_save(tmp_path):
    jar = tmp_path / "cookies" / "siteA.json"
    bak = tmp_path / "cookies" / "siteA.json.bak"
    ck.save_cookies_to_file(jar, ACC0)
    assert not bak.exists(), "first save has no previous jar to keep"
    first = jar.read_bytes()
    ck.save_cookies_to_file(jar, ACC1)
    assert bak.is_file(), "BAK: no <file>.bak kept of the jar the save replaced"
    assert bak.read_bytes() == first
    second = jar.read_bytes()
    ck.save_cookies_to_file(jar, GOOD)
    assert bak.read_bytes() == second
    assert sorted(p.name for p in jar.parent.iterdir()) == [
        "siteA.json",
        "siteA.json.bak",
    ]


def test_damaged_jar_never_overwrites_good_bak(tmp_path):
    jar = tmp_path / "siteA.json"
    bak = tmp_path / "siteA.json.bak"
    ck.save_cookies_to_file(jar, ACC0)
    ck.save_cookies_to_file(jar, ACC1)
    assert bak.is_file(), "BAK: no <file>.bak kept of the jar the save replaced"
    good_bak = bak.read_bytes()
    jar.write_text("{not json", encoding="utf-8")
    ck.save_cookies_to_file(jar, GOOD)
    assert bak.read_bytes() == good_bak
    assert _values(json.loads(jar.read_text())) == [GOOD[0]["value"]]


def test_s34_overlapping_saves_use_distinct_temps(monkeypatch, tmp_path):
    jar = tmp_path / "siteA.json"
    real_replace = pathlib.Path.replace
    fired = []

    def replace(self, target):
        # writer B runs its whole save between writer A's temp write and
        # A's replace -- the interleave two keepers hit on one jar
        if not fired and pathlib.Path(target) == jar:
            fired.append(self.name)
            ck.save_cookies_to_file(jar, ACC1)
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", replace)
    ck.save_cookies_to_file(jar, ACC0)
    assert fired, "precondition: the interleave did not run"
    assert _values(json.loads(jar.read_text())) == ["ACCOUNT-ZERO"]
    assert not [p.name for p in jar.parent.iterdir() if p.name.endswith(".tmp")]


# --- F3: failed finalize + snapshot -----------------------------------------


class _Carrier:
    def __init__(self, config, sid="siteA"):
        self.config = config
        self.site_id = sid
        self.log = types.SimpleNamespace(
            warning=lambda *a, **k: None, info=lambda *a, **k: None
        )
        self._manual_login_handle = None
        self._manual_cookie_snapshot = []
        self._login_thread = None
        self.jobs = {}
        self.cookies_set = []
        self.events = []

    def set_cookies(self, cl):
        self.cookies_set.append(cl)

    def _override_suppresses_persist(self):
        return True

    def log_event(self, *a, **k):
        self.events.append(a)

    def _poll_manual_cookies(self, session, stop_event):
        return None


def _runner(config):
    cls = type("_H", (runner_auth.AuthMixin, runner_browser.BrowserMixin, _Carrier), {})
    return cls(config)


@pytest.fixture
def jar(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_sync, "get_vault_sync", lambda: None)
    p = tmp_path / "siteA.json"
    ck.save_cookies_to_file(p, GOOD)
    return p


def _finish(monkeypatch, r, result, snapshot):
    synced = []
    monkeypatch.setattr(login, "finalize_manual_login", lambda h: result)
    monkeypatch.setattr(
        profile_sync,
        "sync_manual_to_runtime",
        lambda site_id, ensure=None: synced.append(site_id) or {"synced": {}},
    )
    r._manual_cookie_snapshot = list(snapshot)
    r._manual_login_handle = object()
    ok, msg = r.finish_manual_login()
    return ok, msg, synced


CLOSED = (
    False,
    "Target page, context or browser has been closed",
    [],
    {"clicks": [], "inputs": []},
)
EMPTY_OK = (True, "captured 0 cookies", [], {"clicks": [], "inputs": []})


@pytest.mark.parametrize("snapshot", [ANON, ANON4], ids=["anon1", "anon4"])
@pytest.mark.parametrize(
    "result", [CLOSED, EMPTY_OK], ids=["finalize_failed", "finalize_empty"]
)
def test_s11_failed_finalize_with_anon_snapshot_keeps_jar(
    monkeypatch, jar, snapshot, result
):
    r = _runner({"cookie_file": str(jar)})
    before = jar.read_bytes()
    ok, msg, synced = _finish(monkeypatch, r, result, snapshot)
    after = json.loads(jar.read_text())
    assert ok is False and jar.read_bytes() == before, (
        f"S11: finalize {result[1]!r} + non-auth snapshot -> ok={ok} msg={msg!r}; "
        f"jar now {[c['name'] for c in after]} (was {[c['name'] for c in GOOD]}); "
        f"set_cookies calls={len(r.cookies_set)}"
    )
    assert r.cookies_set == [] and synced == []
    assert "jar was left unchanged" in msg


def test_s11_control_no_snapshot_fails(monkeypatch, jar):
    r = _runner({"cookie_file": str(jar)})
    before = jar.read_bytes()
    ok, _msg, synced = _finish(monkeypatch, r, CLOSED, [])
    assert ok is False and jar.read_bytes() == before and synced == []


def test_s11_control_logged_in_snapshot_is_captured(monkeypatch, jar):
    r = _runner({"cookie_file": str(jar)})
    fresh = [
        {
            "name": "auth_session",
            "value": "FRESH" * 8,
            "domain": ".site.test",
            "path": "/",
        }
    ]
    before = jar.read_bytes()
    ok, msg, synced = _finish(monkeypatch, r, CLOSED, fresh)
    assert ok is True and "from snapshot" in msg, msg
    assert jar.read_bytes() != before
    assert _values(json.loads(jar.read_text())) == ["FRESH" * 8]
    assert synced == ["siteA"] and r.cookies_set == [fresh]
