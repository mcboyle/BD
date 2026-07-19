"""v3.66.336 — consolidated cut: four carry-forward items.

Each assertion below is RED on pristine v3.66.335 source and GREEN after this cut.
Where a behavioral seam exists it is used (the global_config schema is imported;
the gui_parity inventory is built against the LIVE tree like test_parity_method_aware
/ test_v3_66_328); GUI-control presence is proven by source reference to the exact
literals the scanner / operator depend on (house style — see test_v3_66_328_spa_add_url).

  1. SPA "Reset pool slot" composed account_pool/reset/reset/<idx> (doubled verb ->
     404). Fixed by lib/poolPath.actionSuffixWithIdx, which appends ONLY the index.
  2. struct_embed tie-breaker (v3.66.321) gets an operator switch: global_config
     player_struct_tiebreak (opt-in, default OFF), read by the WACZ->template build
     and passed as detect(..., struct_tiebreak=...). Default OFF => byte-identical.
  3. ai_api_key gains a masked SecretField in the global AI-assist form (backend
     already masks on GET + preserves on the "<configured>" sentinel).
  4. vpn_tunnel_id renders as a <select> sourced from GET /api/vpn/tunnels (was
     free-text); the LIST GET flips spa_wired:false -> true.

Sandbox: custom runner (no pytest fixtures); zero-arg tests.
"""
from pathlib import Path

import tools.gui_parity_inventory as g

_REPO = Path(__file__).resolve().parent.parent
_FE = _REPO / "frontend" / "src"


def _read(rel):
    return (_REPO / rel).read_text(encoding="utf-8")


def _wired(items, ce):
    for it in items:
        if it.get("command_or_endpoint") == ce:
            return it.get("spa_wired")
    raise AssertionError("inventory item not found: " + ce)


# ─────────────────────────── item 1 ───────────────────────────
def test_item1_poolpath_helper_appends_only_the_index():
    """The helper appends ONLY the index — never the action verb again."""
    src = (_FE / "lib" / "poolPath.ts").read_text(encoding="utf-8")
    # the corrected single-segment append
    assert "${suffix}/${encodeURIComponent(idx)}" in src
    # the doubled-verb append must not be reintroduced here
    assert "/reset/${encodeURIComponent(idx)}" not in src


def test_item1_siteactions_uses_helper_not_doubled_append():
    """SiteActions no longer hand-builds the doubled `${suffix}/reset/<idx>` path;
    it routes through the helper."""
    src = (_FE / "routes" / "SiteActions.tsx").read_text(encoding="utf-8")
    assert "actionSuffixWithIdx" in src
    assert "${suffix}/reset/${encodeURIComponent(idx)}" not in src


# ─────────────────────────── item 2 ───────────────────────────
def test_item2_schema_has_struct_tiebreak_toggle_off_by_default():
    """player_struct_tiebreak is a bool global-config key defaulting OFF."""
    import importlib
    import bulk_downloader.global_config as gc
    importlib.reload(gc)
    spec = gc.GLOBAL_CONFIG_SCHEMA.get("player_struct_tiebreak")
    assert spec is not None, "player_struct_tiebreak missing from schema"
    assert spec.get("type") is bool
    assert spec.get("safe_default") is False
    assert spec.get("safety") is False


def test_item2_build_reads_flag_and_passes_struct_tiebreak_to_detect():
    """The WACZ->template build reads the operator flag and threads it into
    player_recognition.detect()."""
    src = _read("tools/build_template_from_wacz.py")
    assert "player_struct_tiebreak" in src        # reads the operator flag
    assert "struct_tiebreak=" in src               # passes it into detect()


def test_item2_settings_renders_struct_tiebreak_toggle():
    src = (_FE / "routes" / "Settings.tsx").read_text(encoding="utf-8")
    assert "player_struct_tiebreak" in src
    assert 'setField("player_struct_tiebreak"' in src


# ─────────────────────────── item 3 ───────────────────────────
def test_item3_settings_renders_masked_ai_api_key_control():
    """The AI-assist form exposes ai_api_key as a write-only masked field using
    the "<configured>" mask sentinel (mirrors the auth_token control)."""
    src = (_FE / "routes" / "Settings.tsx").read_text(encoding="utf-8")
    assert 'setField("ai_api_key"' in src
    assert 'draft.ai_api_key === "<configured>"' in src
    # The masking is now provided by the shared SecretField component (which
    # renders type="password" + autocomplete="new-password" internally); the
    # raw type="password" literal moved out of Settings.tsx in the v3.66.360
    # SecretField sweep. The write-only masked behavior is unchanged.
    assert "<SecretField" in src


def test_item3_ai_api_key_typed_in_global_config_subset():
    src = (_FE / "lib" / "api-types.ts").read_text(encoding="utf-8")
    assert "ai_api_key?: string;" in src


# ─────────────────────────── item 4 ───────────────────────────
def test_item4_vpn_tunnels_list_get_is_spa_wired():
    """Live inventory: GET /api/vpn/tunnels is now SPA-wired (the per-site editor
    populates the vpn_tunnel_id <select> from it)."""
    inv = g.build(str(_REPO))
    assert _wired(inv["items"], "GET /api/vpn/tunnels") is True
    # v3.66.769 (6B): the per-tunnel GET is now genuinely wired too -- the VPN
    # Leak tests card GETs single-tunnel detail via apiGet(`/api/vpn/tunnels/${id}`)
    # alongside the leak results. It is no longer a method-blind phantom.
    assert _wired(inv["items"], "GET /api/vpn/tunnels/<tunnel_id>") is True


def test_item4_sitesettings_renders_tunnel_select_from_full_literal():
    """vpn_tunnel_id renders a <select>, and the dropdown is fed by the FULL
    /api/vpn/tunnels literal (so the parity scanner credits the GET)."""
    src = (_FE / "routes" / "SiteSettings.tsx").read_text(encoding="utf-8")
    assert '"/api/vpn/tunnels"' in src
    assert 'd.key === "vpn_tunnel_id"' in src
    assert "<select" in src
