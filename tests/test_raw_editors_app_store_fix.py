"""raw-editors-dev-auth app-store-fix: F1, F4 and F5 (bughunt raw-editors-dev-auth FINDINGS.md).

  F1  _dev_request_authorized admitted a remote caller on a token-less box with
      either the anonymous csrf_bootstrap session GET / hands out, or a Referer
      whose host equals the Host header -- three values the caller chooses.
  F4  _session_valid checked only the sliding idle TTL; every authenticated
      request slides it, so a login session never expired while in use.
  F5  POST /api/settings/store-raw wrote a tunnel secret typed into the raw
      editor straight into tunnels.json, bypassing secrets_store (the VPN save
      path moves it to the vault as a @cred ref, or refuses with 503).

Each repro sits beside the controls that show the probe can say yes: the real
token, a real login session, loopback, a @cred ref and the widgets store.
"""

import json
import time

import pytest

from bulk_downloader import app as bd_app

BD_GATE_SCOPE = "module"

TOKEN = "fixture-raw-editors-token"  # a test fixture value, not a credential
CANARY = "FAKE-CANARY-not-a-key"  # a test fixture value, not a key
READ = "/api/global_config"
DEV = "/api/dev/runs/no-such-run"  # guarded first; admitted -> 404 unknown run
REMOTE = {"REMOTE_ADDR": "10.0.0.99"}
SAME_HOST = {"Host": "bd.local:5555", "Referer": "http://bd.local:5555/"}
DEV_REFUSAL = "dev endpoints require"
DAY = 24 * 3600

F1_ADMITTED = "F1_REMOTE_DEV_REQUEST_ADMITTED"
F4_STALE_ADMITTED = "F4_SESSION_PAST_ABSOLUTE_LIFETIME_ADMITTED"
F5_SECRET_ON_DISK = "F5_PLAINTEXT_SECRET_IN_TUNNELS_JSON"


@pytest.fixture
def no_token(monkeypatch):
    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setenv("BD_DEV_MODE", "1")
    assert bd_app._accepted_tokens() == [], "COULD NOT LOOK: a token is configured here"
    return bd_app.app.test_client()


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setenv("BD_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("BD_TOKEN", raising=False)
    monkeypatch.setenv("BD_DEV_MODE", "1")
    assert bd_app._accepted_tokens() == [TOKEN], "COULD NOT LOOK: the token is not what the app reads"
    return bd_app.app.test_client()


def _login_session(**fields):
    sess = bd_app._session_create(source="pair_redeem")
    with bd_app._session_lock:
        bd_app._sessions[sess].update(fields)
    return sess


def _dev_refused(r):
    body = r.get_data(as_text=True)
    assert r.status_code == 403 and DEV_REFUSAL in body, f"{F1_ADMITTED}: {r.status_code} {body[:120]}"


def _dev_admitted(r):
    body = r.get_data(as_text=True)
    assert r.status_code != 403 and DEV_REFUSAL not in body, f"control refused: {r.status_code} {body[:120]}"


# ── F1: the dev guard ─────────────────────────────────────────────────────────────────────────────


def test_f1_anonymous_bootstrap_session_is_refused(no_token):
    no_token.set_cookie("bd_session", bd_app._session_create(source="csrf_bootstrap"))
    _dev_refused(no_token.get(DEV, environ_base=REMOTE))


def test_f1_bootstrap_session_from_get_root_is_refused(no_token):
    no_token.get("/", environ_base=REMOTE)  # mints the anonymous bd_session cookie
    assert no_token.get_cookie("bd_session") is not None, "COULD NOT LOOK: GET / set no bd_session"
    _dev_refused(no_token.get(DEV, environ_base=REMOTE, headers=SAME_HOST))


def test_f1_referer_equal_to_host_is_refused(no_token):
    _dev_refused(no_token.get(DEV, environ_base=REMOTE, headers=SAME_HOST))


def test_f1_refusal_tells_the_operator_what_to_do(no_token):
    r = no_token.get(DEV, environ_base=REMOTE)
    assert r.status_code == 403
    assert "log in or set a token" in r.get_json()["error"], f"F1_REFUSAL_TEXT: {r.get_json()}"


def test_f1_paired_login_session_is_refused_with_the_master_token_message(no_token):
    # dev-paired-session-scope (PM 1240Z) reversed this admission: the only login
    # source is pair_redeem, and a paired device gets the app API, not /api/dev/*.
    no_token.set_cookie("bd_session", _login_session())
    r = no_token.get(DEV, environ_base=REMOTE)
    assert r.status_code == 403, f"PAIRED_SESSION_ADMITTED: {r.status_code}"
    assert r.get_json()["error"] == "dev tools need the master token on a paired device"


@pytest.mark.parametrize("header", ["Authorization", "X-BD-Token"])
def test_f1_token_is_admitted(with_token, header):
    value = f"Bearer {TOKEN}" if header == "Authorization" else TOKEN
    _dev_admitted(with_token.get(DEV, environ_base=REMOTE, headers={header: value}))


@pytest.mark.parametrize("headers", [{}, SAME_HOST])
def test_f1_loopback_is_admitted_as_before(no_token, headers):
    _dev_admitted(no_token.get(DEV, headers=headers))  # test-client peer is 127.0.0.1


def test_f1_unauthenticated_remote_is_refused_control(no_token):
    _dev_refused(no_token.get(DEV, environ_base=REMOTE))


# ── F4: absolute session lifetime ─────────────────────────────────────────────────────────────────


def test_f4_session_in_use_for_thirty_days_is_refused(with_token):
    now = time.time()
    sess = _login_session(created=now - 30 * DAY, last_used=now)
    with_token.set_cookie("bd_session", sess)
    r = with_token.get(READ, environ_base=REMOTE)
    assert r.status_code == 401, f"{F4_STALE_ADMITTED}: {r.status_code}"
    assert not bd_app._session_valid(sess)
    with bd_app._session_lock:
        assert sess not in bd_app._sessions, "an expired session record must be dropped"


def test_f4_absolute_lifetime_boundary():
    ttl = getattr(bd_app, "SESSION_ABSOLUTE_TTL", None)
    assert isinstance(ttl, (int, float)) and 0 < ttl < 30 * DAY, f"{F4_STALE_ADMITTED}: SESSION_ABSOLUTE_TTL={ttl!r}"
    now = time.time()
    assert bd_app._session_valid(_login_session(created=now - ttl + 60, last_used=now))
    assert not bd_app._session_valid(_login_session(created=now - ttl - 60, last_used=now))


def test_f4_session_without_created_is_refused():
    sess = _login_session()
    with bd_app._session_lock:
        del bd_app._sessions[sess]["created"]
    assert not bd_app._session_valid(sess), f"{F4_STALE_ADMITTED}: no 'created' admitted"


def test_f4_fresh_login_session_is_admitted(with_token):
    now = time.time()
    with_token.set_cookie("bd_session", _login_session(created=now - 3600, last_used=now))
    r = with_token.get(READ, environ_base=REMOTE)
    assert r.status_code == 200, f"control refused: {r.status_code}"


def test_f4_idle_session_is_still_refused(with_token):
    now = time.time()
    sess = _login_session(created=now - 60, last_used=now - bd_app.SESSION_IDLE_TTL - 5)
    with_token.set_cookie("bd_session", sess)
    assert with_token.get(READ, environ_base=REMOTE).status_code == 401


# ── F5: raw store editor and the vault ────────────────────────────────────────────────────────────


class _MemBackend:
    name = "memory_test"

    def __init__(self, refuse=False):
        self._d, self._refuse = {}, refuse

    def set(self, key, password):
        if self._refuse:
            raise RuntimeError("vault locked (fixture)")
        self._d[key] = password

    def get(self, key):
        return self._d.get(key)

    def delete(self, key):
        return self._d.pop(key, None) is not None

    def list_keys(self):
        return list(self._d)

    def is_unlocked(self):
        return not self._refuse


class _PlaintextBackend(_MemBackend):
    name = "plaintext"


@pytest.fixture
def store(monkeypatch, tmp_path):
    """Bare Flask app with the raw editor; tunnels.json/widgets.json in tmp_path; vpn_config's
    module state and the secrets backend restored at teardown."""
    from flask import Flask

    from bulk_downloader import app_store_raw_editor as SR
    from bulk_downloader import secrets_store, vpn_config, widgets_config

    monkeypatch.setenv("BD_VPN_CONFIG_PATH", str(tmp_path / "tunnels.json"))
    monkeypatch.setenv("BD_WIDGETS_CONFIG_PATH", str(tmp_path / "widgets.json"))
    for mod in (vpn_config, widgets_config):
        monkeypatch.setattr(mod, "_state", json.loads(json.dumps(mod._state)))
        monkeypatch.setattr(mod, "_loaded", False)
    monkeypatch.setattr(secrets_store, "_backend", None)
    app = Flask(__name__)
    SR.register_routes(app)

    def use(backend):
        secrets_store._backend = backend
        return backend

    yield app.test_client(), tmp_path / "tunnels.json", use
    for mod in (vpn_config, widgets_config):
        mod._loaded = False


def _tunnel_text(value):
    t = {"tunnel_id": "t1", "name": "n", "provider": "p", "backend": "wireguard",
         "config": {"private_key": value, "endpoint": "198.51.100.7:51820"}}
    return json.dumps({"schema_version": 1, "tunnels": [t]})


def _post(client, value, store_name="vpn"):
    text = _tunnel_text(value) if store_name == "vpn" else value
    return client.post("/api/settings/store-raw", json={"store": store_name, "text": text})


def test_f5_secret_moves_to_the_vault(store):
    client, path, use = store
    vault = use(_MemBackend())
    r = _post(client, CANARY)
    assert r.status_code == 200, r.get_json()
    on_disk = path.read_text(encoding="utf-8")
    assert CANARY not in on_disk, f"{F5_SECRET_ON_DISK}: {path}"
    assert json.loads(on_disk)["tunnels"][0]["config"]["private_key"] == "@cred:t1:private_key"
    assert vault._d == {"t1:private_key": CANARY}
    assert "198.51.100.7:51820" in on_disk, "a non-secret field must stay inline"


def test_f5_refusing_vault_refuses_the_write(store):
    client, path, use = store
    use(_MemBackend(refuse=True))
    path.write_text('{"schema_version": 1, "tunnels": []}', encoding="utf-8")
    before = path.read_bytes()
    r = _post(client, CANARY)
    assert r.status_code == 503, f"{F5_SECRET_ON_DISK}: {r.status_code} {r.get_json()}"
    assert r.get_json()["note"] == "no write performed"
    assert path.read_bytes() == before, "a refused write must leave tunnels.json byte-identical"
    assert not path.with_suffix(".json.tmp").exists()


def test_f5_cred_ref_passes_unchanged(store):
    client, path, use = store
    vault = use(_MemBackend())
    vault.set("t1:private_key", CANARY)
    r = _post(client, "@cred:t1:private_key")
    assert r.status_code == 200, r.get_json()
    assert json.loads(path.read_text(encoding="utf-8"))["tunnels"][0]["config"]["private_key"] == "@cred:t1:private_key"
    assert vault._d == {"t1:private_key": CANARY}


def test_f5_plaintext_backend_keeps_the_value_inline(store):
    # Parity with vpn_config.store_secrets: a plaintext backend has no storage,
    # so the value stays inline, exactly as the VPN save path keeps it.
    client, path, use = store
    use(_PlaintextBackend())
    assert _post(client, CANARY).status_code == 200
    assert CANARY in path.read_text(encoding="utf-8")


def test_f5_widgets_store_is_untouched_by_the_vault(store):
    client, _path, use = store
    use(_MemBackend(refuse=True))
    r = _post(client, json.dumps({"global": [], "per_site": {}}), store_name="widgets")
    assert r.status_code == 200, r.get_json()
