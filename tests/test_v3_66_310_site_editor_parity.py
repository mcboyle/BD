"""v3.66.310 — CLI->GUI parity Phase 4.1: all 51 per-site keys promoted full.

Single cut. The per-site write path (PUT /api/sites/<sid> = app.api_update) already
validates + range-backstops + preserve-on-blanks secrets + audits, so the WRITE side
is complete. This cut delivers the read/descriptor + SPA editor side:

  - The editable endpoint gains a SEPARATE `gated_meta` / `gated_groups` block carrying
    descriptors for the 23 gated site_keys (secrets / login / selector / relogin).
    Secrets stay PRESENCE-ONLY (`current: {present: bool}`) — never a value. The existing
    gui-safe `field_meta` surface is UNCHANGED (slice4's F2 invariant: secrets never in
    field_meta, no value leak — preserved here).
  - All 51 site_keys are ledgered full in reports/config_gui_manifest.json; the parity
    ratchet baseline drops 141 -> 90.
  - The SPA gains a schema-driven site settings editor that fetches the editable endpoint
    and writes via the full /api/sites/<sid> literal.

RED-first: every assertion fails on pristine v3.66.309. Custom runner; zero-arg tests;
restore env in try/finally.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

from flask import Flask  # noqa: E402
from bulk_downloader import app_settings_center as sc  # noqa: E402

_SECRET_VALUE = "SUPERSECRETVALUE"
_KEY_VALUE = "STASHKEY12345"
_CFG = {"sites": {"demo": {
    "name": "Demo", "max_concurrent": 4, "wait": 5,
    "username": "alice", "login_url": "https://example.test/login",
    "password": _SECRET_VALUE, "stash_api_key": _KEY_VALUE,
    "dl_selector": "a.download", "trigger_selector": "#go",
}}}


def _app():
    app = Flask(__name__)
    sc.register_routes(app)
    return app


def _stage(cfg):
    d = tempfile.mkdtemp()
    p = Path(d) / "sites_config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    old = os.environ.get("BD_SITES_CONFIG_PATH")
    os.environ["BD_SITES_CONFIG_PATH"] = str(p)
    return old


def _unstage(old):
    if old is None:
        os.environ.pop("BD_SITES_CONFIG_PATH", None)
    else:
        os.environ["BD_SITES_CONFIG_PATH"] = old


def _site_keys():
    """The authoritative parity-tracked per-site key set (== inventory site_keys)."""
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    return {it["key"] for it in d["items"] if it["kind"] == "site_key"}


# ── endpoint: gated_meta covers every gated site_key; field_meta unchanged ───

# v3.66.710: `accounts` is a NESTED per-account credential list. It is withheld from
# the editor descriptor ON PURPOSE (app_settings_center._STRUCTURED_CREDENTIAL_FIELDS)
# so credentials never go on the wire in a field editor.
#
# It used to satisfy these invariants only because it was INVISIBLE: the inventory read
# CFG_FIELDS with a non-greedy regex that truncated at the first nested ']', so 178 of
# 235 per-site keys -- accounts among them -- were never in the universe being asserted
# over. The truncation was load-bearing by accident. Now that the denominator is real,
# the exclusion has to be STATED.
_INTENTIONALLY_NOT_EXPOSED = {"accounts"}


def test_editable_emits_gated_meta_for_all_site_keys():
    old = _stage(_CFG)
    try:
        b = json.loads(_app().test_client().get("/api/settings/site/demo/editable").data)
        assert "gated_meta" in b, "endpoint must emit a gated_meta block"
        assert "gated_groups" in b, "endpoint must emit gated_groups"
        covered = set(b.get("field_meta", {})) | set(b.get("gated_meta", {}))
        missing = _site_keys() - covered - _INTENTIONALLY_NOT_EXPOSED
        assert not missing, f"site_keys with no descriptor: {sorted(missing)}"
    finally:
        _unstage(old)


def test_gated_secrets_presence_only_no_value_leak():
    old = _stage(_CFG)
    try:
        raw = _app().test_client().get("/api/settings/site/demo/editable").data
        b = json.loads(raw)
        gm = b["gated_meta"]
        for s in ("password", "stash_api_key"):
            assert s in gm, f"{s} missing from gated_meta"
            assert gm[s]["secret"] is True, s
            cur = gm[s]["current"]
            assert isinstance(cur, dict) and cur.get("present") is True, (s, cur)
        # no secret value anywhere in the serialized response
        assert _SECRET_VALUE not in raw.decode("utf-8")
        assert _KEY_VALUE not in raw.decode("utf-8")
    finally:
        _unstage(old)


def test_field_meta_still_gui_safe_only_regression():
    """slice4 invariant must hold: secrets never appear in field_meta."""
    old = _stage(_CFG)
    try:
        b = json.loads(_app().test_client().get("/api/settings/site/demo/editable").data)
        for s in ("password", "stash_api_key", "auth_token", "captcha_api_key"):
            assert s not in b.get("field_meta", {}), s
        for d in b.get("field_meta", {}).values():
            assert d["secret"] is False
    finally:
        _unstage(old)


# ── inventory: every site_key is gui_exposure full ───────────────────────────
def test_inventory_all_site_keys_full():
    import config_surface_inventory as P2
    d = P2.build(str(_REPO))
    sk = [it for it in d["items"] if it["kind"] == "site_key"]
    assert sk, "expected site_keys"
    not_full = sorted(it["key"] for it in sk
                      if it["gui_exposure"] != "full"
                      and it["key"] not in _INTENTIONALLY_NOT_EXPOSED)
    assert not not_full, f"site_keys not full: {not_full}"


# ── ratchet baseline dropped to <= 90; no site_key remains open ──────────────
def test_ratchet_baseline_dropped_to_90():
    base = json.loads((_REPO / "reports/config_parity_baseline.json").read_text())
    assert base["open_count"] <= 90, base["open_count"]   # ceiling, not equality
    sk = _site_keys()
    still_open = sk & set(base.get("open", []))
    assert not still_open, f"site_keys still in open: {sorted(still_open)}"


# ── manifest ledgers every site_key full ─────────────────────────────────────
def test_manifest_ledgers_all_site_keys():
    m = json.loads((_REPO / "reports/config_gui_manifest.json").read_text())
    exposed = m.get("exposed", m)
    missing = sorted(k for k in _site_keys()
                     if exposed.get(k) != "full"
                     and k not in _INTENTIONALLY_NOT_EXPOSED)
    assert not missing, f"site_keys not ledgered full: {missing}"


# ── SPA: a schema-driven site editor fetches the endpoint + writes via PUT ───
def test_spa_site_editor_wires_endpoints():
    src_dir = _REPO / "frontend" / "src"
    blob = ""
    for p in src_dir.rglob("*.ts*"):
        try:
            blob += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    assert "/api/settings/site/" in blob, "no SPA consumer of the editable endpoint"
    assert "/editable" in blob, "SPA does not fetch the editable descriptor surface"
    # the write must use the full /api/sites/<id> literal (parity scanner credits literals)
    assert "/api/sites/" in blob


# ── version: this feature landed at >= 3.66.310 (NOT a live pin -- the canonical
#    live-version pin is test_settings_center_slice4; this stays stable across cuts) ──
def test_site_editor_landed_at_or_after_310():
    from bulk_downloader import __version__
    parts = tuple(int(x) for x in __version__.split(".")[:3])
    assert parts >= (3, 66, 310), __version__
