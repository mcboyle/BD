"""v3.66.507 — Bucket 3b: raw vpn/widgets store-metadata editor + tunnel rekey.

vpn.* and widgets.* metadata (schema_version, tunnel_id, _saved_at) live in
SEPARATE stores (tunnels.json / widgets.json), not global_config — so the GUI
path is a raw JSON store-file editor (GET/POST /api/settings/store-raw), not a
per-key control. Operator directive: raw-editable.

Decisions taken (documented recs):
  * _saved_at — kept display-only (auto-stamped by save(); a manual value is
    transient). schema_version (x2) + tunnel_id are promoted to full.
  * tunnel_id — R1: the raw editor BLOCKS a tunnel_id change/removal that would
    orphan stored secrets (400, file byte-identical). R2: a dedicated atomic
    rekey action (POST /api/settings/store-raw/rekey) moves @cred:OLD:* -> NEW:*.
  * raw whole-file editor (no per-key meta API exists).

RED-first: every assertion fails on pristine v3.66.506. Custom-runner safe:
zero-arg tests; env + store globals restored in try/finally; tempfile.mkdtemp.
A throwaway in-memory secrets backend is installed for the secret-bearing tests
(the default master_password backend is locked; plaintext is a no-op).
"""
import ast
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))
import spa_population  # noqa: E402  (needs the sys.path insert above)


def _app():
    from flask import Flask
    from bulk_downloader import app_store_raw_editor as SR
    app = Flask(__name__)
    SR.register_routes(app)
    return app


def _client():
    return _app().test_client()


def _vpn_env(tmp):
    from bulk_downloader import vpn_config as VC
    p = Path(tmp) / "tunnels.json"
    os.environ["BD_VPN_CONFIG_PATH"] = str(p)
    VC._loaded = False
    return p, VC


def _widgets_env(tmp):
    from bulk_downloader import widgets_config as WC
    p = Path(tmp) / "widgets.json"
    os.environ["BD_WIDGETS_CONFIG_PATH"] = str(p)
    WC._loaded = False
    return p, WC


class _MemBackend:
    """Unlocked real-storage secrets backend for tests. name != 'plaintext' so
    vpn_config.store_secrets indirects through @cred refs."""
    name = "memory_test"

    def __init__(self):
        self._d = {}

    def set(self, key, password):
        self._d[key] = password

    def get(self, key):
        return self._d.get(key)

    def delete(self, key):
        return self._d.pop(key, None) is not None

    def list_keys(self):
        return list(self._d.keys())

    def is_unlocked(self):
        return True


def _seed_tunnel_with_secret(VC, ss, tunnel_id="tun-aaa"):
    ss.get_backend().set(f"{tunnel_id}:private_key", "SEKRIT")
    VC._state["tunnels"] = [{
        "tunnel_id": tunnel_id, "provider": "wireguard",
        "config": {"private_key": f"@cred:{tunnel_id}:private_key"},
    }]
    VC.save()


# ── GET ──────────────────────────────────────────────────────────────────────
def test_get_widgets_returns_text():
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_WIDGETS_CONFIG_PATH")
    try:
        p, WC = _widgets_env(tmp)
        WC.save()
        r = _client().get("/api/settings/store-raw?store=widgets")
        assert r.status_code == 200, r.status_code
        body = r.get_json()
        assert body["store"] == "widgets"
        parsed = json.loads(body["text"])
        assert "schema_version" in parsed
    finally:
        WC._loaded = False
        os.environ.pop("BD_WIDGETS_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_WIDGETS_CONFIG_PATH", saved)


def test_get_rejects_unknown_store():
    r = _client().get("/api/settings/store-raw?store=evil")
    assert r.status_code == 400


# ── POST round-trip + cache-invalidate ───────────────────────────────────────
def test_post_widgets_roundtrip_and_live_state():
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_WIDGETS_CONFIG_PATH")
    try:
        p, WC = _widgets_env(tmp)
        WC.save()
        cur = json.loads(p.read_text())
        cur["schema_version"] = 7
        r = _client().post("/api/settings/store-raw",
                           json={"store": "widgets", "text": json.dumps(cur)})
        assert r.status_code == 200, (r.status_code, r.get_json())
        g = json.loads(_client().get("/api/settings/store-raw?store=widgets")
                       .get_json()["text"])
        assert g["schema_version"] == 7
        assert WC.load()["schema_version"] == 7
    finally:
        WC._loaded = False
        os.environ.pop("BD_WIDGETS_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_WIDGETS_CONFIG_PATH", saved)


def test_post_malformed_json_400_file_unchanged():
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_WIDGETS_CONFIG_PATH")
    try:
        p, WC = _widgets_env(tmp)
        WC.save()
        before = p.read_bytes()
        r = _client().post("/api/settings/store-raw",
                           json={"store": "widgets", "text": "{ not json ]"})
        assert r.status_code == 400
        assert p.read_bytes() == before
    finally:
        WC._loaded = False
        os.environ.pop("BD_WIDGETS_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_WIDGETS_CONFIG_PATH", saved)


# ── R1: tunnel_id guard ──────────────────────────────────────────────────────
def test_post_vpn_tunnel_id_change_blocked_when_secrets_present():
    from bulk_downloader import secrets_store as ss
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_VPN_CONFIG_PATH")
    prev = ss._backend
    ss._backend = _MemBackend()
    try:
        p, VC = _vpn_env(tmp)
        _seed_tunnel_with_secret(VC, ss, "tun-aaa")
        before = p.read_bytes()
        edited = json.loads(p.read_text())
        edited["tunnels"][0]["tunnel_id"] = "tun-bbb"
        r = _client().post("/api/settings/store-raw",
                           json={"store": "vpn", "text": json.dumps(edited)})
        assert r.status_code == 400, (r.status_code, r.get_json())
        assert p.read_bytes() == before
    finally:
        VC._loaded = False
        ss._backend = prev
        os.environ.pop("BD_VPN_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_VPN_CONFIG_PATH", saved)


# ── R2: dedicated rekey moves secrets, orphans none ──────────────────────────
def test_rekey_tunnel_moves_secrets_no_orphans():
    from bulk_downloader import vpn_config as VC
    from bulk_downloader import secrets_store as ss
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_VPN_CONFIG_PATH")
    prev = ss._backend
    ss._backend = _MemBackend()
    try:
        p, VC = _vpn_env(tmp)
        _seed_tunnel_with_secret(VC, ss, "tun-aaa")
        b = ss.get_backend()
        assert "tun-aaa:private_key" in (b.list_keys() or [])
        moved = VC.rekey_tunnel("tun-aaa", "tun-bbb")
        assert moved is True
        keys = b.list_keys() or []
        assert not any(k.startswith("tun-aaa:") for k in keys), "old secrets orphaned"
        assert "tun-bbb:private_key" in keys, "secret not moved"
        t = VC.get_tunnel_config("tun-bbb")
        assert t is not None
        assert t["config"]["private_key"] == "@cred:tun-bbb:private_key"
        assert VC.get_tunnel_config("tun-aaa") is None
    finally:
        VC._loaded = False
        ss._backend = prev
        os.environ.pop("BD_VPN_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_VPN_CONFIG_PATH", saved)


def test_rekey_endpoint():
    from bulk_downloader import vpn_config as VC
    from bulk_downloader import secrets_store as ss
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_VPN_CONFIG_PATH")
    prev = ss._backend
    ss._backend = _MemBackend()
    try:
        p, VC = _vpn_env(tmp)
        _seed_tunnel_with_secret(VC, ss, "tun-aaa")
        r = _client().post("/api/settings/store-raw/rekey",
                           json={"old_id": "tun-aaa", "new_id": "tun-ccc"})
        assert r.status_code == 200, (r.status_code, r.get_json())
        keys = ss.get_backend().list_keys() or []
        assert any(k.startswith("tun-ccc:") for k in keys)
        assert not any(k.startswith("tun-aaa:") for k in keys)
    finally:
        VC._loaded = False
        ss._backend = prev
        os.environ.pop("BD_VPN_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_VPN_CONFIG_PATH", saved)


# ── _saved_at transience (documents the caveat) ──────────────────────────────
def test_saved_at_is_transient():
    tmp = tempfile.mkdtemp()
    saved = os.environ.get("BD_VPN_CONFIG_PATH")
    try:
        p, VC = _vpn_env(tmp)
        VC.save()
        first = json.loads(p.read_text()).get("_saved_at")
        data = json.loads(p.read_text()); data["_saved_at"] = 1.0
        p.write_text(json.dumps(data))
        VC.save()
        after = json.loads(p.read_text()).get("_saved_at")
        assert after != 1.0, "_saved_at should be re-stamped by save()"
        assert isinstance(after, (int, float))
        assert first is not None
    finally:
        VC._loaded = False
        os.environ.pop("BD_VPN_CONFIG_PATH", None) if saved is None \
            else os.environ.__setitem__("BD_VPN_CONFIG_PATH", saved)


# ── inventory + manifest classification ──────────────────────────────────────
def test_inventory_promotes_schema_version_and_tunnel_id():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in ("vpn.schema_version", "widgets.schema_version", "vpn.tunnel_id"):
        assert items[k]["runtime_tunable"] is True, f"{k} should be runtime-tunable"
        assert items[k]["gui_exposure"] == "full", (k, items[k]["gui_exposure"])
    for k in ("vpn._saved_at", "widgets._saved_at"):
        assert items[k]["runtime_tunable"] is False, f"{k} stays display-only"
        assert items[k]["gui_exposure"] == "display-only", (k, items[k]["gui_exposure"])


def test_manifest_promotes_three_keys():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text()).get("exposed", {})
    for k in ("vpn.schema_version", "widgets.schema_version", "vpn.tunnel_id"):
        assert m.get(k) == "full", (k, m.get(k))


def test_metadata_sets_shrunk_in_source():
    src = (_REPO / "tools" / "config_surface_inventory.py").read_text()
    tree = ast.parse(src)
    sets = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in ("_VPN_METADATA", "_WIDGETS_METADATA"):
                    sets[t.id] = set(ast.literal_eval(node.value))
    assert sets["_VPN_METADATA"] == {"vpn._saved_at"}, sets["_VPN_METADATA"]
    assert sets["_WIDGETS_METADATA"] == {"widgets._saved_at"}, sets["_WIDGETS_METADATA"]


def test_spa_wires_store_raw():
    # POPULATION: PRODUCT-ONLY (row 232). This is a positive-existence claim
    # about the SHIPPED app, so a Vitest spec naming the token must not satisfy
    # it -- that is the v3.66.1217 laundering, where a FIXTURE vouched for 14
    # endpoints no product code called. require_both_halves keeps the narrowing
    # honest: an empty product half would pass this gate vacuously, and an empty
    # excluded half would mean the rule never fires on the real tree.
    src_dir = _REPO / "frontend" / "src"
    _sel, _exc = spa_population.select(src_dir)
    spa_population.require_both_halves(_sel, _exc, "test_spa_wires_store_raw")
    blob = spa_population.product_text(src_dir)
    assert "/api/settings/store-raw" in blob
