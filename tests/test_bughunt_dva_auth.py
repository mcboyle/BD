"""Bug-hunt dva-auth (deep-vpn-admin sweep, /api/auth/* rows in app_auth.py).

Each test replays the finder's HTTP probe (ev/probe_users.py) on a bare Flask
app with the auth blueprint, store in a fresh cwd. Controls (``*_ctl``) show
the same route still answering the legitimate case.

U1  "john.doe" could log in but whoami said None (token split on ".").
U2  "Admin" was created beside "admin".   U3 "." and ".." were usernames.
U4  the last admin could demote itself: no admin could ever exist again.
U5  deleting the last admin reopened the unauthenticated first-admin bootstrap.
U6-U9 non-string fields / non-object body -> 500 AttributeError.
U13 logout only cleared the browser cookie: a copy of it still worked.
U17 the bootstrap count() ran outside the store lock: two concurrent
    unauthenticated creates could both become admin.
U18 /api/auth/login had no auth_throttle.
r2 (R144-D F1/F2): r1 throttled on ONE global label that any success cleared, so an
    operator could reset its own backoff between guesses at admin (F1), and one
    client's failures locked every user out (F2). Now per (username, address),
    plus a soft per-username ceiling capped at BASE seconds.
r3 (R180-A F3): check, then verify, then count: a burst of concurrent guesses all
    passed the check before any failure was counted. Now checked and counted in
    one lock hold before the verify, one verify in flight per label.
r4 (R224-A F4): a long flood on one username overflowed the back-off exponent
    (500 for every later login) and left its pair held (429 forever). Now the
    exponent is capped, the per-user label forgets failures older than MAX and a
    success clears it, and no label outlives its request.
STORE a corrupt store made whoami / bootstrap answer a bare server error.
"""
from __future__ import annotations

import os
import threading
import types

import pytest
from flask import Flask

from bulk_downloader import app_auth as M
from bulk_downloader import auth_throttle as AT
from bulk_downloader import user_accounts as UA

BD_GATE_SCOPE = "module"

PW = "pw123456"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the auth store is cwd-relative
    AT.reset()
    a = Flask(__name__)
    a.testing = True
    M.register_routes(a)
    yield a
    AT.reset()


def _admin(app, name="admin1"):
    c = app.test_client()
    r = c.post("/api/auth/users", json={"username": name, "password": PW, "role": "admin"})
    assert r.status_code == 200, r.get_json()
    assert c.post("/api/auth/login", json={"username": name, "password": PW}).status_code == 200
    return c


def _user(c):
    return (c.get("/api/auth/whoami").get_json() or {}).get("user")


def _create(c, name, role="operator"):
    return c.post("/api/auth/users", json={"username": name, "password": PW, "role": role})


def _names(c):
    return [u["username"] for u in c.get("/api/auth/users").get_json()["users"]]


def _bootstrap(app, name="intruder"):
    return _create(app.test_client(), name, role="admin").status_code


# ── U1 / U2 / U3 usernames ──────────────────────────────────────────────

@pytest.mark.parametrize("name", ["john.doe", "john_doe"], ids=["U1", "U1_ctl"])
def test_u1_dotted_username_session_resolves(app, name):
    a = _admin(app)
    assert _create(a, name).status_code == 200
    b = app.test_client()
    assert b.post("/api/auth/login", json={"username": name, "password": PW}).status_code == 200
    assert _user(b) == {"username": name, "role": "operator"}


@pytest.mark.parametrize("name", [".", ".."])
def test_u3_dot_only_usernames_refused(app, name):
    a = _admin(app)
    r = _create(a, name)
    assert r.status_code == 400 and "only dots" in r.get_json()["error"]
    assert name not in _names(a)


def test_u3_ctl_dot_inside_name_still_allowed(app):
    assert _create(_admin(app), "a.b").status_code == 200


def test_u2_case_lookalike_refused(app):
    a = _admin(app, "admin")
    r = _create(a, "Admin")
    assert r.status_code == 400 and "different case" in r.get_json()["error"]
    assert _names(a) == ["admin"]


def test_u2_ctl_distinct_name_created(app):
    a = _admin(app, "admin")
    assert _create(a, "Bob").status_code == 200
    assert _names(a) == ["Bob", "admin"]


# ── U4 / U5 last admin ──────────────────────────────────────────────────

def test_u4_last_admin_cannot_demote_self(app):
    a = _admin(app)
    r = a.post("/api/auth/users/admin1/role", json={"role": "operator"})
    assert r.status_code == 409 and r.get_json()["error"] == UA.LAST_ADMIN
    assert _user(a)["role"] == "admin"
    assert a.get("/api/auth/users").status_code == 200


def test_u4_ctl_demote_one_of_two_admins(app):
    a = _admin(app)
    assert _create(a, "admin2", role="admin").status_code == 200
    assert a.post("/api/auth/users/admin2/role", json={"role": "operator"}).status_code == 200
    assert a.post("/api/auth/users/admin2/role", json={"role": "admin"}).status_code == 200


def test_u5_last_admin_cannot_be_deleted_bootstrap_stays_shut(app):
    a = _admin(app)
    r = a.delete("/api/auth/users/admin1")
    assert r.status_code == 409 and r.get_json()["error"] == UA.LAST_ADMIN
    assert _bootstrap(app) == 403
    assert _names(a) == ["admin1"]


def test_u5_ctl_delete_one_of_two_admins(app):
    a = _admin(app)
    assert _create(a, "admin2", role="admin").status_code == 200
    assert a.delete("/api/auth/users/admin2").status_code == 200
    assert _bootstrap(app) == 403
    assert a.delete("/api/auth/users/ghost").status_code == 404


# ── U6-U9 malformed bodies -> 400, never 500 ────────────────────────────

@pytest.mark.parametrize("path,body", [
    ("/api/auth/users", {"username": "a", "password": 123}),          # U6
    ("/api/auth/users", {"username": 5, "password": "pw"}),            # U7
    ("/api/auth/users", {"username": "bob", "password": PW, "role": ["admin"]}),
    ("/api/auth/users", [1]),                                          # U9
    ("/api/auth/login", [1]),
    ("/api/auth/login", {"username": "admin1", "password": 123}),
    ("/api/auth/users/admin1/role", {"role": ["admin"]}),              # U8
    ("/api/auth/users/admin1/role", [1]),
    ("/api/auth/users/admin1/password", {"password": 123}),
    ("/api/auth/users/admin1/password", [1]),
], ids=["U6", "U7", "U8_create", "U9", "U9_login", "U6_login", "U8", "U9_role",
        "U6_password", "U9_password"])
def test_u6_u9_malformed_body_is_400(app, path, body):
    a = _admin(app)
    r = a.post(path, json=body)
    assert r.status_code == 400, (r.status_code, r.get_json())
    assert r.get_json()["ok"] is False
    assert _names(a) == ["admin1"] and _user(a)["role"] == "admin"


def test_u8_ctl_unknown_role_is_400(app):
    a = _admin(app)
    assert _create(a, "bob").status_code == 200
    assert a.post("/api/auth/users/bob/role", json={"role": "wizard"}).status_code == 400


# ── U13 logout revokes server-side ──────────────────────────────────────

def test_u13_replayed_cookie_after_logout_is_dead(app):
    a = _admin(app)
    ck = a.get_cookie("bd_user").value
    assert a.post("/api/auth/logout").status_code == 200
    c2 = app.test_client()
    c2.set_cookie("bd_user", ck)
    assert _user(c2) is None


def test_u13_ctl_cookie_without_logout_works_and_relogin_works(app):
    a = _admin(app)
    c2 = app.test_client()
    c2.set_cookie("bd_user", a.get_cookie("bd_user").value)
    assert _user(c2) == {"username": "admin1", "role": "admin"}
    a.post("/api/auth/logout")
    assert a.post("/api/auth/login", json={"username": "admin1", "password": PW}).status_code == 200
    assert _user(a) == {"username": "admin1", "role": "admin"}
    assert app.test_client().post("/api/auth/logout").status_code == 200  # no cookie: still 200


# ── U17 bootstrap race ──────────────────────────────────────────────────

def test_u17_concurrent_bootstraps_make_one_admin(app, monkeypatch):
    real_count = UA.count
    gate = threading.Barrier(2)

    def count_then_wait(base_dir=None):
        n = real_count(base_dir)
        gate.wait(timeout=20)  # both requests have read the count before either creates
        return n
    monkeypatch.setattr(UA, "count", count_then_wait)
    codes = {}

    def boot(name):
        codes[name] = _bootstrap(app, name)
    ts = [threading.Thread(target=boot, args=(n,)) for n in ("first", "second")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(60)
    monkeypatch.setattr(UA, "count", real_count)
    assert sorted(codes.values()) == [200, 403], codes
    assert [u["role"] for u in UA.list_users()] == ["admin"]


def test_u17_ctl_single_bootstrap_is_admin(app):
    assert _create(app.test_client(), "first", role="operator").status_code == 200
    assert UA.list_users()[0]["role"] == "admin"
    assert _bootstrap(app) == 403


# ── U18 login throttle ──────────────────────────────────────────────────

def _throttle_on(monkeypatch):
    monkeypatch.setattr(AT, "_store_get", lambda key: None)  # env decides, not a host config
    monkeypatch.setenv("BD_AUTH_THROTTLE", "1")
    monkeypatch.setenv("BD_AUTH_THROTTLE_FREE", "1")
    monkeypatch.setenv("BD_AUTH_THROTTLE_BASE", "60")


def test_u18_login_is_throttled(app, monkeypatch):
    _admin(app)
    _throttle_on(monkeypatch)
    c = app.test_client()
    bad = {"username": "admin1", "password": "wrong"}
    assert [c.post("/api/auth/login", json=bad).status_code for _ in range(2)] == [401, 401]
    calls = []
    real = UA.verify_password
    monkeypatch.setattr(UA, "verify_password", lambda *a, **k: calls.append(1) or real(*a, **k))
    r = c.post("/api/auth/login", json=bad)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert c.post("/api/auth/login", json={"username": "admin1", "password": PW}).status_code == 429
    assert calls == []  # a throttled guess costs no PBKDF2


def test_u18_ctl_throttle_off_and_success_clears(app, monkeypatch):
    _admin(app)
    monkeypatch.setattr(AT, "_store_get", lambda key: None)
    monkeypatch.delenv("BD_AUTH_THROTTLE", raising=False)
    c = app.test_client()
    bad = {"username": "admin1", "password": "wrong"}
    assert [c.post("/api/auth/login", json=bad).status_code for _ in range(3)] == [401] * 3
    assert c.post("/api/auth/login", json={"username": "admin1", "password": PW}).status_code == 200
    _throttle_on(monkeypatch)
    assert c.post("/api/auth/login", json=bad).status_code == 401
    assert c.post("/api/auth/login", json={"username": "admin1", "password": PW}).status_code == 200
    assert not [k for k in AT.snapshot() if k[1] == "pair"]  # the success cleared its own pair


# ── r2: per-(username, address) keys (R144-D F1 HIGH, F2 MED) ───────────

X = {"REMOTE_ADDR": "10.9.9.9"}   # attacker
Y = {"REMOTE_ADDR": "10.7.7.7"}   # the real user
BAD = {"username": "admin1", "password": "wrong"}


def _login(app, body, env):
    return app.test_client().post("/api/auth/login", json=body, environ_base=env)


def _throttle(monkeypatch, free="2"):
    _throttle_on(monkeypatch)
    monkeypatch.setenv("BD_AUTH_THROTTLE_FREE", free)


def _clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(AT, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    return now


def test_r2_f1_own_login_between_guesses_does_not_reset_backoff(app, monkeypatch):
    """R144-D P1: an operator interleaves logins to its own account with guesses
    at admin1's password from the same address; the guesses must still stop."""
    a = _admin(app)
    assert _create(a, "op").status_code == 200
    _throttle(monkeypatch)
    _clock(monkeypatch)
    op = {"username": "op", "password": PW}
    guesses, own = [], []
    for _ in range(6):
        guesses.append(_login(app, BAD, X).status_code)
        own.append(_login(app, op, X).status_code)
    assert guesses == [401, 401, 401, 429, 429, 429], guesses
    assert own == [200] * 6, own  # control: the operator's own account is never refused


def test_r2_f2_flood_from_x_does_not_refuse_other_users_on_y(app, monkeypatch):
    """R144-D P2: anonymous failures from X (known and unknown names) must not
    refuse a correct login from Y."""
    a = _admin(app)
    assert _create(a, "op").status_code == 200
    _throttle(monkeypatch)
    _clock(monkeypatch)
    for name in ("ghost", "admin1"):
        codes = [_login(app, {"username": name, "password": "x"}, X).status_code for _ in range(4)]
        assert codes == [401, 401, 401, 429], (name, codes)  # control: X is throttled
    assert _login(app, {"username": "op", "password": PW}, Y).status_code == 200
    assert _login(app, {"username": "op", "password": PW}, X).status_code == 200


def test_r2_per_user_ceiling_is_soft(app, monkeypatch):
    """Guesses at one account from many addresses are slowed, but the real user
    on Y waits at most BASE seconds while X stays locked out."""
    _admin(app)
    _throttle(monkeypatch)
    now = _clock(monkeypatch)
    codes = [_login(app, BAD, {"REMOTE_ADDR": f"10.1.0.{i}"}).status_code for i in range(6)]
    assert codes[:3] == [401, 401, 401] and set(codes[3:]) == {429}, codes  # spread guesses slowed
    r = _login(app, {"username": "admin1", "password": PW}, Y)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) <= 61, r.headers.get("Retry-After")
    for _ in range(4):  # X's own pair escalates past BASE (4th failure: 120 s)
        now[0] += 61
        assert _login(app, BAD, X).status_code == 401
    now[0] += 61
    assert _login(app, BAD, X).status_code == 429
    assert _login(app, {"username": "admin1", "password": PW}, Y).status_code == 200


def test_r2_unknown_and_known_username_cost_the_same(app, monkeypatch):
    _admin(app)
    _throttle(monkeypatch)
    _clock(monkeypatch)
    hashes = []
    real = UA._hash_password
    monkeypatch.setattr(UA, "_hash_password", lambda *a: hashes.append(1) or real(*a))
    seen = {}
    for name in ("admin1", "nobody"):
        hashes.clear()
        rs = [_login(app, {"username": name, "password": "x"}, X) for _ in range(4)]
        seen[name] = ([r.status_code for r in rs], rs[-1].headers.get("Retry-After"), len(hashes))
    assert seen["admin1"] == seen["nobody"] == ([401, 401, 401, 429], "61", 3), seen


def test_r2_client_labels_are_bounded_master_label_kept(app, monkeypatch):
    _throttle(monkeypatch)
    monkeypatch.setattr(AT, "_MAX_CLIENT_LABELS", 4)
    AT.record_failure(AT.LABEL_MASTER_PASSWORD)
    for i in range(10):
        _login(app, {"username": f"u{i}", "password": "x"}, X)
    snap = AT.snapshot()
    assert len([k for k in snap if isinstance(k, tuple)]) <= 4, snap
    assert AT.LABEL_MASTER_PASSWORD in snap  # control: named labels are never evicted


def test_r2_label_flood_does_not_evict_an_escalated_backoff(app, monkeypatch):
    """Minting fresh labels (bogus usernames, one PBKDF2 each) must not push the
    attacker's own escalated (admin1, X) backoff out of the bounded table, in
    cooldown or after it has expired."""
    _admin(app)
    _throttle(monkeypatch)
    now = _clock(monkeypatch)
    monkeypatch.setattr(AT, "_MAX_CLIENT_LABELS", 4)
    assert [_login(app, BAD, X).status_code for _ in range(4)] == [401, 401, 401, 429]
    for i in range(10):
        _login(app, {"username": f"u{i}", "password": "x"}, X)
    assert _login(app, BAD, X).status_code == 429  # still in cooldown
    now[0] += 61  # cooldown over; the count must survive another flood
    for i in range(10, 20):
        _login(app, {"username": f"u{i}", "password": "x"}, X)
    assert _login(app, BAD, X).status_code == 401
    r = _login(app, BAD, X)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) == 121, r.headers.get("Retry-After")



# ── r3: check and count in one step (R180-A F3 MED: a threaded burst) ───

def _burst(app, monkeypatch, n, envs):
    """n concurrent bad logins (address envs[i % len]); every verify that starts
    is held until each request is either answered or inside verify_password, so
    all n are in the check/verify window together. Returns (codes, verifies)."""
    go, cv = threading.Event(), threading.Condition()
    entered, codes = [0], []

    def held_verify(*a, **k):
        with cv:
            entered[0] += 1
            cv.notify_all()
        go.wait(timeout=20)
        return False
    monkeypatch.setattr(UA, "verify_password", held_verify)

    def one(i):
        code = _login(app, BAD, envs[i % len(envs)]).status_code
        with cv:
            codes.append(code)
            cv.notify_all()
    ts = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in ts:
        t.start()
    with cv:
        cv.wait_for(lambda: len(codes) + entered[0] == n, timeout=20)
    go.set()
    for t in ts:
        t.join(60)
    return codes, entered[0]


@pytest.mark.parametrize("n,envs", [(40, [X]), (100, [X]), (40, [X, Y])],
                         ids=["burst40", "burst100", "burst40_two_addresses"])
def test_r3_f3_burst_gets_no_more_401_than_sequential(app, monkeypatch, n, envs):
    """R180-A P4: the same n guesses sent one by one, then all at once (one
    address: the pair key; two addresses: the per-user ceiling)."""
    _admin(app)
    _throttle(monkeypatch)
    _clock(monkeypatch)
    seq = [_login(app, BAD, envs[i % len(envs)]).status_code for i in range(n)]
    seq_401 = seq.count(401)
    assert seq_401 == 3 and seq.count(429) == n - 3, seq  # control: sequential is throttled
    AT.reset()
    codes, verifies = _burst(app, monkeypatch, n, envs)
    assert len(codes) == n, codes
    assert codes.count(401) <= seq_401 and codes.count(429) == n - codes.count(401), \
        (sorted(codes), seq_401)
    assert verifies == 1, verifies  # one verify in flight per label; a refused attempt costs no PBKDF2


def test_r3_successes_never_build_the_user_ceiling(app, monkeypatch):
    """The attempt is counted before the verify; a success must not leave it
    on the user label, or the real user's own logins lock it."""
    _admin(app)
    _throttle(monkeypatch)
    _clock(monkeypatch)
    ok = {"username": "admin1", "password": PW}
    codes = [_login(app, ok, {"REMOTE_ADDR": f"10.2.0.{i}"}).status_code for i in range(6)]
    assert codes == [200] * 6, codes
    assert [_login(app, BAD, X).status_code for _ in range(4)] == [401, 401, 401, 429]


def test_r3_bounded_table_never_drops_the_attempt_being_counted(app, monkeypatch):
    """At the label bound, counting the user label must not evict the pair
    label of the same attempt (or the guesses are never throttled)."""
    _admin(app)
    _throttle(monkeypatch)
    _clock(monkeypatch)
    monkeypatch.setattr(AT, "_MAX_CLIENT_LABELS", 1)
    assert [_login(app, BAD, X).status_code for _ in range(4)] == [401, 401, 401, 429]


def test_r3_verify_exception_stays_counted_and_frees_the_key(app, monkeypatch):
    _admin(app)
    _throttle(monkeypatch)
    _clock(monkeypatch)
    real = UA.verify_password

    def boom(*a, **k):
        raise OSError("store read failed")
    monkeypatch.setattr(UA, "verify_password", boom)
    with pytest.raises(OSError):
        _login(app, BAD, X)
    monkeypatch.setattr(UA, "verify_password", real)
    # the failed verify counts (fail closed) and leaves nothing in flight
    assert [_login(app, BAD, X).status_code for _ in range(3)] == [401, 401, 429]


# ── r4: a long flood never breaks login (R224-A F4 MED) ─────────────────

OK = {"username": "admin1", "password": PW}


def _flood_setup(app, monkeypatch):
    _admin(app)
    _throttle(monkeypatch)
    monkeypatch.setenv("BD_AUTH_THROTTLE_MAX", "300")
    real = UA.verify_password  # a wrong password fails without PBKDF2: a fast flood
    monkeypatch.setattr(UA, "verify_password", lambda u, p: p == PW and real(u, p))
    return _clock(monkeypatch)


def _code(app, body, env):
    try:
        return _login(app, body, env).status_code
    except Exception as e:  # testing mode propagates what would be a 500
        return f"500:{type(e).__name__}"


def _held():
    return [k for k, v in AT.snapshot().items() if "held" in v]


@pytest.mark.parametrize("step,n_addr", [(301.0, 1), (61.0, 5)], ids=["pair", "user"])
def test_r4_f4_long_flood_on_one_username_never_500s(app, monkeypatch, step, n_addr):
    """R224-A P6: past over=1024, base * 2**(over-1) overflowed the float: every
    later login for the username was a 500 and its pair stayed held. pair: one
    address, each guess after the pair cooldown; user: 5 addresses, each guess
    after the user ceiling (BASE) and inside the MAX window."""
    now = _flood_setup(app, monkeypatch)
    codes = []
    for i in range(1030):
        now[0] += step
        codes.append(_code(app, BAD, {"REMOTE_ADDR": f"10.4.0.{i % n_addr}"}))
    assert set(codes) == {401}, {c: codes.count(c) for c in set(codes)}
    assert not _held(), _held()
    now[0] += 301  # after the window
    assert [_code(app, OK, e) for e in ({"REMOTE_ADDR": "10.4.0.0"}, Y)] == [200, 200]


def test_r4_user_ceiling_failures_drop_out_of_the_window(app, monkeypatch):
    """The per-user label counts only the failures of the last MAX seconds."""
    now = _flood_setup(app, monkeypatch)

    def spread(tag, n):
        return [_code(app, BAD, {"REMOTE_ADDR": f"10.5.{tag}.{i}"}) for i in range(n)]
    assert spread(0, 4) == [401, 401, 401, 429]  # control: the ceiling is armed
    now[0] += 299
    assert spread(1, 2) == [401, 429]  # control: inside the window they still count
    now[0] += 302  # every failure is now older than MAX
    assert spread(2, 4) == [401, 401, 401, 429]


def test_r4_success_clears_the_user_label(app, monkeypatch):
    """A correct password drops the per-user label (pop), not just its own attempt."""
    _flood_setup(app, monkeypatch)
    assert [_code(app, BAD, {"REMOTE_ADDR": f"10.6.0.{i}"}) for i in range(2)] == [401, 401]
    assert _code(app, OK, Y) == 200
    assert not [k for k in AT.snapshot() if k[1] == "user"], AT.snapshot()
    assert [_code(app, BAD, {"REMOTE_ADDR": f"10.6.1.{i}"}) for i in range(4)] == [401, 401, 401, 429]


def test_r4_success_with_throttle_off_still_clears_the_labels(app, monkeypatch):
    """Turning the throttle off and on never leaves a stale lockout behind: a
    success while it is off still drops both labels."""
    _flood_setup(app, monkeypatch)
    assert [_code(app, BAD, X) for _ in range(4)] == [401, 401, 401, 429]
    monkeypatch.delenv("BD_AUTH_THROTTLE")
    assert _code(app, OK, X) == 200
    assert not [k for k in AT.snapshot() if k[2] == "admin1"], AT.snapshot()
    monkeypatch.setenv("BD_AUTH_THROTTLE", "1")
    assert _code(app, OK, X) == 200


def _raise_in_reserve(monkeypatch):
    """Counting the user label raises after the pair is already marked in flight
    (the r3 F4 chain: OverflowError there)."""
    real = AT._count

    def flaky(label, *a, **k):
        if label[1] == "user":
            raise OverflowError("int too large to convert to float")
        return real(label, *a, **k)
    monkeypatch.setattr(AT, "_count", flaky)
    return lambda: monkeypatch.setattr(AT, "_count", real)


def _raise_after_reserve(monkeypatch):
    """reserve() succeeds, then the request fails before its verify."""
    real = AT.reserve

    def reserve_then_fail(*a, **k):
        real(*a, **k)
        raise RuntimeError("request aborted after reserve")
    monkeypatch.setattr(AT, "reserve", reserve_then_fail)
    return lambda: monkeypatch.setattr(AT, "reserve", real)


@pytest.mark.parametrize("inject", [_raise_in_reserve, _raise_after_reserve],
                         ids=["inside_reserve", "after_reserve"])
def test_r4_exception_between_reserve_and_release_leaves_no_held_label(app, monkeypatch, inject):
    _flood_setup(app, monkeypatch)
    undo = inject(monkeypatch)
    assert str(_code(app, BAD, X)).startswith("500:")
    assert not _held(), AT.snapshot()
    undo()
    assert _code(app, OK, X) == 200


# ── corrupt store: clean refusal, not a server error ────────────────────

def test_store_corrupt_whoami_and_bootstrap_answer_503(app):
    with open(UA.ACCOUNTS_FILE, "w") as fh:
        fh.write("{not json")
    c = app.test_client()
    for r in (c.get("/api/auth/whoami"), _create(c, "intruder", role="admin")):
        assert r.status_code == 503 and r.get_json()["error"] == UA._UNREADABLE
    with open(UA.ACCOUNTS_FILE) as fh:
        assert fh.read() == "{not json"


def test_store_ctl_missing_store_is_bootstrap(app):
    assert not os.path.exists(UA.ACCOUNTS_FILE)
    assert app.test_client().get("/api/auth/whoami").get_json()["bootstrap"] is True
