"""Phase 4 gap closure, cuts A3 + A4 + A5 (v3.66.324) -- finishes Phase A.

A3 (GAP2): the SPA gains a global AI-assist config form over the already-wired
POST /api/global_config -- ai_enabled / ai_provider / ai_endpoint / ai_model_vision
/ ai_model_text (added to GlobalConfigSubset; controls on the Settings page, which
already owns the global_config draft/save path).

A4 (GAP1): per-site VPN routing keys become editable in the schema-driven
SiteSettings editor -- vpn_enabled / vpn_required / vpn_kill_switch_strict /
vpn_tunnel_id added to CFG_FIELDS (all categorize "general" -> gui-safe). The live
tunnel <select> + per-site badge are deferred UX polish; the write controls (the
deletion blocker) are delivered here.

A5 (LIB-1): library item rating / watched / per-item tag controls wired in
Library.tsx over the existing-but-phantom POST /api/library/<id>/{rating,watched,tags}
routes (full template literals so the parity scanner credits spa_wired).

RED on pristine (keys absent from the editable set / Settings page / GlobalConfigSubset
/ the library ops hook, per-site count at 199/226); GREEN after wiring.
"""
import re
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent


def _read(rel):
    return (_REPO / rel).read_text(encoding="utf-8")


def _gcs_block():
    t = _read("frontend/src/lib/api-types.ts")
    m = re.search(r"interface GlobalConfigSubset\s*\{(.*?)\n\}", t, re.S)
    assert m, "GlobalConfigSubset interface not found"
    return m.group(1)


# ── A3: global AI-assist config form ─────────────────────────────────────────
_AI_KEYS = ("ai_enabled", "ai_provider", "ai_endpoint", "ai_model_vision", "ai_model_text")


def test_a3_settings_page_has_ai_controls():
    s = _read("frontend/src/routes/Settings.tsx")
    for k in _AI_KEYS:
        assert k in s, f"Settings page must control {k}"


def test_a3_global_config_subset_declares_ai_keys():
    b = _gcs_block()
    for k in _AI_KEYS:
        assert k in b, f"GlobalConfigSubset must declare {k}"


# ── A4: per-site VPN routing keys editable ───────────────────────────────────
_VPN_KEYS = ("vpn_enabled", "vpn_required", "vpn_kill_switch_strict", "vpn_tunnel_id")


def test_a4_vpn_keys_in_cfg_fields_and_editable():
    import bulk_downloader.app_settings_center as asc
    cfg = set(asc._cfg_fields())
    ed = asc._editable_field_set()
    for k in _VPN_KEYS:
        assert k in cfg, f"{k} must be in CFG_FIELDS"
        assert k in ed, f"{k} must be a gui-safe editable field"


def test_a4_field_counts():
    import bulk_downloader.app_settings_center as asc
    # +4 VPN keys on top of A2's 199 / 226
    # >= floor (not ==): the absolute editable/unique counts move on every later
    # additive cut; the canonical exact pins live in test_settings_center_slice5
    # (_EDITABLE_COUNT) + test_settings_center_wiring (unique_fields), updated per
    # cut. This per-cut test asserts the FEATURE keys raised the count to at least
    # what this cut delivered, so a later tranche cannot whack-a-mole re-break it.
    assert len(asc._editable_field_set()) >= 203, len(asc._editable_field_set())
    assert asc._schema()["unique_fields"] >= 230, asc._schema()["unique_fields"]


# ── A5: library item rating / watched / tags wired ───────────────────────────
def test_a5_library_ops_hook_wires_item_endpoints():
    h = _read("frontend/src/hooks/useLibraryOps.ts")
    # full template literals so the parity scanner credits spa_wired
    assert "/api/library/" in h
    assert "/rating" in h, "rating mutation missing"
    assert "/watched" in h, "watched mutation missing"
    assert "/tags" in h, "tag mutation missing"


def test_a5_library_card_renders_item_controls():
    lib = _read("frontend/src/routes/Library.tsx")
    assert "rating" in lib, "library card must expose rating"
    assert "watched" in lib, "library card must expose watched"


def test_a5_library_item_type_declares_meta_fields():
    t = _read("frontend/src/lib/api-types.ts")
    m = re.search(r"interface LibraryItem\s*\{(.*?)\n\}", t, re.S)
    assert m, "LibraryItem interface not found"
    body = m.group(1)
    assert "rating" in body and "watched" in body, "LibraryItem must declare rating/watched"
