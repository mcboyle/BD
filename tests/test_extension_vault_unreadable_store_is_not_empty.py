"""An unreadable extension-vault token store is UNKNOWN, never empty.

`extension_vault._load_tokens` wrapped `VAULT_TOKENS_FILE.read_text` and
`json.loads` in one blanket `except Exception`, renamed the operator's LIVE
token store to `vault_tokens.json.corrupt-<ts>`, and returned a fresh empty
structure.  This is the same rename-aside shape AF2 carried in
`secrets_store`, and the trigger is equally ordinary: `Path.replace()` needs
DIRECTORY permission, not file permission, so a chmod-000 file or a transient
EIO renamed the store away exactly as readily as a torn write did.

MEASURED on the defective tree, over a store holding one live redeemed token:

    list_vault_tokens()              []      the LIVE STORE WAS RENAMED AWAY
    vault_tokens.json still present  False
    validate_vault_token(real token) False   a paired extension is unpaired
    revoke_vault_token(real token)   False   phantom "already gone"
    then issue_pairing_token()               publishes a NEW store over the name

Both damage shapes -- unparseable bytes and chmod 000 -- produced that table
identically.

CLAUDE.md A7: an unavailable measurement is UNKNOWN, never OK.  The store must
survive byte-identical under its own name, `unreadable` must be its own state
distinct from `absent` and `ok`, and every store-touching entry point must
refuse rather than answer over data nothing read.

DELIBERATELY DIFFERENT FROM THE `secrets_store` TREATMENT: nothing here caches
the load.  `_load_tokens` runs per call, so repair takes effect on the very
next request and the diagnostic must NOT tell the operator to restart.
`test_repairing_the_file_works_immediately_without_a_restart` pins that.

Every token literal below is a documented zero-entropy synthetic value.  None
is a credential.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bulk_downloader import extension_vault as ev


BD_GATE_SCOPE = "module"

# Documented zero-entropy synthetic values.  None of these is a credential.
_LABEL = "unreadable-store-synthetic-label"
_ABSENT_TOKEN = "synthetic-token-that-was-never-issued"
_ENTRY_KEY = "bulkdl-site-synthetic"
_UNPARSEABLE = b"{synthetic not valid json"


# ── fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def vault(monkeypatch, tmp_path):
    """Point the module at an isolated store. Returns the store path."""
    store = tmp_path / "vault_tokens.json"
    monkeypatch.setattr(ev, "VAULT_TOKENS_FILE", store)
    monkeypatch.setattr(ev, "VAULT_AUDIT_LOG", tmp_path / "vault_access.log")
    return store


def _seed_one_token(store: Path) -> str:
    """PRECONDITION: build a store that really holds one live redeemed token.

    Asserted rather than assumed -- a damage test over an empty store would
    pass vacuously.
    """
    pairing = ev.issue_pairing_token()
    token = ev.redeem_pairing_token(pairing, extension_label=_LABEL)
    assert token, "precondition: the pairing token must redeem"
    assert store.exists(), "precondition: the store must be on disk"
    on_disk = json.loads(store.read_text(encoding="utf-8"))
    assert len(on_disk["redeemed"]) == 1, on_disk
    assert ev.validate_vault_token(token) is not None, (
        "precondition: the seeded token must validate")
    assert len(ev.list_vault_tokens()) == 1, (
        "precondition: exactly one paired extension")
    return token


def _damage_unparseable(store: Path) -> bytes:
    store.write_bytes(_UNPARSEABLE)
    return _UNPARSEABLE


def _damage_unreadable(store: Path) -> bytes:
    original = store.read_bytes()
    os.chmod(store, 0o000)
    # Assert the precondition rather than assume it: as root, chmod 000 does
    # not deny reads and the whole shape would be vacuous.
    with pytest.raises(PermissionError):
        store.read_text(encoding="utf-8")
    return original


_SHAPES = {
    "unparseable": _damage_unparseable,
    "chmod-000": _damage_unreadable,
}


def _read_back(store: Path) -> bytes:
    """Read the store's bytes regardless of the mode a damage shape set."""
    os.chmod(store, 0o600)
    return store.read_bytes()


# Every entry point that touches the durable store. Each is (name, thunk).
def _store_touching_calls() -> list[tuple[str, object]]:
    return [
        ("_load_tokens", lambda: ev._load_tokens()),
        ("issue_pairing_token", lambda: ev.issue_pairing_token()),
        ("redeem_pairing_token",
         lambda: ev.redeem_pairing_token(_ABSENT_TOKEN, _LABEL)),
        ("revoke_vault_token", lambda: ev.revoke_vault_token(_ABSENT_TOKEN)),
        ("revoke_by_prefix", lambda: ev.revoke_by_prefix("abcd")),
        ("list_vault_tokens", lambda: ev.list_vault_tokens()),
        ("validate_vault_token",
         lambda: ev.validate_vault_token(_ABSENT_TOKEN)),
        ("check_and_record_fetch",
         lambda: ev.check_and_record_fetch(_ABSENT_TOKEN, _ENTRY_KEY)),
    ]


# ── the defect, both damage shapes ──────────────────────────────────


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_a_damaged_store_is_preserved_in_place(vault, shape):
    """The rename is gone: byte-identical, under its own name, unmutated.

    Deliberately phrased over the PRE-EXISTING API only, so that on the
    defective base this fails for the defect ("the damaged store must survive
    under its own name") rather than for a missing new symbol.
    """
    _seed_one_token(vault)
    expected = _SHAPES[shape](vault)
    mtime = vault.stat().st_mtime_ns

    # Any store-touching call triggers the load. What it returns or raises is
    # a separate question, asserted elsewhere; here only the file matters.
    try:
        ev.list_vault_tokens()
    except Exception:
        pass

    assert vault.exists(), "the damaged store must survive under its own name"
    assert _read_back(vault) == expected, "the bytes must be untouched"
    assert vault.stat().st_mtime_ns == mtime, "the store must not be rewritten"
    assert sorted(vault.parent.glob("vault_tokens.json.corrupt-*")) == [], (
        "the move-aside sibling is the defect; it must not be created")


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_every_store_touching_call_refuses(vault, shape):
    """Exact counts: all 8 refuse, and each names the distinctive class."""
    _seed_one_token(vault)
    expected = _SHAPES[shape](vault)

    calls = _store_touching_calls()
    assert len(calls) == 8, "the denominator changed; update this gate"
    refused, answered = [], []
    for name, thunk in calls:
        try:
            thunk()
        except ev.VaultTokensUnreadableError as e:
            # The distinctive diagnostic, not merely the exit shape.
            assert "could not be read" in str(e), (name, str(e))
            assert "NOT reinitialized" in str(e), (name, str(e))
            refused.append(name)
        except Exception as e:  # pragma: no cover - a wrong refusal is a bug
            answered.append((name, f"{type(e).__name__}: {e}"))
        else:
            answered.append((name, "returned normally"))

    assert answered == [], f"these answered over an unread store: {answered}"
    assert len(refused) == 8, refused
    # And nothing wrote: the file is still exactly what the damage left.
    assert _read_back(vault) == expected


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_issuing_cannot_publish_a_new_store_over_the_damaged_one(vault, shape):
    """The measured consequence: a fresh store replacing the operator's."""
    _seed_one_token(vault)
    expected = _SHAPES[shape](vault)

    # Pre-existing API only, for the same base-attribution reason as above.
    try:
        ev.issue_pairing_token()
    except Exception:
        pass

    assert _read_back(vault) == expected, (
        "issue_pairing_token published a new store over the damaged one")
    # No .tmp residue either -- _save_tokens must never have been reached.
    assert sorted(p.name for p in vault.parent.iterdir()) == [
        "vault_tokens.json"], sorted(p.name for p in vault.parent.iterdir())


def test_an_unreadable_parent_directory_is_not_absent(vault, tmp_path):
    """`Path.exists()` only swallows ENOENT/ENOTDIR/EBADF/ELOOP.

    An unreadable PARENT raises PermissionError out of `exists()` -- which on
    the defective tree escaped `_load_tokens` as a bare OSError, since the
    `exists()` call sat outside its try. Unmeasurable is never absent.
    """
    locked = tmp_path / "locked"
    locked.mkdir()
    ev.VAULT_TOKENS_FILE = locked / "vault_tokens.json"
    ev.VAULT_AUDIT_LOG = locked / "vault_access.log"
    token = _seed_one_token(ev.VAULT_TOKENS_FILE)
    assert token
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(PermissionError):
            ev.VAULT_TOKENS_FILE.exists()
        with pytest.raises(ev.VaultTokensUnreadableError):
            ev._load_tokens()
        assert ev.store_state() == "unreadable"
    finally:
        os.chmod(locked, 0o700)


# ── every state reachable and distinguishable, with exact counts ────


def test_the_three_states_are_reachable_and_mutually_exclusive(vault):
    """absent / ok / unreadable: each reached once, all three distinct."""
    observed = []

    # absent -- no file has ever been written
    assert not vault.exists()
    observed.append(ev.store_state())

    # ok -- a real store holding one live token
    token = _seed_one_token(vault)
    observed.append(ev.store_state())

    # unreadable -- damaged in place
    _damage_unparseable(vault)
    observed.append(ev.store_state())

    assert observed == ["absent", "ok", "unreadable"], observed
    assert len(set(observed)) == 3, "the states must be mutually exclusive"
    assert token  # the ok state really held a token, not an empty structure


# ── negative control 1: a genuinely absent store is first use ───────


def test_a_genuinely_absent_store_still_reads_as_absent(vault):
    """Over-sensitivity control: a missing file must NOT become unreadable.

    Failing closed on absence would break every first pairing on a fresh
    host -- a fix that refuses everything passes the damage tests above and
    destroys the product. This is the assertion that would catch it.
    """
    assert not vault.exists()
    assert ev._load_tokens() == {"pairing": {}, "redeemed": {}}

    # And first use works end to end, writing the store for the first time.
    pairing = ev.issue_pairing_token()
    assert pairing
    assert vault.exists(), "first use must create the store"
    token = ev.redeem_pairing_token(pairing, extension_label=_LABEL)
    assert token
    assert ev.validate_vault_token(token) is not None
    assert len(ev.list_vault_tokens()) == 1


# ── negative control 2: a healthy store still works normally ────────


def test_a_healthy_store_still_works_normally(vault):
    """Over-sensitivity control: the whole ordinary lifecycle is untouched."""
    token = _seed_one_token(vault)

    paired = ev.list_vault_tokens()
    assert len(paired) == 1
    assert paired[0]["label"] == _LABEL
    assert paired[0]["id"] == token[:8]

    # An unknown token is still a plain miss -- not an unreadable refusal.
    assert ev.validate_vault_token(_ABSENT_TOKEN) is None
    assert ev.revoke_vault_token(_ABSENT_TOKEN) is False
    assert ev.revoke_by_prefix("zzzz") is False

    allowed, reason = ev.check_and_record_fetch(token, _ENTRY_KEY)
    assert (allowed, reason) == (True, "")

    assert ev.revoke_vault_token(token) is True
    assert ev.validate_vault_token(token) is None
    assert ev.list_vault_tokens() == []


def test_pure_helpers_never_consult_the_store(vault):
    """Over-sensitivity control: the origin-matching half must not refuse."""
    _seed_one_token(vault)
    _damage_unparseable(vault)

    # None of these reads vault_tokens.json, so none may raise.
    assert ev.get_hostname("https://user@Example.COM:8443/x") == "example.com"
    assert ev.get_registrable_domain("https://a.b.example.com/x") == "example.com"
    assert ev.hostname_pattern("https://example.com") == r"example\.com"
    entries = [{"id": "e1", "patterns": [r"example\.com"]}]
    assert ev.entries_matching_origin("https://example.com/login", entries) == entries
    ev.audit_fetch({"label": _LABEL}, _ENTRY_KEY, "https://example.com",
                   False, "synthetic")


# ── repair takes effect immediately: no restart, unlike secrets_store ─


def test_repairing_the_file_works_immediately_without_a_restart(vault):
    """Nothing caches the load, so the diagnostic must not demand a restart."""
    token = _seed_one_token(vault)
    healthy = vault.read_bytes()
    _damage_unparseable(vault)

    with pytest.raises(ev.VaultTokensUnreadableError) as caught:
        ev.list_vault_tokens()
    message = str(caught.value)
    assert "no restart is needed" in message, message
    assert "RESTART" not in message.upper().replace("NO RESTART IS NEEDED", ""), (
        "extension_vault re-reads per call; do not copy secrets_store's "
        "restart requirement: " + message)

    # Restore the operator's file. The very next call must succeed.
    vault.write_bytes(healthy)
    assert ev.store_state() == "ok"
    assert len(ev.list_vault_tokens()) == 1
    assert ev.validate_vault_token(token) is not None


# ── the route seam: every extension endpoint fails closed ───────────


@pytest.fixture
def client(monkeypatch, tmp_path, vault):
    """A Flask test client with the vault store isolated and damaged."""
    from bulk_downloader import app as A

    monkeypatch.delenv("BD_AUTH_TOKEN", raising=False)
    home = tmp_path / "cwd"
    (home / "screenshots").mkdir(parents=True)
    monkeypatch.chdir(home)
    return A.app.test_client()


_BEARER = {"Authorization": f"Bearer {_ABSENT_TOKEN}"}

# (method, path, headers, json body) for every /api/secrets/extension route
# that touches the durable store. `ping`, `list_for_origin` and `fetch_one`
# reach it through _require_vault_token.
_EXTENSION_ROUTES = [
    ("POST", "/api/secrets/extension/pair_issue", {}, {}),
    ("POST", "/api/secrets/extension/pair", {},
     {"pairing_token": _ABSENT_TOKEN, "label": _LABEL}),
    ("GET", "/api/secrets/extension/list_paired", {}, None),
    ("POST", "/api/secrets/extension/revoke", {}, {"id": "abcd1234"}),
    ("GET", "/api/secrets/extension/ping", _BEARER, None),
    ("GET", "/api/secrets/extension/list_for_origin?origin=https://example.com",
     _BEARER, None),
    ("POST", "/api/secrets/extension/fetch_one", _BEARER,
     {"id": "site:synthetic"}),
]


def test_every_extension_route_returns_503_unreadable(client, vault):
    """Exact counts: all 7 routes name the state; none reports success."""
    _seed_one_token(vault)
    expected = _damage_unparseable(vault)

    assert len(_EXTENSION_ROUTES) == 7, "the route denominator changed"
    wrong = []
    for method, path, headers, body in _EXTENSION_ROUTES:
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = client.open(path, method=method, **kwargs)
        payload = response.get_json() or {}
        if response.status_code != 503 or payload.get("state") != "unreadable":
            wrong.append((path, response.status_code, payload))
        assert payload.get("ok") is not True, (path, payload)

    assert wrong == [], f"routes that did not fail closed: {wrong}"
    assert vault.read_bytes() == expected, "a route wrote to the damaged store"


def test_a_management_route_refuses_a_bearer_it_cannot_classify(client, vault):
    """_reject_if_vault_token cannot prove this bearer is not a vault token.

    403 rather than 503 here is deliberate: the refusal is the management
    gate's own, and it must hold even if the 503 mapping were ever removed.
    """
    _seed_one_token(vault)
    _damage_unparseable(vault)

    response = client.get("/api/secrets/extension/list_paired",
                          headers=_BEARER)
    payload = response.get_json() or {}
    assert response.status_code == 403, (response.status_code, payload)
    assert payload.get("state") == "unreadable", payload
    assert payload.get("ok") is False


def test_the_routes_still_work_over_a_healthy_store(client, vault):
    """Over-sensitivity control at the route seam."""
    token = _seed_one_token(vault)

    listed = client.get("/api/secrets/extension/list_paired")
    assert listed.status_code == 200
    assert len(listed.get_json()["extensions"]) == 1

    issued = client.post("/api/secrets/extension/pair_issue")
    assert issued.status_code == 200
    assert issued.get_json()["pairing_token"]

    ping = client.get("/api/secrets/extension/ping",
                      headers={"Authorization": f"Bearer {token}"})
    assert ping.status_code == 200
    assert ping.get_json()["label"] == _LABEL

    # An unknown bearer is still a plain 401, not a 503.
    denied = client.get("/api/secrets/extension/ping", headers=_BEARER)
    assert denied.status_code == 401


def test_an_absent_store_still_lets_the_routes_do_first_use(client, vault):
    """Negative control at the route seam: no file yet is not a refusal."""
    assert not vault.exists()

    listed = client.get("/api/secrets/extension/list_paired")
    assert listed.status_code == 200
    assert listed.get_json() == {"ok": True, "extensions": []}

    issued = client.post("/api/secrets/extension/pair_issue")
    assert issued.status_code == 200
    pairing = issued.get_json()["pairing_token"]

    paired = client.post("/api/secrets/extension/pair",
                         json={"pairing_token": pairing, "label": _LABEL})
    assert paired.status_code == 200
    assert paired.get_json()["vault_token"]
    assert vault.exists()
