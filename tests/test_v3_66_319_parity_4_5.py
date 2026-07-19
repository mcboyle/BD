"""v3.66.319 — CLI->GUI parity Phase 4.5: vpn_config (classification only, non-guard).

CLASSIFICATION, not new write paths. The VPN config is already fully GUI-managed
via Vpn.tsx + the tunnel-CRUD API (/api/vpn/tunnels GET/POST/PUT/DELETE/start/stop)
and the global-settings route (get/update_global_settings) — and it is already
SECRET-SAFE: tunnel.to_dict(redact_secrets=True) redacts on read, store_secrets()
indirects secret fields through @cred: on write, and Vpn.tsx renders secret-named
config keys as write-only password inputs (values arrive as "***"). That matches
the §9 secret discipline, so exposing these risks no secret.

FULL (22): tunnel fields (name/location/provider/backend/endpoint/address/dns/
extra/config + the 4 secret fields account_number/peer_public_key/plaintext/
private_key), the collection/toggle (enabled/tunnels), and the bounded global
settings (global_settings/enable_per_site_tunnels/kill_switch_auto_recover/
leak_test_interval_s/max_concurrent_tunnels/system_killswitch_allow_ports/
system_killswitch_default).

DISPLAY-ONLY (3): vpn._saved_at, vpn.schema_version (store-written metadata) and
vpn.tunnel_id (system-assigned on create, not an operator-set knob) — the
widgets._saved_at precedent.

NO runtime/capture file touched — manifest + baseline + scanner metadata set + test.
RED-first: on pristine the 25 are open (gui_exposure=partial). Zero-arg.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "tools"))

_FULL = (
    "vpn.name", "vpn.location", "vpn.provider", "vpn.backend", "vpn.endpoint",
    "vpn.address", "vpn.dns", "vpn.extra", "vpn.config",
    "vpn.account_number", "vpn.peer_public_key", "vpn.plaintext", "vpn.private_key",
    "vpn.enabled", "vpn.tunnels",
    "vpn.global_settings", "vpn.enable_per_site_tunnels", "vpn.kill_switch_auto_recover",
    "vpn.leak_test_interval_s", "vpn.max_concurrent_tunnels",
    "vpn.system_killswitch_allow_ports", "vpn.system_killswitch_default",
)
# v3.66.507 (Bucket 3b): vpn.schema_version + vpn.tunnel_id were promoted from
# display-only to full via the raw store-metadata editor (the rekey action makes
# tunnel_id safely editable). Only vpn._saved_at stays display-only (auto-stamped
# by save() -> a manual edit is transient).
_DISPLAY = ("vpn._saved_at",)
_PROMOTED_FULL_507 = ("vpn.schema_version", "vpn.tunnel_id")


def test_vpn_fields_full():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in _FULL:
        assert items[k]["gui_exposure"] == "full", f"{k} not full"


def test_vpn_meta_display_only():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    for k in _DISPLAY:
        assert items[k]["runtime_tunable"] is False, f"{k} should be non-runtime-tunable"
        assert items[k]["gui_exposure"] == "display-only", k
    # v3.66.507: the two promoted metadata keys are now full + runtime-tunable.
    for k in _PROMOTED_FULL_507:
        assert items[k]["runtime_tunable"] is True, f"{k} should be runtime-tunable @507"
        assert items[k]["gui_exposure"] == "full", k


def test_no_open_vpn_remains():
    import config_surface_inventory as csi
    d = csi.build(str(_REPO))
    items = {it["key"]: it for it in d["items"]}
    openset = set(csi._open_settings(d["items"]))
    vpn_open = [k for k in openset if items[k].get("kind") == "vpn_config"]
    assert vpn_open == [], f"open vpn keys remain: {vpn_open}"


def test_vpn_secret_safe_backing():
    """The 'full' on secret fields is honest only if the read path redacts."""
    vpn = (_REPO / "bulk_downloader" / "vpn.py").read_text(encoding="utf-8")
    assert "redact_secrets: bool = True" in vpn          # to_dict redacts by default
    vc = (_REPO / "bulk_downloader" / "vpn_config.py").read_text(encoding="utf-8")
    assert "def store_secrets" in vc                      # write indirects via @cred:
    # the editing GUI + tunnel CRUD exist
    assert (_REPO / "frontend" / "src" / "routes" / "Vpn.tsx").exists()
    api = (_REPO / "bulk_downloader" / "app_vpn_api.py").read_text(encoding="utf-8")
    assert "/api/vpn/tunnels" in api and "update_global_settings" in api
