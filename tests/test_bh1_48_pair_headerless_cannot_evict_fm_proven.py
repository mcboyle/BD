"""BH1-48 (RULING-pm-B-A3, option b): a headerless GET /api/pair cannot evict
the operator's Fetch-Metadata-proven pairing code.

aac-fu1 A3 stays DECIDED OPEN: a GET with no Fetch Metadata still mints (curl,
bdctl and the bootstrap fixtures rely on it). What closes is the eviction DoS:
at BASE a blind headerless page flooded the PAIRING_TOKENS_MAX cap and the
oldest code -- the operator's pending one -- was dropped. Now a headerless mint
evicts only headerless codes, oldest first, and is refused when every slot
holds a proven (Sec-Fetch-Site same-origin / none) code. A proven mint keeps
today's eviction order.
"""
import sqlite3

import pytest

from bulk_downloader import api_tokens, app_pair, app_state, db, global_config, shares
from bulk_downloader import app as app_mod
from bulk_downloader.app import app

BD_GATE_SCOPE = "module"  # O906.1 gate marker (precollect guard)

_SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """Private config store, sqlite DB, no auth token, and a snapshot of the
    in-process session / pairing stores (same isolation as aac-fu1)."""
    monkeypatch.setattr(global_config, "_CONFIG_FILE", tmp_path / "app_config.json")
    monkeypatch.setattr(global_config, "_cached", None)
    monkeypatch.setattr(global_config, "_cached_mtime", 0.0)
    for name in ("BD_AUTH_TOKEN", "BD_TOKEN", "BD_DEV_MODE_DISABLE"):
        monkeypatch.delenv(name, raising=False)
    for key in ("auth_token", "pair_lan_ip", "pair_port"):
        monkeypatch.delitem(app_mod._app_cfg, key, raising=False)
    database = tmp_path / "bh1_48.db"
    real_db_conn = db.db_conn

    def db_conn(*args, **kwargs):
        if args or kwargs:
            return real_db_conn(*args, **kwargs)
        cx = sqlite3.connect(database)
        cx.row_factory = sqlite3.Row
        return cx

    monkeypatch.setattr(db, "db_conn", db_conn)
    if hasattr(api_tokens._signing_secret, "_fb"):
        monkeypatch.delattr(api_tokens._signing_secret, "_fb")
    if hasattr(shares._signing_secret, "_fb"):
        monkeypatch.delattr(shares._signing_secret, "_fb")
    sessions = dict(app_mod._sessions)
    pairing = dict(app_state._pairing_tokens)
    app_state._pairing_tokens.clear()
    yield tmp_path
    app_mod._sessions.clear()
    app_mod._sessions.update(sessions)
    app_state._pairing_tokens.clear()
    app_state._pairing_tokens.update(pairing)


def _mint(c, headers=None):
    r = c.get("/api/pair", headers=headers or {})
    return r, (r.get_json() or {}).get("token")


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_bh1_48_headerless_flood_cannot_evict_fm_proven_code(iso, site):
    cap = app_pair.PAIRING_TOKENS_MAX
    c = app.test_client()
    r, mine = _mint(c, {"Sec-Fetch-Site": site})
    assert r.status_code == 200 and mine, r.status_code
    statuses = [_mint(c)[0].status_code for _ in range(cap + 1)]
    assert mine in app_state._pairing_tokens, \
        f"BH1-48-HEADERLESS-EVICTED-FM-PROVEN site={site} statuses={set(statuses)}"
    assert set(statuses) == {200}, f"BH1-48-HEADERLESS-MINT-REFUSED statuses={set(statuses)}"
    assert len(app_state._pairing_tokens) <= cap


def test_bh1_48_headerless_mint_refused_when_store_all_fm_proven(iso):
    cap = app_pair.PAIRING_TOKENS_MAX
    c = app.test_client()
    proven = [_mint(c, _SAME_ORIGIN)[1] for _ in range(cap)]
    before = dict(app_state._pairing_tokens)
    r, tok = _mint(c)
    assert r.status_code == 403 and not tok, \
        f"BH1-48-HEADERLESS-MINT-ADMITTED-OVER-PROVEN status={r.status_code}"
    assert r.get_json()["ok"] is False and r.get_json()["error"]
    assert app_state._pairing_tokens == before and all(t in before for t in proven), \
        "BH1-48-HEADERLESS-REFUSAL-MUTATED-STORE"


def test_bh1_48_headerless_mint_evicts_only_headerless_codes(iso):
    cap = app_pair.PAIRING_TOKENS_MAX
    c = app.test_client()
    proven, headerless = [], []
    for i in range(cap):   # oldest slot is a proven code
        if i % 2 == 0:
            proven.append(_mint(c, _SAME_ORIGIN)[1])
        else:
            headerless.append(_mint(c)[1])
    r, tok = _mint(c)
    assert r.status_code == 200 and tok in app_state._pairing_tokens, r.status_code
    gone = [t for t in proven if t not in app_state._pairing_tokens]
    assert not gone, f"BH1-48-HEADERLESS-EVICTED-FM-PROVEN n={len(gone)}"
    kept = headerless[0] in app_state._pairing_tokens
    assert not kept, "BH1-48-OLDEST-HEADERLESS-KEPT"
    assert all(t in app_state._pairing_tokens for t in headerless[1:])
    assert len(app_state._pairing_tokens) == cap


def test_bh1_48_control_fm_proven_mint_keeps_oldest_first_eviction(iso):
    # Control (GREEN at BASE and after): a proven mint evicts the oldest code,
    # proven or not -- today's order.
    cap = app_pair.PAIRING_TOKENS_MAX
    c = app.test_client()
    first = _mint(c, _SAME_ORIGIN)[1]
    rest = [_mint(c)[1] for _ in range(cap - 1)]
    r, tok = _mint(c, _SAME_ORIGIN)
    assert r.status_code == 200 and tok in app_state._pairing_tokens, r.status_code
    assert first not in app_state._pairing_tokens, "BH1-48-PROVEN-MINT-ORDER-CHANGED"
    assert all(t in app_state._pairing_tokens for t in rest)
